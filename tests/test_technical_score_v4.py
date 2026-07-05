import pandas as pd

from app.services.pine_replica_engine import PineReplicaScore, score_from_feature_result
from app.services.technical_indicators import calculate_technical_features
from app.services.technical_score_service import build_technical_score
from app.services.technical_score_v4 import technical_score_v4_from_base_score


def test_technical_score_v4_wraps_base_score_and_calculates_regime_weighted_score() -> None:
    base_score = score_from_feature_result(
        calculate_technical_features(_synthetic_uptrend(), ticker="TEST"),
        market_features=calculate_technical_features(_synthetic_uptrend(), ticker="SPY").latest,
        qqq_market_features=calculate_technical_features(_synthetic_uptrend(), ticker="QQQ").latest,
    )

    v4_score = technical_score_v4_from_base_score(
        base_score,
        {
            "regime_weights": {
                "bull_trend": {
                    "trend": 1.0,
                    "momentum": 0.0,
                    "setup": 0.0,
                    "leadership": 0.0,
                    "risk_control": 0.0,
                    "market": 0.0,
                    "execution": 0.0,
                }
            }
        },
    )

    assert v4_score.base_score is base_score
    assert v4_score.ticker == base_score.ticker
    assert v4_score.engine_version == "4.0.0"
    assert v4_score.final_v4_score == base_score.trend_score
    assert v4_score.final_v4_classification == base_score.classification
    assert v4_score.final_v4_action == base_score.action_bias
    assert v4_score.adaptive == base_score.debug["explainability"]["adaptive"]
    assert v4_score.contraction == base_score.debug["explainability"]["contraction"]
    assert v4_score.box == base_score.debug["explainability"]["box"]
    assert v4_score.stage == base_score.debug["explainability"]["stage"]
    assert v4_score.regime == base_score.debug["explainability"]["regime"]
    assert v4_score.climax == base_score.debug["explainability"]["climax"]
    assert v4_score.feature_flags == base_score.debug["explainability"]["feature_flags"]
    assert v4_score.warning_flags == base_score.debug["explainability"]["warning_flags"]
    assert set(base_score.debug["explainability"]["sub_tags"]).issubset(
        set(v4_score.sub_tags)
    )
    assert "Tight closes" in v4_score.sub_tags
    assert v4_score.debug["explainability"]["debug"]["score_source"] == "regime_weighted_v4"
    assert v4_score.debug["explainability"]["debug"]["regime_weight_key"] == "bull_trend"
    assert (
        v4_score.debug["explainability"]["debug"]["v4_components"]["trend_quality"]
        == base_score.trend_score
    )


def test_build_technical_score_accepts_v4_wrapper() -> None:
    base_score = score_from_feature_result(
        calculate_technical_features(_synthetic_uptrend(), ticker="TEST")
    )
    v4_score = technical_score_v4_from_base_score(base_score)

    model = build_technical_score(run_id=3, score=v4_score)

    assert model.dual_score is not None
    assert float(model.dual_score) == v4_score.final_v4_score
    assert model.classification == v4_score.final_v4_classification
    assert model.action_bias == v4_score.final_v4_action
    assert model.v4_debug_json == v4_score.debug["explainability"]


def test_v4_classification_keeps_danger_ahead_of_buyable_setups() -> None:
    score = _base_score(
        classification="No trade",
        debug=_debug(
            derived={"failed_breakout": True, "rsi": 80.0},
            explainability={
                "box": {"box_breakout": True, "breakout_quality_score": 9.0},
                "contraction": {"vcp_detected": True, "vcp_score": 8.5},
                "stage": {"stage": "Stage 2", "stage_tags": []},
                "climax": {"climax_risk_score": 8.0, "reasons": ["vertical_move"]},
            },
        ),
    )

    v4_score = technical_score_v4_from_base_score(score)

    assert v4_score.final_v4_classification == "Failed breakout"
    assert v4_score.final_v4_action == "Avoid"
    assert "failed_breakout" in v4_score.warning_flags
    assert {"Stage 2", "VCP", "Darvas box", "Climax risk"}.issubset(
        set(v4_score.sub_tags)
    )
    assert v4_score.debug["explainability"]["debug"]["classification_reasons"] == [
        "failed_breakout"
    ]


def test_v4_classification_promotes_climax_reversal_risk() -> None:
    score = _base_score(
        classification="Fresh breakout",
        debug=_debug(
            derived={"rsi": 81.0},
            explainability={
                "stage": {"stage": "Stage 2", "stage_tags": []},
                "climax": {
                    "climax_risk_score": 7.2,
                    "momentum_crash_risk": True,
                    "reasons": ["vertical_move", "volume_climax"],
                },
            },
        ),
    )

    v4_score = technical_score_v4_from_base_score(score)

    assert v4_score.final_v4_classification == "Climax reversal risk"
    assert v4_score.final_v4_action == "Avoid / reversal risk"
    assert "climax_reversal_risk" in v4_score.warning_flags
    assert "Climax risk" in v4_score.sub_tags


def test_v4_classification_promotes_tight_base_breakout() -> None:
    score = _base_score(
        classification="No trade",
        debug=_debug(
            explainability={
                "box": {
                    "box_breakout": True,
                    "donchian_20_breakout": True,
                    "breakout_quality_score": 8.1,
                },
                "stage": {"stage": "Stage 2", "stage_tags": []},
            },
        ),
    )

    v4_score = technical_score_v4_from_base_score(score)

    assert v4_score.final_v4_classification == "Tight base breakout"
    assert v4_score.final_v4_action == "Breakout candidate, confirm R/R"
    assert {"Darvas box", "Donchian breakout", "Stage 2"}.issubset(
        set(v4_score.sub_tags)
    )


