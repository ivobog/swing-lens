"""Recovery gates cannot turn data exclusions or integrity failures into success."""

from copy import deepcopy

import pytest

from scripts.forensics.run41_outcome_reference import derive_pipeline_outcome
from scripts.forensics.run41_recovery_contract import (
    EXPECTED_EXCLUDED_TICKERS,
    validate_terminal_result,
)


def _case():
    result = {
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
        "winner_prediction_exclusion_reasons": {"winner_source_ineligible": 6},
    }
    result["pipeline_outcome_summary"] = derive_pipeline_outcome("PARTIAL", result)
    return {
        "status": "PARTIAL",
        "result": result,
        "prediction_count": 82,
        "excluded_tickers": sorted(EXPECTED_EXCLUDED_TICKERS),
        "step_statuses": ["COMPLETED"] * 13,
    }


def test_preserved_known_gaps_are_explicit_success_with_gaps():
    proof = validate_terminal_result(**_case())
    assert proof["pipeline_status"] == "PARTIAL"
    assert proof["execution_outcome"] == "SUCCEEDED_WITH_GAPS"
    assert proof["winner_predictions"] == 82
    assert proof["preserved_exclusions"] == sorted(EXPECTED_EXCLUDED_TICKERS)


@pytest.mark.parametrize(
    "change",
    [
        "new_failure",
        "integrity_hazard",
        "corrupt_summary",
        "new_gap",
        "changed_exclusions",
        "unexpected_predictions",
        "forced_green",
        "missing_step",
        "unreported_fetch_failure",
    ],
)
def test_recovery_rejects_new_failures_hazards_and_changed_coverage(change):
    case = deepcopy(_case())
    result = case["result"]
    if change == "new_failure":
        result["winner_prediction_failed"] = 1
    elif change == "integrity_hazard":
        result["contamination_detected"] = True
    elif change == "corrupt_summary":
        result["pipeline_outcome_summary"]["summary_fingerprint"] = "altered"
    elif change == "new_gap":
        result["ceri_failed"] = 1
    elif change == "changed_exclusions":
        case["excluded_tickers"][0] = "UNEXPECTED"
    elif change == "unexpected_predictions":
        case["prediction_count"] = 83
    elif change == "forced_green":
        case["status"] = "COMPLETED"
    elif change == "missing_step":
        case["step_statuses"].pop()
    elif change == "unreported_fetch_failure":
        result["fetch_failed"] = 1
    if change != "corrupt_summary":
        result["pipeline_outcome_summary"] = derive_pipeline_outcome(case["status"], result)
    with pytest.raises(ValueError, match="RUN41_RECOVERY_"):
        validate_terminal_result(**case)
