from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import engine
from app.models.ceri_tables import (
    CeriCompany,
    CeriSecDocumentExtraction,
    CeriSecFilingDocument,
    CeriSourceRecord,
)
from app.models.tables import RawCompanyRow
from app.services.ceri.sec.processor_signature import sec_guidance_processor_signature
from app.services.ceri.sec.readiness_diagnostics import diagnose_sec_readiness


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Aggregate partitioned SHADOW and ACTIVE-warm SEC certification evidence."
    )
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--shadow-report", required=True, action="append", type=Path)
    parser.add_argument("--active-report", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    signature = sec_guidance_processor_signature()
    shadow_reports = [_load_report(path) for path in args.shadow_report]
    active_report = _load_report(args.active_report)
    with Session(engine) as db:
        expected_tickers = _run_tickers(db, args.run_id)
        readiness = diagnose_sec_readiness(
            db,
            tickers=expected_tickers,
            processor_signature=signature,
        )
        database_invariants = _database_invariants(
            db,
            tickers=expected_tickers,
            processor_signature=signature,
        )

    shadow_tickers = [
        str(ticker).strip().upper()
        for report in shadow_reports
        for ticker in report.get("tickers", [])
    ]
    active_tickers = tuple(
        sorted(
            {
                str(ticker).strip().upper()
                for ticker in active_report.get("tickers", [])
                if ticker
            }
        )
    )
    scenario_names = [
        {str(item.get("name")) for item in report.get("scenarios", [])}
        for report in shadow_reports
    ]
    active_scenario_names = {
        str(item.get("name")) for item in active_report.get("scenarios", [])
    }
    shadow_first_scenarios = [
        item
        for report in shadow_reports
        for item in report.get("scenarios", [])
        if item.get("name") == "shadow_first"
    ]
    active_scenario = next(
        (
            item
            for item in active_report.get("scenarios", [])
            if item.get("name") == "active_warm"
        ),
        {},
    )
    shadow_first_bytes = sum(
        int(item.get("bytes_downloaded") or 0) for item in shadow_first_scenarios
    )
    shadow_first_elapsed = max(
        (float(item.get("elapsed_seconds") or 0) for item in shadow_first_scenarios),
        default=0,
    )
    active_bytes = int(active_scenario.get("bytes_downloaded") or 0)
    active_elapsed = float(active_scenario.get("elapsed_seconds") or 0)
    checks = {
        "shadow_reports_present": bool(shadow_reports),
        "shadow_reports_passed": all(report.get("passed") is True for report in shadow_reports),
        "shadow_signatures_match_deployed": all(
            report.get("processor_signature") == signature for report in shadow_reports
        ),
        "shadow_scenarios_complete": all(
            names == {"shadow_first", "shadow_repeat"} for names in scenario_names
        ),
        "shadow_check_sets_passed": all(
            report.get("checks")
            and all(bool(value) for value in report["checks"].values())
            for report in shadow_reports
        ),
        "shadow_partitions_have_no_overlap": all(
            count == 1 for count in Counter(shadow_tickers).values()
        ),
        "shadow_universe_matches_run": tuple(sorted(shadow_tickers)) == expected_tickers,
        "active_report_passed": active_report.get("passed") is True,
        "active_signature_matches_deployed": (
            active_report.get("processor_signature") == signature
        ),
        "active_scenario_is_warm_only": active_scenario_names == {"active_warm"},
        "active_check_set_passed": bool(active_report.get("checks"))
        and all(bool(value) for value in active_report["checks"].values()),
        "active_universe_matches_run": active_tickers == expected_tickers,
        "active_repeated_bytes_reduction_gt_95pct": (
            shadow_first_bytes > 0 and active_bytes < shadow_first_bytes * 0.05
        ),
        "active_repeated_elapsed_reduction_gt_80pct": (
            shadow_first_elapsed > 0 and active_elapsed < shadow_first_elapsed * 0.20
        ),
        "readiness_complete": readiness.complete,
        "all_current_signature_extractions_terminal": (
            database_invariants["nonterminal_extractions"] == 0
        ),
        "no_duplicate_sec_source_record_identity": (
            database_invariants["duplicate_source_record_groups"] == 0
        ),
    }
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "run_id": args.run_id,
        "processor_signature": signature,
        "expected_tickers": list(expected_tickers),
        "shadow_reports": [str(path.resolve()) for path in args.shadow_report],
        "active_report": str(args.active_report.resolve()),
        "warm_validation_measurements": {
            "shadow_first_bytes": shadow_first_bytes,
            "active_bytes": active_bytes,
            "shadow_first_partition_wall_seconds": shadow_first_elapsed,
            "active_wall_seconds": active_elapsed,
        },
        "readiness": readiness.as_dict(),
        "database_invariants": database_invariants,
        "checks": checks,
        "passed": all(checks.values()),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown = args.output.with_suffix(".md")
    markdown.write_text(_markdown(report), encoding="utf-8")
    print(f"aggregate_output={args.output.resolve()}")
    print(f"aggregate_markdown={markdown.resolve()}")
    print(f"aggregate_passed={report['passed']}")
    return 0 if report["passed"] else 1


def _load_report(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SystemExit(f"Certification report not found: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"Certification report is not a JSON object: {path}")
    return value


def _run_tickers(db: Session, run_id: int) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                str(value).strip().upper()
                for value in db.scalars(
                    select(RawCompanyRow.ticker).where(RawCompanyRow.run_id == run_id)
                )
                if value
            }
        )
    )


