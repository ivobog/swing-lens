from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.services.ib_market_intelligence.calculations import (
    calculate_histogram,
    calculate_liquidity,
    calculate_options_activity,
    calculate_short_pressure,
    calculate_volatility,
    options_event_premium_score,
    safe_ratio,
)
from app.services.ib_market_intelligence.enums import AvailabilityStatus


def _bars(values, *, start=date(2026, 1, 2), field="close_value"):
    return [
        SimpleNamespace(
            session_date=start + timedelta(days=index),
            data_hash=f"h{index}",
            **{field: value},
        )
        for index, value in enumerate(values)
    ]


def _bid_ask(spreads, *, start=date(2026, 1, 2)):
    return [
        SimpleNamespace(
            session_date=start + timedelta(days=index),
            open_value=100.0,
            close_value=100.0 + spread,
            data_hash=f"ba{index}",
        )
        for index, spread in enumerate(spreads)
    ]


def test_liquidity_tight_wide_stale_and_deterministic():
    config = {
        "short_window_sessions": 5,
        "lookback_sessions": 20,
        "historical_max_age_days": 5,
        "spread_grade_pct": {
            "excellent_max": 0.10,
            "good_max": 0.25,
            "acceptable_max": 0.50,
            "poor_max": 1.0,
        },
    }
    tight = calculate_liquidity(
        _bid_ask([0.05] * 20),
        as_of=date(2026, 1, 25),
        dollar_volume=10_000_000,
        config=config,
    )
    assert tight.classification == "EXCELLENT"
    assert tight.components["median_spread_20d"] == pytest.approx(0.0499875)
    assert tight == calculate_liquidity(
        _bid_ask([0.05] * 20),
        as_of=date(2026, 1, 25),
        dollar_volume=10_000_000,
        config=config,
    )
    wide = calculate_liquidity(
        _bid_ask([2.0] * 20),
        as_of=date(2026, 1, 25),
        config=config,
    )
    assert wide.classification == "VERY_POOR"
    stale = calculate_liquidity(
        _bid_ask([0.05]),
        as_of=date(2026, 2, 1),
        config=config,
    )
    assert "STALE_BID_ASK" in stale.warnings


@pytest.mark.parametrize(
    ("bid", "ask"),
    [(0, 1), (None, 1), (1, None), (2, 1), (float("nan"), 1)],
)
def test_liquidity_rejects_invalid_and_missing_bid_ask(bid, ask):
    feature = calculate_liquidity(
        [SimpleNamespace(session_date=date.today(), open_value=bid, close_value=ask)],
        as_of=date.today(),
    )
    assert feature.classification == "INSUFFICIENT"
    assert feature.score is None


def test_liquidity_outlier_uses_robust_median():
    feature = calculate_liquidity(
        _bid_ask([0.1] * 19 + [20]),
        as_of=date(2026, 1, 25),
        config={"lookback_sessions": 20, "historical_max_age_days": 5},
    )
    assert feature.components["median_spread_20d"] < 0.11


def test_short_pressure_components_missing_data_and_semantics():
    fees = _bars([2.0] * 16 + [4, 6, 8, 12, 20])
    feature = calculate_short_pressure(
        fees,
        as_of=date.today(),
        shortable_shares=20_000,
        shortable_state="LOCATE_MAY_BE_REQUIRED",
        config={
            "high_fee_rate_pct": 10,
            "extreme_fee_rate_pct": 25,
            "low_availability_shares": 100_000,
            "very_low_availability_shares": 25_000,
        },
    )
    assert feature.classification == "HIGH_BORROW_COST"
    assert feature.score is not None and feature.score >= 7
    assert "NOT_OFFICIAL_SHORT_INTEREST" in feature.warnings
    missing = calculate_short_pressure(
        [],
        as_of=date.today(),
        availability_status=AvailabilityStatus.SUBSCRIPTION_REQUIRED,
    )
    assert missing.score is None
    assert missing.coverage_status == AvailabilityStatus.SUBSCRIPTION_REQUIRED


def test_short_pressure_non_shortable_and_easy_to_borrow():
    non_shortable = calculate_short_pressure(
        _bars([1.0]),
        as_of=date.today(),
        shortable_state="NOT_SHORTABLE",
    )
    assert non_shortable.classification == "NOT_SHORTABLE"
    easy = calculate_short_pressure(
        _bars([0.25] * 21),
        as_of=date.today(),
        shortable_shares=5_000_000,
        shortable_state="EASY_TO_BORROW",
    )
    assert easy.classification == "EASY_TO_BORROW"


