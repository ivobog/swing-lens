from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.services.eligibility_policy import (
    AnalyticalConfidence,
    CompletenessStatus,
    ConsumerEligibilityOutcome,
)
from app.services.fundamental_admission_policy import (
    FUNDAMENTAL_ADMISSION_POLICY_VERSION,
    FUNDAMENTAL_COMPONENTS,
    FundamentalAdmissionInput,
    evaluate_fundamental_admission,
    fundamental_input_from_frozen_row,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "readiness_phase2"
NOW = datetime(2026, 10, 13, 9, 30, tzinfo=UTC)


def _coverage(*, available=78, critical=(), high=(), medium=(), low=("capex_estimate_ntm",)):
    ratio = round(available / 79, 4)
    return {
        "available_scoring_fields": available,
        "total_scoring_fields": 79,
        "coverage_ratio": ratio,
        "data_coverage_score": round(ratio * 10, 4),
        "missing_fields_by_priority": {
            "critical": list(critical),
            "high": list(high),
            "medium": list(medium),
            "low": list(low),
        },
    }


def _input(**overrides) -> FundamentalAdmissionInput:
    values = {
        "consumer_edge": "FUNDAMENTAL_TO_COMBINED",
        "source_identity_fingerprint": "a" * 64,
        "source_evidence_id": 42,
        "decision_cutoff": NOW,
        "fundamental_score": "7.25",
        "fundamental_label": "High-quality quant",
        "scoring_model_version": "fundamentals_v2.1",
        "coverage": _coverage(),
        "component_coverage": dict.fromkeys(FUNDAMENTAL_COMPONENTS, 1.0),
        "parse_diagnostics": {"failed_field_count": 0, "failed_fields": []},
        "warning_codes": ("balance_sheet_stress",),
    }
    values.update(overrides)
    return FundamentalAdmissionInput(**values)


def test_low_priority_gap_can_be_included_only_with_component_support() -> None:
    decision = evaluate_fundamental_admission(_input())

    assert decision.eligibility is ConsumerEligibilityOutcome.INCLUDED
    assert decision.completeness_status is CompletenessStatus.PARTIAL
    assert decision.analytical_confidence is AnalyticalConfidence.LOW
    assert "low_priority_missing_with_component_support" in decision.reason_codes
    assert decision.authoritative is False


def test_high_priority_gap_remains_undecided() -> None:
    value = _input(
        coverage=_coverage(available=77, high=("revenue_growth_ttm_yoy",))
    )
    decision = evaluate_fundamental_admission(value)

    assert decision.eligibility is ConsumerEligibilityOutcome.UNDECIDED
    assert "high_priority_field_missing" in decision.reason_codes


def test_absent_component_evidence_is_not_rescued_by_aggregate_score() -> None:
    components = dict.fromkeys(FUNDAMENTAL_COMPONENTS, 1.0)
    components["forward_quality_score"] = 0.0
    decision = evaluate_fundamental_admission(
        _input(component_coverage=components)
    )

    assert decision.eligibility is ConsumerEligibilityOutcome.UNDECIDED
    assert "component_evidence_absent" in decision.reason_codes


def test_setup_dependency_is_explicitly_not_certified() -> None:
    decision = evaluate_fundamental_admission(
        _input(consumer_edge="FUNDAMENTAL_TO_SETUP")
    )

    assert decision.eligibility is ConsumerEligibilityOutcome.UNDECIDED
    assert "consumer_field_dependencies_not_certified" in decision.reason_codes


def test_complete_score_is_included_at_normal_confidence() -> None:
    decision = evaluate_fundamental_admission(
        _input(coverage=_coverage(available=79, low=()), warning_codes=()),
        authoritative=True,
    )

    assert decision.eligibility is ConsumerEligibilityOutcome.INCLUDED
    assert decision.completeness_status is CompletenessStatus.COMPLETE
    assert decision.analytical_confidence is AnalyticalConfidence.NORMAL
    assert decision.policy_version == FUNDAMENTAL_ADMISSION_POLICY_VERSION
    assert decision.authoritative is True


def test_multiple_certified_low_priority_gaps_remain_bounded() -> None:
    decision = evaluate_fundamental_admission(
        _input(
            coverage=_coverage(
                available=77,
                low=("capex_estimate_ntm", "quick_ratio_annual", "analyst_rating"),
            )
        )
    )

    assert decision.eligibility is ConsumerEligibilityOutcome.INCLUDED
    assert decision.analytical_confidence is AnalyticalConfidence.LOW


@pytest.mark.parametrize(
    ("priority", "field", "reason"),
    [
        ("critical", "market_cap", "critical_field_missing"),
        ("high", "roic_annual", "high_priority_field_missing"),
        ("medium", "free_float", "medium_priority_field_missing"),
    ],
)
def test_non_low_omissions_remain_undecided(priority, field, reason) -> None:
    kwargs = {priority: (field,)}
    decision = evaluate_fundamental_admission(
        _input(coverage=_coverage(available=77, **kwargs))
    )

    assert decision.eligibility is ConsumerEligibilityOutcome.UNDECIDED
    assert reason in decision.reason_codes


@pytest.mark.parametrize("score", [None, "NaN", "Infinity", "not-a-number"])
def test_non_finite_or_invalid_score_remains_undecided(score) -> None:
    decision = evaluate_fundamental_admission(_input(fundamental_score=score))
    assert decision.eligibility is ConsumerEligibilityOutcome.UNDECIDED


def test_invalid_source_identity_and_model_contract_remain_undecided() -> None:
    decision = evaluate_fundamental_admission(
        _input(
            source_identity_fingerprint="bad",
            source_evidence_id=None,
            scoring_model_version="fundamentals_v3",
        )
    )
    assert decision.eligibility is ConsumerEligibilityOutcome.UNDECIDED
    assert "source_identity_invalid" in decision.reason_codes
    assert "scoring_model_version_unsupported" in decision.reason_codes


def test_missing_or_invalid_label_remains_undecided() -> None:
    for label in (None, "", "Invented label"):
        decision = evaluate_fundamental_admission(_input(fundamental_label=label))
        assert decision.eligibility is ConsumerEligibilityOutcome.UNDECIDED


def test_parse_failure_remains_undecided() -> None:
    decision = evaluate_fundamental_admission(
        _input(
            parse_diagnostics={
                "failed_field_count": 1,
                "failed_fields": [{"field": "pe_ratio", "reason": "invalid"}],
            }
        )
    )
    assert decision.eligibility is ConsumerEligibilityOutcome.UNDECIDED
    assert "fundamental_parse_failure" in decision.reason_codes


def test_unknown_warning_fails_closed_but_investment_risk_does_not() -> None:
    unknown = evaluate_fundamental_admission(_input(warning_codes=("new_warning",)))
    investment = evaluate_fundamental_admission(
        _input(warning_codes=("balance_sheet_stress",))
    )

    assert unknown.eligibility is ConsumerEligibilityOutcome.UNDECIDED
    assert investment.eligibility is ConsumerEligibilityOutcome.INCLUDED


def test_low_priority_name_outside_versioned_allowlist_is_not_admitted() -> None:
    decision = evaluate_fundamental_admission(
        _input(coverage=_coverage(low=("future_low_field",)))
    )
    assert decision.eligibility is ConsumerEligibilityOutcome.UNDECIDED
    assert "low_priority_field_not_certified" in decision.reason_codes


def test_invalid_field_and_component_accounting_remain_undecided() -> None:
    bad_coverage = _coverage()
    bad_coverage["total_scoring_fields"] = 80
    bad_components = dict.fromkeys(FUNDAMENTAL_COMPONENTS - {"forward_quality_score"}, 1.0)
    decision = evaluate_fundamental_admission(
        _input(coverage=bad_coverage, component_coverage=bad_components)
    )
    assert decision.eligibility is ConsumerEligibilityOutcome.UNDECIDED
    assert "field_coverage_contract_invalid" in decision.reason_codes
    assert "component_coverage_contract_invalid" in decision.reason_codes


def test_naive_decision_cutoff_is_rejected_not_repaired() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate_fundamental_admission(_input(decision_cutoff=NOW.replace(tzinfo=None)))


def test_frozen_run_33_shadow_outcomes_are_deterministic_and_non_authoritative() -> None:
    payload = json.loads((FIXTURE_DIR / "run_33.json").read_text(encoding="utf-8"))
    decisions = [
        evaluate_fundamental_admission(
            fundamental_input_from_frozen_row(row, consumer_edge="FUNDAMENTAL_TO_COMBINED")
        )
        for row in payload["fundamentals"]
    ]
    counts = Counter(decision.eligibility.value for decision in decisions)

    assert counts == {"INCLUDED": 83, "UNDECIDED": 35}
    assert all(not decision.authoritative for decision in decisions)
    assert all(decision.consumer_edge == "FUNDAMENTAL_TO_COMBINED" for decision in decisions)


def test_frozen_run_32_shadow_outcomes_are_deterministic_and_non_authoritative() -> None:
    payload = json.loads((FIXTURE_DIR / "run_32.json").read_text(encoding="utf-8"))
    decisions = [
        evaluate_fundamental_admission(
            fundamental_input_from_frozen_row(row, consumer_edge="FUNDAMENTAL_TO_RANKING")
        )
        for row in payload["fundamentals"]
    ]
    counts = Counter(decision.eligibility.value for decision in decisions)

    assert counts == {"INCLUDED": 171, "UNDECIDED": 87}
    assert all(not decision.authoritative for decision in decisions)
