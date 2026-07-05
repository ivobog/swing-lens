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


def test_score_from_feature_result_includes_market_regime_v4_debug() -> None:
    frame = _synthetic_uptrend()
    features = calculate_technical_features(frame, ticker="TEST")
    market_features = calculate_technical_features(frame, ticker="SPY").latest

    result = score_from_feature_result(
        features,
        market_features=market_features,
        qqq_market_features=market_features,
    )

    assert result.debug["market_regime_v4"]["regime"] == "Bull trend"
    assert result.debug["market_regime_v4"]["gate_ok"] is True


def test_score_from_feature_result_includes_v4_explainability_snapshot() -> None:
    frame = _synthetic_uptrend()
    features = calculate_technical_features(frame, ticker="TEST")
    result = score_from_feature_result(features)

    explainability = result.debug["explainability"]

    assert explainability["engine_version"] == "4.0.0"
    assert explainability["base_engine_version"] == "3.2.0"
    assert explainability["data_readiness"]["confidence"] == result.technical_confidence
    assert "atr_percentile_252" in explainability["adaptive"]
    assert "vcp_score" in explainability["contraction"]
    assert "box_tightness_score" in explainability["box"]
    assert "stage" in explainability["stage"]
    assert "regime" in explainability["regime"]
    assert "climax_risk_score" in explainability["climax"]
    assert isinstance(explainability["feature_flags"], list)
    assert isinstance(explainability["warning_flags"], list)
    assert isinstance(explainability["sub_tags"], list)
    assert explainability["final_v4_score"] == result.dual_score
    assert explainability["final_v4_classification"] == result.classification
    assert explainability["final_v4_action"] == result.action_bias


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
    assert model.technical_engine_version == "4.0.0"
    assert model.data_quality_score is not None
    assert model.stage == result.debug["explainability"]["stage"]["stage"]
    assert model.market_regime == result.debug["explainability"]["regime"]["regime"]
    assert model.vcp_score is not None
    assert model.box_tightness_score is not None
    assert model.breakout_quality_score is not None
    assert model.climax_risk_score is not None
    assert model.feature_flags_json == result.debug["explainability"]["feature_flags"]
    assert model.warning_flags_json == result.debug["explainability"]["warning_flags"]
    assert model.sub_tags_json == result.debug["explainability"]["sub_tags"]
    assert model.v4_debug_json == result.debug["explainability"]


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