def test_short_pressure_marks_stale_historical_and_live_evidence():
    feature = calculate_short_pressure(
        _bars([2.0], start=date(2026, 1, 1)),
        as_of=date(2026, 2, 1),
        shortable_shares=10_000,
        availability_status=AvailabilityStatus.STALE,
        config={"historical_max_age_days": 5},
    )
    assert feature.freshness_status == AvailabilityStatus.STALE
    assert "BORROW_DATA_STALE" in feature.warnings
    assert "SHORTABLE_DATA_STALE" in feature.warnings


@pytest.mark.parametrize(
    ("hv", "iv", "expected"),
    [
        ([0.2], [0.4], "EXTREME_IV_PREMIUM"),
        ([0.2], [0.2], "IV_NORMAL"),
        ([0.4], [0.2], "IV_NORMAL"),
    ],
)
def test_volatility_relationships(hv, iv, expected):
    feature = calculate_volatility(_bars(hv), _bars(iv), as_of=date.today())
    assert feature.classification == expected


def test_volatility_zero_missing_entitlement_and_ceri_bound():
    zero = calculate_volatility(_bars([0]), _bars([0.4]), as_of=date.today())
    assert zero.components["iv_hv_ratio"] is None
    missing = calculate_volatility(
        _bars([0.2]),
        [],
        as_of=date.today(),
        iv_availability=AvailabilityStatus.SUBSCRIPTION_REQUIRED,
    )
    assert missing.coverage_status == AvailabilityStatus.SUBSCRIPTION_REQUIRED
    inferred_missing = calculate_volatility(_bars([0.2]), [], as_of=date.today())
    assert inferred_missing.coverage_status == AvailabilityStatus.UNAVAILABLE
    unavailable_with_old_iv = calculate_volatility(
        _bars([0.2]),
        _bars([0.4]),
        as_of=date.today(),
        iv_availability=AvailabilityStatus.SUBSCRIPTION_REQUIRED,
    )
    assert unavailable_with_old_iv.coverage_status == AvailabilityStatus.SUBSCRIPTION_REQUIRED
    extreme = calculate_volatility(_bars([0.1]), _bars([0.4]), as_of=date.today())
    assert options_event_premium_score(extreme, maximum=1.5) == 1.5


def test_volatility_marks_stale_metric_evidence():
    feature = calculate_volatility(
        _bars([0.2], start=date(2026, 1, 1)),
        _bars([0.4], start=date(2026, 1, 1)),
        as_of=date(2026, 2, 1),
        config={"historical_max_age_days": 5},
    )
    assert feature.freshness_status == AvailabilityStatus.STALE
    assert "VOLATILITY_DATA_STALE" in feature.warnings


def test_options_activity_ratios_zero_and_unavailable_are_distinct():
    feature = calculate_options_activity(
        {
            "call_volume": 100,
            "put_volume": 40,
            "call_open_interest": 500,
            "put_open_interest": 400,
            "average_option_volume": 50,
        }
    )
    assert feature.classification == "ABNORMAL_OPTION_ACTIVITY"
    assert feature.components["put_call_volume_ratio"] == pytest.approx(0.4)
    observed_zero = calculate_options_activity({"call_volume": 0, "put_volume": 5})
    assert observed_zero.components["put_call_volume_ratio"] is None
    unavailable = calculate_options_activity(
        {},
        availability_status=AvailabilityStatus.SUBSCRIPTION_REQUIRED,
    )
    assert unavailable.classification == "INSUFFICIENT"
    assert "OPTIONS_ACTIVITY_SUBSCRIPTION_REQUIRED" in unavailable.warnings
    partial = calculate_options_activity(
        {"call_volume": 100, "put_volume": 40},
        availability_status=AvailabilityStatus.UNAVAILABLE,
    )
    assert partial.classification == "INSUFFICIENT"
    assert partial.score is None
    assert partial.components["put_call_volume_ratio"] is None
    assert partial.coverage_status == AvailabilityStatus.UNAVAILABLE


def test_histogram_unimodal_multiple_peaks_sparse_and_relative_price():
    feature = calculate_histogram(
        [(98, 5), (99, 20), (100, 50), (101, 20), (102, 5)],
        reference_price=103,
    )
    assert feature.components["poc_like_price"] == 100
    assert feature.classification == "ABOVE_DOMINANT_AREA"
    assert "NOT_EXCHANGE_VOLUME_PROFILE" in feature.warnings
    tied = calculate_histogram([(10, 5), (11, 20), (12, 20), (13, 5)], reference_price=9)
    assert tied.components["poc_like_price"] == 11
    assert tied.classification == "BELOW_DOMINANT_AREA"
    sparse = calculate_histogram([(10, 1)], reference_price=10)
    assert sparse.classification == "INSUFFICIENT"


def test_safe_ratio_rejects_zero_nan_and_infinity():
    assert safe_ratio(1, 0) is None
    assert safe_ratio(float("nan"), 1) is None
    assert safe_ratio(1, float("inf")) is None
