from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import engine
from app.models.ceri_tables import CeriSecDocumentExtraction, CeriSourceRecord
from app.services.ceri.config import load_ceri_config
from app.services.ceri.enums import CeriDataset
from app.services.ceri.orchestration import CeriIngestionRequest, CeriIngestionService
from app.services.ceri.provider_registry import CeriProviderRegistry
from app.services.ceri.sec.client import SecClientConfig, SecEdgarClient
from app.services.ceri.sec.processor_signature import sec_guidance_processor_signature
from app.services.ceri.sec.provider import SecCeriProvider
from app.services.ceri.source_record_service import source_record_content_hash
from app.settings import SecDocumentIncrementalMode, Settings, get_settings

TICKERS = ("AIZ", "AMZN", "CLBT", "JPM", "SLDE")


class CertificationProvider(SecCeriProvider):
    def __init__(self, *, client: SecEdgarClient, tickers: tuple[str, ...]) -> None:
        super().__init__(client=client)
        self.output_fingerprints: dict[str, list[str]] = {ticker: [] for ticker in tickers}
        self.parse_calls = 0

    def extract_guidance_document(self, document, *, text=None):
        self.parse_calls += 1
        records = super().extract_guidance_document(document, text=text)
        self.output_fingerprints[document.ticker].extend(
            f"{record.provider_record_id}:{source_record_content_hash(record.payload)}"
            for record in records
        )
        return records


@dataclass
class ScenarioResult:
    name: str
    mode: str
    elapsed_seconds: float
    sec_requests: int
    submissions_requests: int
    filing_downloads: int
    bytes_downloaded: int
    parsing_calls: int
    guidance_records: int
    inserted_records: int
    deduplicated_records: int
    documents_discovered: int
    documents_downloaded: int
    documents_skipped: int
    documents_would_skip: int
    output_fingerprint: str
    per_ticker: dict[str, dict]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument(
        "--tickers",
        nargs="+",
        default=list(TICKERS),
        help="Explicit SEC-only ticker universe; no downstream CERI stages are scheduled.",
    )
    parser.add_argument(
        "--scenarios",
        nargs="+",
        choices=("shadow_first", "shadow_repeat", "active_warm"),
        default=("shadow_first", "shadow_repeat", "active_warm"),
        help="Use shadow_first alone for a one-pass production-universe bootstrap.",
    )
    parser.add_argument(
        "--requests-per-second",
        type=float,
        default=None,
        help="Process-local SEC pacing override for partitioned bootstrap workers.",
    )
    args = parser.parse_args()
    tickers = tuple(sorted({str(value).strip().upper() for value in args.tickers if value}))
    if not tickers:
        parser.error("at least one ticker is required")
    runtime = get_settings()
    requests_per_second = args.requests_per_second or runtime.sec_requests_per_second
    if requests_per_second <= 0 or requests_per_second > 10:
        parser.error("requests per second must be greater than zero and at most 10")
    config = load_ceri_config()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    scenarios: list[ScenarioResult] = []
    with Session(engine) as db:
        source_count_before = int(db.scalar(select(func.count(CeriSourceRecord.id))) or 0)
    modes = {
        "shadow_first": SecDocumentIncrementalMode.SHADOW,
        "shadow_repeat": SecDocumentIncrementalMode.SHADOW,
        "active_warm": SecDocumentIncrementalMode.ACTIVE,
    }
    for name in args.scenarios:
        result = run_scenario(
            name=name,
            mode=modes[name],
            runtime=runtime,
            config=config,
            stamp=stamp,
            tickers=tickers,
            requests_per_second=requests_per_second,
        )
        scenarios.append(result)
        print(json.dumps(asdict(result), sort_keys=True), flush=True)
    with Session(engine) as db:
        source_count_after = int(db.scalar(select(func.count(CeriSourceRecord.id))) or 0)
        extraction_counts = {
            str(status): int(count)
            for status, count in db.execute(
                select(CeriSecDocumentExtraction.status, func.count())
                .group_by(CeriSecDocumentExtraction.status)
                .order_by(CeriSecDocumentExtraction.status)
            )
        }
    checks = _certification_checks(scenarios)
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "tickers": list(tickers),
        "processor_signature": sec_guidance_processor_signature(),
        "runtime": {
            "sec_requests_per_second": runtime.sec_requests_per_second,
            "certification_requests_per_second": requests_per_second,
            "sec_http_timeout_seconds": runtime.sec_http_timeout_seconds,
            "sec_form4_enabled": runtime.sec_form4_enabled,
            "default_incremental_mode": runtime.sec_document_incremental_mode.value,
        },
        "source_record_count_before": source_count_before,
        "source_record_count_after": source_count_after,
        "extraction_status_counts": extraction_counts,
        "scenarios": [asdict(item) for item in scenarios],
        "checks": checks,
        "passed": all(checks.values()),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / f"sec_incremental_recertification_{stamp}.json"
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_path = args.output_dir / f"sec_incremental_recertification_{stamp}.md"
    markdown_path.write_text(_markdown_report(report), encoding="utf-8")
    print(f"certification_output={output_path.resolve()}", flush=True)
    print(f"certification_markdown={markdown_path.resolve()}", flush=True)
    print(f"certification_passed={report['passed']}", flush=True)
    return 0 if report["passed"] else 1