def _database_invariants(
    db: Session,
    *,
    tickers: tuple[str, ...],
    processor_signature: str,
) -> dict[str, Any]:
    ciks = tuple(
        sorted(
            {
                str(value)
                for value in db.scalars(
                    select(CeriCompany.cik).where(CeriCompany.ticker.in_(tickers))
                )
                if value
            }
        )
    )
    extraction_rows = list(
        db.execute(
            select(CeriSecDocumentExtraction.status, func.count())
            .join(
                CeriSecFilingDocument,
                CeriSecFilingDocument.id == CeriSecDocumentExtraction.document_id,
            )
            .where(
                CeriSecFilingDocument.cik.in_(ciks),
                CeriSecDocumentExtraction.dataset == "guidance",
                CeriSecDocumentExtraction.processor_signature == processor_signature,
            )
            .group_by(CeriSecDocumentExtraction.status)
        )
    )
    extraction_status_counts = {
        str(status): int(count) for status, count in extraction_rows
    }
    terminal = {"COMPLETED_WITH_RECORDS", "COMPLETED_NO_RECORDS"}
    nonterminal = sum(
        count for status, count in extraction_status_counts.items() if status not in terminal
    )
    duplicates = (
        select(CeriSourceRecord.provider_record_id)
        .where(CeriSourceRecord.provider == "sec", CeriSourceRecord.dataset == "guidance")
        .group_by(
            CeriSourceRecord.provider,
            CeriSourceRecord.dataset,
            CeriSourceRecord.provider_record_id,
            CeriSourceRecord.content_hash,
        )
        .having(func.count() > 1)
        .subquery()
    )
    duplicate_groups = int(
        db.scalar(select(func.count()).select_from(duplicates)) or 0
    )
    return {
        "cik_count": len(ciks),
        "extraction_status_counts": extraction_status_counts,
        "nonterminal_extractions": int(nonterminal),
        "duplicate_source_record_groups": duplicate_groups,
        "source_lineage_constraints": [
            "uq_ceri_source_records_provider_record",
            "uq_ceri_source_records_idempotency",
            "uq_ceri_sec_document_extraction_identity",
        ],
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# SEC Certification Aggregate — Run {report['run_id']}",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Processor: `{report['processor_signature']}`",
        f"Universe: `{len(report['expected_tickers'])}` tickers",
        f"Readiness: `{report['readiness']['ready_tickers']}/"
        f"{report['readiness']['requested_tickers']}`",
        "",
        "## Checks",
        "",
    ]
    lines.extend(
        f"- [{'x' if passed else ' '}] {name}"
        for name, passed in report["checks"].items()
    )
    lines.extend(
        [
            "",
            f"Overall: **{'PASS' if report['passed'] else 'FAIL'}**",
            "",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
