from app.services.technical_feature_flags import (
    feature_flags_from_latest,
    promote_explainability_flags,
    sub_tags_from_latest,
    warning_flags_from_latest,
)


def test_feature_flags_promote_v4_pattern_stage_and_risk_signals() -> None:
    latest = {
        "vcp_score": 7.2,
        "volume_dry_up_quality": 7.5,
        "tight_close_count_5": 4,
        "box_breakout": True,
        "donchian_55_breakout": True,
        "stage": "Stage 2",
        "stage_tags": ["stage_2_continuation", "stage_1_to_2_transition"],
        "momentum_crash_risk": True,
        "market_risk_off": True,
    }

    assert feature_flags_from_latest(latest) == [
        "vcp_detected",
        "volume_dry_up",
        "tight_closes",
        "box_breakout",
        "donchian_55_breakout",
        "stage_2",
        "stage_1_to_2_transition",
        "stage_2_continuation",
        "momentum_crash_risk",
        "market_risk_off",
    ]


def test_sub_tags_promote_human_readable_v4_signals() -> None:
    latest = {
        "vcp_detected": True,
        "volume_dry_up_quality": 7.0,
        "tight_close_count_5": 3,
        "box_breakout": True,
        "donchian_20_breakout": True,
        "stage": "Stage 2",
        "stage_tags": ["stage_2_continuation", "stage_1_to_2_transition"],
        "market_risk_off": True,
        "climax_risk_score": 7.1,
    }

    assert sub_tags_from_latest(latest, data_readiness={"confidence": "low"}) == [
        "VCP",
        "Darvas box",
        "Donchian breakout",
        "Stage 2",
        "Stage 2 continuation",
        "Stage 1-to-2 transition",
        "Volume dry-up",
        "Tight closes",
        "Low confidence",
        "Market risk",
        "Climax risk",
    ]


def test_warning_flags_promote_stage_confidence_and_risk_signals() -> None:
    latest = {
        "box_failure": True,
        "stage": "Stage 4",
        "climax_risk_score": 7.0,
    }

    assert warning_flags_from_latest(
        latest=latest,
        derived={"market_risk_off": True},
        warning_flags=["missing_market_data"],
        data_readiness={"confidence": "low"},
    ) == [
        "missing_market_data",
        "box_failure",
        "market_risk_off",
        "climax_reversal_risk",
        "stage_4_downtrend",
        "low_technical_confidence",
    ]


def test_promote_explainability_flags_preserves_existing_values() -> None:
    explainability = {
        "data_readiness": {"confidence": "low"},
        "contraction": {
            "vcp_score": 7.5,
            "volume_dry_up_quality": 7.2,
            "tight_close_count_5": 4,
        },
        "box": {"donchian_20_breakout": True},
        "stage": {
            "stage": "Stage 2",
            "stage_tags": ["stage_2_continuation"],
        },
        "regime": {"risk_off": True},
        "climax": {"climax_risk_score": 7.4},
    }

    promoted = promote_explainability_flags(
        explainability,
        feature_flags=["rs_leader"],
        warning_flags=["missing_benchmark_data"],
        sub_tags=["RS leader"],
    )

    assert promoted["feature_flags"] == [
        "rs_leader",
        "vcp_detected",
        "volume_dry_up",
        "tight_closes",
        "donchian_20_breakout",
        "stage_2",
        "stage_2_continuation",
        "market_risk_off",
    ]
    assert promoted["warning_flags"] == [
        "missing_benchmark_data",
        "market_risk_off",
        "climax_reversal_risk",
        "low_technical_confidence",
    ]
    assert promoted["sub_tags"] == [
        "RS leader",
        "VCP",
        "Donchian breakout",
        "Stage 2",
        "Stage 2 continuation",
        "Volume dry-up",
        "Tight closes",
        "Low confidence",
        "Market risk",
        "Climax risk",
    ]
