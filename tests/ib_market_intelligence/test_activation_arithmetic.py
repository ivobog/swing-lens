from datetime import date

import pytest

from app.services.ceri.event_risk_service import CeriEventRiskService
from app.services.ranking_profile_config import get_ranking_profile
from app.services.ranking_profile_engine import _tradeability_penalty


@pytest.mark.parametrize(
    ("grade", "volume", "expected"),
    [
        ("EXCELLENT", 10_000_000, 0),
        ("EXCELLENT", 4_000_000, 0.5),
        ("POOR", 10_000_000, 0.5),
        ("VERY_POOR", 10_000_000, 1),
    ],
)
def test_liquidity_profile_dollar_floor_and_grade_penalties(grade, volume, expected):
    profile = get_ranking_profile("momentum_swing")
    feature = {
        "coverage_status": "AVAILABLE",
        "classification": grade,
        "components_json": {"dollar_volume": volume},
    }
    assert _tradeability_penalty(profile, feature)[0] == expected


@pytest.mark.parametrize(
    ("conflict", "stale", "premium", "marginal"),
    [
        (0, False, 1.5, 1.5),
        (1, False, 1.5, 1),
        (2, False, 1.5, 0),
        (1, True, 1.5, 0),
        (0, False, 0, 0),
    ],
)
def test_options_marginal_contribution_recomputes_the_shared_secondary_cap(
    conflict, stale, premium, marginal
):
    service = CeriEventRiskService()
    common = dict(
        as_of_session=date(2026, 10, 30),
        next_earnings_session=date(2026, 10, 30),
        conflict_penalty=conflict,
        stale=stale,
    )
    actual = service.calculate(**common, options_event_premium_score=premium)
    removed = service.calculate(**common)
    assert actual.score - removed.score == pytest.approx(marginal)
    assert any(item["name"] == "ibkr_options_event_premium" for item in actual.penalties) is bool(
        premium
    )


def test_short_pressure_changes_only_the_exact_context_reason():
    service = CeriEventRiskService()
    without = service.calculate(as_of_session=date(2026, 10, 30))
    with_context = service.calculate(
        as_of_session=date(2026, 10, 30), short_pressure_classification="EASY_TO_BORROW"
    )
    assert with_context.score == without.score
    assert with_context.penalties == without.penalties
    assert "ibkr_short_pressure_context:easy_to_borrow" in with_context.reasons


@pytest.mark.parametrize("shares", [None, 1_000_000])
def test_short_pressure_disclosure_warning_currently_blocks_ceri_even_with_live_data(shares):
    from dataclasses import asdict
    from datetime import timedelta

    from app.services.contextual_consumer_eligibility import IBMI_SHORT_PRESSURE_TO_CERI
    from app.services.ib_market_intelligence.calculations import calculate_short_pressure
    from app.services.producer_readiness import normalize_producer_readiness

    session = date(2026, 10, 30)
    bars = [
        {"close_value": 0.0025, "session_date": session - timedelta(days=29 - i)} for i in range(30)
    ]
    feature = calculate_short_pressure(bars, as_of=session, shortable_shares=shares)
    assert feature.coverage_status == "AVAILABLE"
    assert feature.freshness_status == "AVAILABLE"
    assert feature.warnings == ("NOT_OFFICIAL_SHORT_INTEREST",)
    readiness = normalize_producer_readiness(
        "IBMI", {"derived_output": asdict(feature)}, identity_fingerprint="a" * 64
    )
    decision = IBMI_SHORT_PRESSURE_TO_CERI.evaluate(readiness)
    assert readiness.status.value == "DEGRADED"
    assert decision.status.value == "POLICY_UNDECIDED"
    assert [reason.value for reason in decision.reasons] == ["DEGRADED_POLICY_UNDECIDED"]


def test_options_actual_marginal_is_limited_by_the_final_event_risk_cap():
    from app.services.ceri.catalyst_feature_service import CatalystFeature

    event = CatalystFeature(
        catalyst_event_id=1,
        catalyst_revision_id=1,
        category="REGULATORY",
        status="SCHEDULED",
        direction="NEUTRAL",
        materiality_score=10,
        opportunity_component=0,
        binary_risk_score=9.5,
        conflict_penalty=0,
        date_confidence="CONFIRMED",
        binary_eligible=True,
        risk_component="regulatory_binary_risk",
    )
    service = CeriEventRiskService()
    common = dict(as_of_session=date(2026, 10, 30), catalyst_features=[event])
    actual = service.calculate(**common, options_event_premium_score=1.5)
    removed = service.calculate(**common)
    assert actual.score == 10
    assert removed.score == 9.5
    assert actual.score - removed.score == 0.5
    assert {"name": "ibkr_options_event_premium", "value": 1.5} in actual.penalties