def test_v4_classification_promotes_stage_two_vcp_setup() -> None:
    score = _base_score(
        classification="No trade",
        trend_score=7.2,
        debug=_debug(
            explainability={
                "contraction": {
                    "vcp_detected": True,
                    "vcp_score": 7.4,
                    "volume_dry_up_quality": 7.5,
                    "tight_close_count_5": 4,
                },
                "stage": {"stage": "Stage 2", "stage_tags": []},
            },
        ),
    )

    v4_score = technical_score_v4_from_base_score(score)

    assert v4_score.final_v4_classification == "Volatility contraction setup"
    assert v4_score.final_v4_action == "Setup candidate, wait for trigger"
    assert {"VCP", "Stage 2", "Volume dry-up", "Tight closes"}.issubset(
        set(v4_score.sub_tags)
    )


def test_v4_stage_gate_blocks_buyable_vcp_outside_stage_two() -> None:
    score = _base_score(
        classification="No trade",
        trend_score=7.2,
        debug=_debug(
            explainability={
                "contraction": {"vcp_detected": True, "vcp_score": 7.4},
                "stage": {"stage": "Stage 4", "stage_tags": ["stage_4_downtrend"]},
            },
        ),
    )

    v4_score = technical_score_v4_from_base_score(score)

    assert v4_score.final_v4_classification == "Filtered pullback"
    assert v4_score.final_v4_action == "Good chart, stage gate failed"
    assert "stage_gate_blocked" in v4_score.warning_flags
    assert "stage_4_downtrend" in v4_score.warning_flags
    assert v4_score.debug["explainability"]["debug"]["stage_gate"] == {
        "checked": True,
        "required": True,
        "passed": False,
        "stage": "Stage 4",
        "stage_tags": ["stage_4_downtrend"],
        "blocked_classification": "Volatility contraction setup",
    }


def test_v4_stage_gate_blocks_tight_base_breakout_as_filtered_momentum() -> None:
    score = _base_score(
        classification="No trade",
        debug=_debug(
            explainability={
                "box": {"box_breakout": True, "breakout_quality_score": 8.1},
                "stage": {"stage": "Stage 3", "stage_tags": ["stage_3_distribution"]},
            },
        ),
    )

    v4_score = technical_score_v4_from_base_score(score)

    assert v4_score.final_v4_classification == "Filtered momentum"
    assert v4_score.final_v4_action == "Momentum, stage gate failed"
    assert "stage_gate_blocked" in v4_score.warning_flags
    assert "stage_3_distribution" in v4_score.warning_flags


def test_v4_stage_gate_allows_configured_stage_transition() -> None:
    score = _base_score(
        classification="Clean bull pullback",
        debug=_debug(
            explainability={
                "stage": {
                    "stage": "Stage 1",
                    "stage_tags": ["stage_1_to_2_transition"],
                },
            },
        ),
    )

    v4_score = technical_score_v4_from_base_score(score)

    assert v4_score.final_v4_classification == "Clean bull pullback"
    assert v4_score.debug["explainability"]["debug"]["stage_gate"]["passed"] is True


def test_v4_stage_gate_blocks_transition_when_config_disallows_it() -> None:
    score = _base_score(
        classification="Clean bull pullback",
        debug=_debug(
            explainability={
                "stage": {
                    "stage": "Stage 1",
                    "stage_tags": ["stage_1_to_2_transition"],
                },
            },
        ),
    )

    v4_score = technical_score_v4_from_base_score(
        score,
        {"stage_analysis": {"allow_stage_1_to_2_transition": False}},
    )

    assert v4_score.final_v4_classification == "Filtered pullback"
    assert "stage_gate_blocked" in v4_score.warning_flags


def test_v4_stage_gate_keeps_danger_classification_priority() -> None:
    score = _base_score(
        classification="Failed breakout",
        debug=_debug(
            derived={"failed_breakout": True},
            explainability={
                "stage": {"stage": "Stage 4", "stage_tags": ["stage_4_downtrend"]},
            },
        ),
    )

    v4_score = technical_score_v4_from_base_score(score)

    assert v4_score.final_v4_classification == "Failed breakout"
    assert v4_score.final_v4_action == "Avoid"
    assert "stage_4_downtrend" in v4_score.warning_flags


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


def _base_score(
    *,
    classification: str,
    trend_score: float = 7.0,
    debug: dict | None = None,
) -> PineReplicaScore:
    return PineReplicaScore(
        ticker="TEST",
        local_trend_score=trend_score,
        trend_score=trend_score,
        momentum_score=6.8,
        setup_score=5.5,
        risk_score=2.5,
        market_score=6.0,
        relative_strength_score=6.5,
        sector_relative_strength_score=6.0,
        combined_relative_strength_score=6.3,
        htf_score=6.0,
        dual_score=6.6,
        classification=classification,
        action_bias="No clear trade",
        pullback_health="Mixed",
        suggested_stop=95.0,
        suggested_target=120.0,
        reward_risk=2.5,
        entry_risk_pct=5.0,
        insufficient_data=False,
        missing_data={},
        debug=debug or _debug(),
        technical_confidence="high",
        data_quality_score=10.0,
        warning_flags=(),
    )


def _debug(
    *,
    derived: dict | None = None,
    explainability: dict | None = None,
) -> dict:
    default_explainability = {
        "engine_version": "4.0.0",
        "data_readiness": {},
        "adaptive": {},
        "contraction": {},
        "box": {},
        "stage": {},
        "regime": {},
        "leadership": None,
        "climax": {},
        "feature_flags": [],
        "warning_flags": [],
        "sub_tags": [],
        "debug": {},
    }
    return {
        "derived": derived or {},
        "explainability": {
            **default_explainability,
            **(explainability or {}),
        },
    }
