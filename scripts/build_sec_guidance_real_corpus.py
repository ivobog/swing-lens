from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from app.services.ceri.sec.client import SecClientConfig, SecEdgarClient
from app.services.ceri.sec.guidance_extractor import GuidanceExtractionService, _visible_text
from app.services.ceri.sec.provider import GUIDANCE_FORMS

_CANDIDATE_TERMS = re.compile(
    r"\b(?:guidance|outlook|expects?|forecast|anticipates?|projects?|targets?|"
    r"raise[ds]?|lower(?:ed|s)?|reaffirm(?:ed|s)?|maintain(?:ed|s)?|withdraw(?:n|s|al)?|"
    r"actual results may differ|undue reliance|forward-looking statements?)\b",
    re.IGNORECASE,
)
_HTML_SUFFIXES = {".htm", ".html", ".txt"}
_EXHIBIT_HINT = re.compile(
    r"(?:^|[-_])(?:ex|exhibit)[-_]?99|99[-_.]?\d|earnings?|results?|release|"
    r"commentary|presentation",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FilingMetadata:
    company: str
    ticker: str
    cik: str
    accession: str
    filing_date: str
    form: str
    primary_document: str
    filing_items: str


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value)


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _filings_for_ticker(
    client: SecEdgarClient,
    *,
    ticker: str,
    cik: str,
    company: str,
    accession: str | None,
    forms: set[str],
    start_date: date | None,
    end_date: date | None,
    earnings_only: bool,
) -> list[FilingMetadata]:
    submissions = client.submissions(cik)
    recent = submissions.get("filings", {}).get("recent", {})
    values = zip(
        recent.get("accessionNumber", []),
        recent.get("filingDate", []),
        recent.get("form", []),
        recent.get("primaryDocument", []),
        recent.get("items", []),
        strict=False,
    )
    selected: list[FilingMetadata] = []
    for filing_accession, filing_date, form, document, filing_items in values:
        filed = _date(str(filing_date)[:10])
        if accession and str(filing_accession) != accession:
            continue
        if forms and str(form).upper() not in forms:
            continue
        if start_date and filed and filed < start_date:
            continue
        if end_date and filed and filed > end_date:
            continue
        if earnings_only and str(form).upper() == "8-K" and "2.02" not in str(filing_items):
            continue
        selected.append(
            FilingMetadata(
                company=company,
                ticker=ticker.upper(),
                cik=cik.zfill(10),
                accession=str(filing_accession),
                filing_date=str(filing_date)[:10],
                form=str(form),
                primary_document=str(document),
                filing_items=str(filing_items),
            )
        )
    if accession and not selected:
        raise SystemExit(
            f"Accession {accession} was not present in recent submissions for {ticker}."
        )
    return selected


def _archive_url(client: SecEdgarClient, filing: FilingMetadata, document: str) -> str:
    cik = filing.cik.lstrip("0") or "0"
    accession = filing.accession.replace("-", "")
    return f"{client.config.archive_url.rstrip('/')}/{cik}/{accession}/{document}"


def _filing_documents(
    client: SecEdgarClient,
    filing: FilingMetadata,
    *,
    include_exhibits: bool,
    requested_document: str | None,
) -> list[str]:
    if requested_document:
        return [requested_document]
    selected = [filing.primary_document]
    if not include_exhibits:
        return selected
    index_url = _archive_url(client, filing, "index.json")
    index = client.get_json_absolute(index_url)
    items = index.get("directory", {}).get("item", [])
    names = [str(item.get("name", "")) for item in items if isinstance(item, dict)]
    likely_exhibits = [
        name
        for name in names
        if Path(name).suffix.lower() in _HTML_SUFFIXES
        and _EXHIBIT_HINT.search(name)
        and name != filing.primary_document
    ]
    return list(dict.fromkeys([*selected, *likely_exhibits]))


