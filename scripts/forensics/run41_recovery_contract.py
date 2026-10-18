"""Recovery acceptance for Run 41's unchanged, pre-existing coverage gaps.

Operational verification only; this does not grant source/decision authority.
Native consumers must continue excluding all six incomplete Combined inputs.
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import text

from scripts.forensics.run41_outcome_reference import NO_INTEGRITY_HAZARD, derive_pipeline_outcome

EXPECTED_EXCLUDED_TICKERS = frozenset({"COKE", "DK", "HNGE", "JOE", "NLST", "TEN"})


def validate_combined_rows(db, expected_hashes):
    rows = list(
        db.scalars(
            text("select to_jsonb(c) from combined_results c where run_id=41 order by ticker")
        )
    )
    hashes = {
        row["ticker"]: hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
        for row in rows
    }
    if hashes != expected_hashes or len(rows) != 88:
        raise ValueError("RUN41_RECOVERY_COMBINED_SOURCES_CHANGED")
    if {row["ticker"] for row in rows if not row["is_complete"]} != EXPECTED_EXCLUDED_TICKERS:
        raise ValueError("RUN41_RECOVERY_PREEXISTING_GAPS_CHANGED")


def validate_terminal_result(*, status, result, prediction_count, excluded_tickers, step_statuses):
    expected_counts = {
        "uploaded_rows": 88,
        "combined_results": 88,
        "incomplete_rows": 6,
        "setup_lifecycle_failed": 0,
        "winner_prediction_planned": 88,
        "winner_prediction_attempted": 88,
        "winner_prediction_inserted": 82,
        "winner_prediction_excluded": 6,
        "winner_prediction_failed": 0,
        "winner_prediction_duplicate": 0,
    }
    if any(result.get(key) != value for key, value in expected_counts.items()):
        raise ValueError("RUN41_RECOVERY_UNEXPECTED_COVERAGE_OR_FAILURE")
    if any(
        result.get(key)
        for key in (
            "ib_failure_count",
            "technical_error_count",
            "ceri_failed",
            "fetch_failed",
            "market_regime_low_confidence",
            "winner_prediction_capture_skipped",
            "winner_prediction_failure_classifications",
            "winner_prediction_representative_failures",
        )
    ):
        raise ValueError("RUN41_RECOVERY_UNEXPECTED_EXECUTION_GAP")
    summary = result.get("pipeline_outcome_summary") or {}
    if (
        status != "PARTIAL"
        or summary != derive_pipeline_outcome(status, result)
        or summary.get("execution_outcome") != "SUCCEEDED_WITH_GAPS"
        or summary.get("integrity") != {"status": NO_INTEGRITY_HAZARD, "hazards": []}
        or summary.get("coverage", {}).get("gaps") != {"incomplete_rows": 6}
    ):
        raise ValueError("RUN41_RECOVERY_UNSAFE_TERMINAL_OUTCOME")
    if (
        prediction_count != 82
        or set(excluded_tickers) != EXPECTED_EXCLUDED_TICKERS
        or len(excluded_tickers) != 6
        or result.get("winner_prediction_exclusion_reasons") != {"winner_source_ineligible": 6}
        or len(step_statuses) != 13
        or any(value != "COMPLETED" for value in step_statuses)
    ):
        raise ValueError("RUN41_RECOVERY_OUTPUT_OR_EXCLUSIONS_MISMATCH")
    return {
        "contract": "run41-preserved-gap-recovery-v1",
        "pipeline_status": "PARTIAL",
        "execution_outcome": "SUCCEEDED_WITH_GAPS",
        "integrity": NO_INTEGRITY_HAZARD,
        "completed_steps": 13,
        "winner_predictions": 82,
        "preserved_exclusions": sorted(EXPECTED_EXCLUDED_TICKERS),
        "lifecycle_failures": 0,
        "winner_failures": 0,
    }


def validate_recovery(db, pipeline, expected_combined_hashes):
    validate_combined_rows(db, expected_combined_hashes)
    exclusions = list(
        db.execute(
            text(
                "select ticker,eligibility_reason,source_decisions_json "
                "from winner_acquisition_decisions "
                "where upload_run_id=41 and acquisition_outcome='EXCLUDED' order by ticker"
            )
        ).mappings()
    )
    for row in exclusions:
        rejected = {
            item["source"]: item
            for item in row["source_decisions_json"]
            if item.get("requirement") == "MANDATORY" and not item.get("included")
        }
        if (
            row["eligibility_reason"] != "winner_source_ineligible"
            or set(rejected) != {"combined", "ranking"}
            or rejected["combined"]["producer_readiness"]["blocking_reasons"]
            != ["COMBINED_INCOMPLETE"]
            or rejected["ranking"]["producer_readiness"]["blocking_reasons"]
            != ["RANKING_INCOMPLETE"]
        ):
            raise ValueError("RUN41_RECOVERY_EXCLUSION_PROOF_MISMATCH")
    return validate_terminal_result(
        status=pipeline.status,
        result=pipeline.result_json,
        prediction_count=db.scalar(
            text("select count(*) from winner_prediction_snapshots where run_id=41")
        ),
        excluded_tickers=[row["ticker"] for row in exclusions],
        step_statuses=list(
            db.scalars(
                text(
                    "select status from pipeline_steps where pipeline_run_id=41 order by step_order"
                )
            )
        ),
    )
