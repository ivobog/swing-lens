import pandas as pd

from app.services.pine_replica_engine import (
    CLASS_BLOWOFF_TOP,
    CLASS_CLEAN_PULLBACK,
    CLASS_DISTRIBUTION_RISK,
    CLASS_FAILED_BREAKOUT,
    CLASS_NO_TRADE,
    CLASS_PRIME_PULLBACK,
    _meets_threshold,
    action_bias_text,
    classify_setup,
    dual_score,
    engine_version,
    score_from_feature_result,
)
from app.services.technical_indicators import calculate_technical_features
from app.services.technical_score_service import build_technical_score


def test_engine_version_matches_pine_defaults() -> None:
    assert engine_version() == "3.2.0"


def test_classification_priority_danger_overrides_buyable() -> None:
    common = dict(
        trend_score_value=9,
        momentum_score_value=9,
        setup_score_value=9,
        risk_score_value=2,
        had_pullback=True,
        not_too_deep=True,
        held_near_support=True,
        above_mid_ma=True,
        rsi_value=60,
        extension_mid_pct=2,
        extension_warn_pct=8,
        extension_danger_pct=15,
        entry_filters_ok=True,
        all_filters_ok=True,
        fresh_breakout=False,
        strong_close=0.8,
        above_slow_ma=True,
        mid_slope_pct=2,
        combined_rs_score=8,
    )

    assert (
        classify_setup(
            **common,
            blowoff_top_value=True,
            failed_breakout=True,
            distribution_risk_value=True,
        )
        == CLASS_BLOWOFF_TOP
    )
    assert (
        classify_setup(
            **common,
            blowoff_top_value=False,
            failed_breakout=True,
            distribution_risk_value=True,
        )
        == CLASS_FAILED_BREAKOUT
    )
    assert (
        classify_setup(
            **common,
            blowoff_top_value=False,
            failed_breakout=False,
            distribution_risk_value=True,
        )
        == CLASS_DISTRIBUTION_RISK
    )
    assert (
        classify_setup(
            **common,
            blowoff_top_value=False,
            failed_breakout=False,
            distribution_risk_value=False,
        )
        == CLASS_PRIME_PULLBACK
    )


def test_classification_clean_pullback_and_no_trade() -> None:
    borderline_prime = classify_setup(
        trend_score_value=8.0,
        momentum_score_value=8.2,
        setup_score_value=8.4,
        risk_score_value=0.7,
        had_pullback=True,
        not_too_deep=True,
        held_near_support=True,
        above_mid_ma=True,
        rsi_value=58,
        extension_mid_pct=4.5,
        extension_warn_pct=8,
        extension_danger_pct=15,
        entry_filters_ok=True,
        all_filters_ok=True,
        distribution_risk_value=False,
        blowoff_top_value=False,
        failed_breakout=False,
        fresh_breakout=False,
        strong_close=0.7,
        above_slow_ma=True,
        mid_slope_pct=1,
        combined_rs_score=9.5,
    )
    clean = classify_setup(
        trend_score_value=7.2,
        momentum_score_value=6.7,
        setup_score_value=7.7,
        risk_score_value=3.5,
        had_pullback=True,
        not_too_deep=True,
        held_near_support=True,
        above_mid_ma=True,
        rsi_value=58,
        extension_mid_pct=3,
        extension_warn_pct=8,
        extension_danger_pct=15,
        entry_filters_ok=True,
        all_filters_ok=True,
        distribution_risk_value=False,
        blowoff_top_value=False,
        failed_breakout=False,
        fresh_breakout=False,
        strong_close=0.7,
        above_slow_ma=True,
        mid_slope_pct=1,
        combined_rs_score=7,
    )
    no_trade = classify_setup(
        trend_score_value=2,
        momentum_score_value=2,
        setup_score_value=2,
        risk_score_value=2,
        had_pullback=False,
        not_too_deep=True,
        held_near_support=False,
        above_mid_ma=False,
        rsi_value=45,
        extension_mid_pct=0,
        extension_warn_pct=8,
        extension_danger_pct=15,
        entry_filters_ok=True,
        all_filters_ok=True,
        distribution_risk_value=False,
        blowoff_top_value=False,
        failed_breakout=False,
        fresh_breakout=False,
        strong_close=0.5,
        above_slow_ma=False,
        mid_slope_pct=-1,
        combined_rs_score=2,
    )

    assert borderline_prime == CLASS_CLEAN_PULLBACK
    assert clean == CLASS_CLEAN_PULLBACK
    assert no_trade == CLASS_NO_TRADE


def test_dual_score_balanced_weights() -> None:
    assert dual_score(8, 7, 6, 3, 7, 8, 6, "Balanced") == 7.09


def test_reward_risk_threshold_tolerates_float_boundary() -> None:
    assert _meets_threshold(1.9999999999999984, 2.0)
    assert not _meets_threshold(1.9999, 2.0)


def test_action_bias_text() -> None:
    assert (
        action_bias_text(CLASS_PRIME_PULLBACK, "R/R ok", "Filter failed")
        == "Best buyable, R/R ok"
    )
    assert action_bias_text(CLASS_FAILED_BREAKOUT, "R/R ok", "Filter failed") == "Avoid"


def test_score_from_feature_result_returns_technical_score_shape() -> None:
    frame = _synthetic_uptrend()
    features = calculate_technical_features(frame, ticker="TEST")

    result = score_from_feature_result(features)

    assert result.ticker == "TEST"
    assert 0 <= result.trend_score <= 10
    assert 0 <= result.momentum_score <= 10
    assert 0 <= result.setup_score <= 10
    assert 0 <= result.risk_score <= 10
    assert 0 <= result.dual_score <= 10
    assert result.classification
    assert result.action_bias
    assert result.debug["derived"]


def test_build_technical_score_maps_replica_to_model() -> None:
    frame = _synthetic_uptrend()
    features = calculate_technical_features(frame, ticker="TEST")
    result = score_from_feature_result(features)

    model = build_technical_score(run_id=12, score=result)

    assert model.run_id == 12
    assert model.ticker == "TEST"
    assert model.dual_score is not None
    assert model.classification == result.classification
    assert model.debug_json["derived"]


def _synthetic_uptrend() -> pd.DataFrame:
    rows = []
    for index in range(320):
        base = 50 + index * 0.35
        rows.append(
            {
                "date": pd.Timestamp("2025-01-01") + pd.Timedelta(days=index),
                "open": base,
                "high": base + 2,
                "low": base - 1,
                "close": base + 1.2,
                "volume": 1_000_000 + index * 2_000,
            }
        )
    return pd.DataFrame(rows)