def _candidate_records(
    *,
    filing: FilingMetadata,
    document: str,
    source_url: str,
    raw_path: Path,
    source_text: str,
    max_candidates: int,
) -> list[dict[str, Any]]:
    source_sha = _sha256_text(source_text)
    visible = _visible_text(source_text)
    paragraphs = re.split(r"\n\s*\n", visible)
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    extractor = GuidanceExtractionService()
    for index, paragraph in enumerate(paragraphs, start=1):
        passage = paragraph.strip()
        if len(passage) < 20 or not _CANDIDATE_TERMS.search(passage):
            continue
        passage_hash = _sha256_text(passage)
        if passage_hash in seen:
            continue
        seen.add(passage_hash)
        locator = f"paragraph-{index}"
        current_rows = [
            {
                "metric": row.metric,
                "period_label": row.period_label,
                "low_value": str(row.low_value) if row.low_value is not None else None,
                "high_value": str(row.high_value) if row.high_value is not None else None,
                "point_value": str(row.point_value) if row.point_value is not None else None,
                "unit": row.unit,
                "currency": row.currency,
                "management_claim": row.management_claim,
                "confidence": row.confidence,
            }
            for row in extractor.extract(
                passage,
                locator=f"{filing.accession}/{document}/{locator}",
            )
        ]
        candidates.append(
            {
                "case_id": (
                    f"{filing.ticker}-{filing.filing_date}-{filing.form}-"
                    f"{filing.accession}-{_safe_name(document)}-p{index}"
                ),
                "company": filing.company,
                "ticker": filing.ticker,
                "cik": filing.cik,
                "accession": filing.accession,
                "filing_date": filing.filing_date,
                "form": filing.form,
                "document_name": document,
                "source_url": source_url,
                "source_document_sha256": source_sha,
                "source_document_path": raw_path.as_posix(),
                "visible_text_locator": locator,
                "passage_sha256": passage_hash,
                "passage_text": passage,
                "label": "REVIEW_REQUIRED",
                "review_state": "UNRESOLVED",
                "expected": None,
                "annotation_answers": None,
                "notes": "Candidate only; gold label must be assigned independently.",
                "candidate_extractor_output_non_gold": current_rows,
            }
        )
        if len(candidates) >= max_candidates:
            break
    return candidates


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def build(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir).resolve()
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    config = SecClientConfig(
        user_agent=os.getenv(
            "SEC_USER_AGENT", "SwingLens corpus certification operator@example.invalid"
        ),
        requests_per_second=float(os.getenv("SEC_REQUESTS_PER_SECOND", "2")),
        timeout_seconds=int(os.getenv("SEC_HTTP_TIMEOUT_SECONDS", "30")),
    )
    client = SecEdgarClient(config=config)
    ticker_rows = client.company_tickers()
    lookup = {
        str(row.get("ticker", "")).upper(): row
        for row in ticker_rows.values()
        if isinstance(row, dict) and row.get("ticker")
    }
    tickers = [item.strip().upper() for value in args.ticker for item in value.split(",")]
    forms = {
        item.strip().upper() for value in args.form for item in value.split(",") if item.strip()
    }
    if not forms:
        forms = set(GUIDANCE_FORMS)
    all_candidates: list[dict[str, Any]] = []
    filings_manifest: list[dict[str, Any]] = []
    for ticker in tickers:
        row = lookup.get(ticker)
        if not row:
            raise SystemExit(f"Ticker not found in SEC company_tickers.json: {ticker}")
        cik = str(row["cik_str"]).split(".")[0].zfill(10)
        company = str(row.get("title") or ticker)
        filings = _filings_for_ticker(
            client,
            ticker=ticker,
            cik=cik,
            company=company,
            accession=args.accession,
            forms=forms,
            start_date=_date(args.start_date),
            end_date=_date(args.end_date),
            earnings_only=args.earnings_only,
        )
        if args.max_filings_per_ticker:
            filings = filings[: args.max_filings_per_ticker]
        for filing in filings:
            documents = _filing_documents(
                client,
                filing,
                include_exhibits=args.include_exhibits,
                requested_document=args.document,
            )
            for document in documents:
                raw_path = (
                    raw_dir
                    / filing.cik
                    / filing.accession.replace("-", "")
                    / _safe_name(document)
                )
                source_url = _archive_url(client, filing, document)
                reused = bool(args.reuse_existing and raw_path.exists())
                if reused:
                    source_text = raw_path.read_text(encoding="utf-8")
                else:
                    source_text = client.archive_document(
                        filing.cik, filing.accession, document
                    )
                    raw_path.parent.mkdir(parents=True, exist_ok=True)
                    raw_path.write_text(source_text, encoding="utf-8", newline="\n")
                candidates = _candidate_records(
                    filing=filing,
                    document=document,
                    source_url=source_url,
                    raw_path=raw_path.relative_to(Path.cwd()),
                    source_text=source_text,
                    max_candidates=args.max_candidates_per_document,
                )
                all_candidates.extend(candidates)
                filings_manifest.append(
                    {
                        **asdict(filing),
                        "document_name": document,
                        "source_url": source_url,
                        "source_document_path": raw_path.relative_to(Path.cwd()).as_posix(),
                        "source_document_sha256": _sha256_text(source_text),
                        "source_bytes": len(source_text.encode("utf-8")),
                        "candidate_count": len(candidates),
                        "reused": reused,
                    }
                )
    candidate_path = output_dir / "sec_guidance_real_candidates.jsonl"
    manifest_path = output_dir / "sec_guidance_real_candidates_manifest.json"
    _write_jsonl(candidate_path, all_candidates)
    manifest = {
        "schema_version": 1,
        "gold_labels_assigned": False,
        "tickers": tickers,
        "forms": sorted(forms),
        "filings": filings_manifest,
        "candidate_count": len(all_candidates),
        "sec_client_stats": asdict(client.stats()),
        "candidate_path": candidate_path.as_posix(),
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect real SEC guidance candidates without assigning gold labels."
    )
    parser.add_argument("--ticker", action="append", required=True)
    parser.add_argument("--accession")
    parser.add_argument("--form", action="append", default=[])
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--output-dir", default="output/sec_guidance_real_corpus_work")
    parser.add_argument("--reuse-existing", action="store_true")
    parser.add_argument("--include-exhibits", action="store_true")
    parser.add_argument("--earnings-only", action="store_true")
    parser.add_argument("--document")
    parser.add_argument("--max-filings-per-ticker", type=int, default=3)
    parser.add_argument("--max-candidates-per-document", type=int, default=20)
    return parser.parse_args()


if __name__ == "__main__":
    result = build(parse_args())
    print(json.dumps(result, indent=2, ensure_ascii=False))