def run_scenario(
    *, name, mode, runtime, config, stamp, tickers, requests_per_second
) -> ScenarioResult:
    settings = Settings(
        sec_document_incremental_mode=mode,
        sec_document_lease_seconds=runtime.sec_document_lease_seconds,
        sec_document_retry_base_seconds=runtime.sec_document_retry_base_seconds,
    )
    client = SecEdgarClient(
        SecClientConfig(
            user_agent=runtime.sec_user_agent,
            requests_per_second=requests_per_second,
            timeout_seconds=runtime.sec_http_timeout_seconds,
        )
    )
    provider = CertificationProvider(client=client, tickers=tickers)
    registry = CeriProviderRegistry(providers={"sec": provider}, config=config)
    service = CeriIngestionService(config=config, registry=registry, settings=settings)
    per_ticker = {}
    started = time.perf_counter()
    with Session(engine) as db:
        for ticker in tickers:
            ticker_started = time.perf_counter()
            result = service.ingest(
                db,
                CeriIngestionRequest(
                    provider="sec",
                    dataset=CeriDataset.GUIDANCE,
                    ticker=ticker,
                    request_key=f"sec-certification:{stamp}:{name}:{ticker}",
                    scope={"ticker": ticker, "certification": name},
                ),
            )
            db.commit()
            per_ticker[ticker] = {
                **result.as_dict(),
                "elapsed_seconds": round(time.perf_counter() - ticker_started, 6),
            }
            print(
                f"{name} {ticker} status={result.status} "
                f"downloads={result.documents_downloaded} "
                f"skipped={result.documents_skipped} "
                f"would_skip={result.documents_would_skip}",
                flush=True,
            )
    stats = client.stats()
    fingerprints = sorted(
        value for values in provider.output_fingerprints.values() for value in values
    )
    return ScenarioResult(
        name=name,
        mode=mode.value,
        elapsed_seconds=round(time.perf_counter() - started, 6),
        sec_requests=stats.requests,
        submissions_requests=stats.submissions_requests,
        filing_downloads=stats.filing_document_requests,
        bytes_downloaded=stats.bytes_downloaded,
        parsing_calls=provider.parse_calls,
        guidance_records=sum(item["fetched"] for item in per_ticker.values()),
        inserted_records=sum(item["inserted"] for item in per_ticker.values()),
        deduplicated_records=sum(item["deduplicated"] for item in per_ticker.values()),
        documents_discovered=sum(item["documents_discovered"] for item in per_ticker.values()),
        documents_downloaded=sum(item["documents_downloaded"] for item in per_ticker.values()),
        documents_skipped=sum(item["documents_skipped"] for item in per_ticker.values()),
        documents_would_skip=sum(item["documents_would_skip"] for item in per_ticker.values()),
        output_fingerprint=hashlib.sha256("\n".join(fingerprints).encode()).hexdigest(),
        per_ticker=per_ticker,
    )


def _markdown_report(report: dict) -> str:
    scenarios = {item["name"]: item for item in report["scenarios"]}
    lines = [
        "# SEC Incremental Re-Certification",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Processor signature: `{report['processor_signature']}`",
        f"Tickers: `{', '.join(report['tickers'])}`",
        "",
        "| Scenario | Mode | Discovered | Filing downloads | Skipped | "
        "Parsing calls | SEC requests | Bytes | Elapsed (s) |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("shadow_first", "shadow_repeat", "active_warm"):
        item = scenarios.get(name)
        if item is None:
            continue
        lines.append(
            f"| {name} | {item['mode']} | {item['documents_discovered']} | "
            f"{item['filing_downloads']} | {item['documents_skipped']} | "
            f"{item['parsing_calls']} | {item['sec_requests']} | "
            f"{item['bytes_downloaded']} | {item['elapsed_seconds']} |"
        )
    lines.extend(
        [
            "",
            "## Checks",
            "",
            *[
                f"- [{'x' if passed else ' '}] {name}"
                for name, passed in report["checks"].items()
            ],
            "",
            f"Overall: **{'PASS' if report['passed'] else 'FAIL'}**",
            "",
        ]
    )
    return "\n".join(lines)


def _certification_checks(scenarios: list[ScenarioResult]) -> dict[str, bool]:
    by_name = {item.name: item for item in scenarios}
    checks = {
        f"{item.name}_all_tickers_ready": all(
            values.get("run_evidence_status") == "READY"
            and values.get("status") == "COMPLETED"
            for values in item.per_ticker.values()
        )
        for item in scenarios
    }
    first = by_name.get("shadow_first")
    repeat = by_name.get("shadow_repeat")
    active = by_name.get("active_warm")
    if first is not None:
        checks["shadow_downloaded_all_discovered"] = (
            first.documents_downloaded == first.documents_discovered
        )
    if first is not None and repeat is not None:
        checks.update(
            shadow_output_parity=first.output_fingerprint == repeat.output_fingerprint,
            shadow_record_count_parity=first.guidance_records == repeat.guidance_records,
            repeat_would_skip_all_discovered=(
                repeat.documents_would_skip == repeat.documents_discovered
            ),
        )
    if active is not None:
        checks.update(
            active_zero_filing_downloads=active.filing_downloads == 0,
            active_zero_parsing=active.parsing_calls == 0,
            active_zero_extraction_records=active.guidance_records == 0,
            active_skipped_all_discovered=(
                active.documents_skipped == active.documents_discovered
            ),
        )
    if first is not None and active is not None:
        checks.update(
            repeated_bytes_reduction_gt_95pct=(
                active.bytes_downloaded < first.bytes_downloaded * 0.05
            ),
            repeated_elapsed_reduction_gt_80pct=(
                active.elapsed_seconds < first.elapsed_seconds * 0.20
            ),
        )
    return checks


if __name__ == "__main__":
    raise SystemExit(main())
