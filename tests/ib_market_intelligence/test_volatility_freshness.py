from dataclasses import asdict
from datetime import date, timedelta

import pytest

from app.services.ib_market_intelligence.calculations import calculate_volatility
from app.services.ib_market_intelligence.enums import AvailabilityStatus, Confidence


def series(values, latest):
    return [
        {
            "close_value": value,
            "session_date": latest - timedelta(days=len(values) - i - 1),
            "data_hash": f"{latest}:{i}:{value}",
        }
        for i, value in enumerate(values)
    ]


def test_fresh_latest_preserves_long_history_change_percentile_and_evidence():
    session = date(2026, 10, 30)
    hv = series([0.2] * 252, session)
    iv = series([0.2 + i / 1000 for i in range(252)], session)
    feature = calculate_volatility(
        list(reversed(hv)), list(reversed(iv)), as_of=session, config={"historical_max_age_days": 5}
    )
    assert feature.freshness_status == AvailabilityStatus.AVAILABLE
    assert feature.coverage_status == AvailabilityStatus.AVAILABLE
    assert feature.confidence == Confidence.HIGH
    assert "VOLATILITY_DATA_STALE" not in feature.warnings
    assert "VOL_DATA_STALE" not in feature.reasons
    assert feature.components["iv_20d_change"] == pytest.approx(0.02)
    assert feature.components["iv_rank_252"] == pytest.approx(100 * 251.5 / 252)
    assert len(feature.evidence_hashes) == 504


@pytest.mark.parametrize(
    ("hv_age", "iv_age", "stale"), [(0, 6, True), (6, 0, True), (5, 5, False), (6, 6, True)]
)
def test_each_latest_source_and_calendar_age_boundary_are_checked(hv_age, iv_age, stale):
    session = date(2026, 10, 30)
    feature = calculate_volatility(
        series([0.2] * 30, session - timedelta(days=hv_age)),
        series([0.3] * 30, session - timedelta(days=iv_age)),
        as_of=session,
        config={"historical_max_age_days": 5},
    )
    assert (feature.freshness_status == AvailabilityStatus.STALE) is stale
    assert ("VOLATILITY_DATA_STALE" in feature.warnings) is stale
    assert ("VOL_DATA_STALE" in feature.reasons) is stale


@pytest.mark.parametrize("missing", ["hv", "iv", "both"])
def test_missing_series_keeps_missing_values_and_unavailable_coverage(missing):
    session = date(2026, 10, 30)
    feature = calculate_volatility(
        [] if missing in {"hv", "both"} else series([0.2], session),
        [] if missing in {"iv", "both"} else series([0.3], session),
        as_of=session,
    )
    assert feature.coverage_status == AvailabilityStatus.UNAVAILABLE
    assert feature.components["iv_hv_ratio"] is None
    assert feature.score is None
    if missing in {"iv", "both"}:
        assert feature.components["implied_volatility"] is None
        assert feature.classification == "INSUFFICIENT"
    if missing == "both":
        assert feature.freshness_status == AvailabilityStatus.UNAVAILABLE


def test_invalid_recent_values_do_not_hide_stale_latest_usable_source():
    session = date(2026, 10, 30)
    hv = series([0.2], session - timedelta(days=6)) + series([None], session)
    iv = series([0.3] * 30, session)
    feature = calculate_volatility(hv, iv, as_of=session)
    assert feature.freshness_status == AvailabilityStatus.STALE
    assert feature.components["historical_volatility"] == 0.2


def test_subscription_failure_is_preserved_with_fresh_latest_values():
    session = date(2026, 10, 30)
    feature = calculate_volatility(
        series([0.2] * 30, session),
        series([0.3] * 30, session),
        as_of=session,
        iv_availability=AvailabilityStatus.SUBSCRIPTION_REQUIRED,
    )
    assert feature.coverage_status == AvailabilityStatus.SUBSCRIPTION_REQUIRED
    assert "OPTIONS_SUBSCRIPTION_REQUIRED" in feature.warnings


@pytest.mark.parametrize(("latest_age", "expected"), [(0, "ELIGIBLE"), (6, "INELIGIBLE")])
def test_latest_source_freshness_reaches_unchanged_ceri_readiness_policy(latest_age, expected):
    from app.services.contextual_consumer_eligibility import IBMI_VOLATILITY_TO_CERI
    from app.services.producer_readiness import normalize_producer_readiness

    session = date(2026, 10, 30)
    feature = calculate_volatility(
        series([0.2] * 252, session),
        series([0.3] * 252, session - timedelta(days=latest_age)),
        as_of=session,
    )
    readiness = normalize_producer_readiness(
        "IBMI", {"derived_output": asdict(feature)}, identity_fingerprint="a" * 64
    )
    decision = IBMI_VOLATILITY_TO_CERI.evaluate(readiness)
    assert decision.status.value == expected
