from __future__ import annotations

from scripts.qa.certify_ceri_run102_golden import (
    REQUIRED_SCENARIOS,
    build_golden_traces,
    certify_golden_cohort,
)


def test_run102_golden_ten_has_complete_verticals_and_passes_code_gate() -> None:
    traces = build_golden_traces()
    summary = certify_golden_cohort(traces)

    assert len(traces) == 10
    assert {trace["scenario"] for trace in traces} == REQUIRED_SCENARIOS
    assert all(len(trace["stages"]) == 12 for trace in traces)
    assert summary["status"] == "PASS"
    assert summary["minimum_opportunity_coverage_pct"] == 60.0
    assert summary["broad_200_ticker_recertification_authorized"] is False


def test_golden_unavailable_features_are_null_with_exact_first_cause() -> None:
    traces = build_golden_traces()

    for trace in traces:
        feature = trace["stages"]["feature"]
        if feature["available"]:
            continue
        assert feature["value"] is None
        assert feature["first_cause"]


def test_rejected_sec_guidance_cannot_reach_coverage_lifecycle_or_alert() -> None:
    trace = next(
        row
        for row in build_golden_traces()
        if row["scenario"] == "REJECTED_LEGACY_SEC_GUIDANCE"
    )

    assert trace["stages"]["eligibility"]["accepted"] is False
    assert trace["stages"]["component"]["available"] is False
    assert trace["stages"]["coverage"]["opportunity_pct"] == 0.0
    assert trace["stages"]["lifecycle"]["change_created"] is False
    assert trace["stages"]["alert"]["emitted"] is False
