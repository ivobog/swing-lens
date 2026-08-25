from decimal import Decimal

from app.models.tables import CombinedResult, TechnicalScore
from app.services.technical_display_fields import (
    technical_score_display_fields,
    technical_v4_detail_fields,
    technical_v4_summary_fields,
)


def test_score_display_keeps_v4_active_when_v5_is_shadow() -> None:
    score = TechnicalScore(
        run_id=1,
        ticker="MSFT",
        dual_score=Decimal("8.44"),
        classification="Clean bull pullback",
        technical_engine_version="4.0.0",
        technical_composite_score=Decimal("7.51"),
        technical_strength_score=Decimal("7.6"),
        setup_quality_score=Decimal("7.0"),
        entry_quality_score=Decimal("7.7"),
        v5_debug_json={
            "classification": "Volatility contraction setup",
            "rollout": {"mode": "shadow"},
        },
    )
    combined = CombinedResult(
        run_id=1,
        ticker="MSFT",
        dual_score=Decimal("8.44"),
        technical_classification="Clean bull pullback",
    )

    display = technical_score_display_fields(score, combined)

    assert display["active"] == {
        "version": "V4",
        "role": "ACTIVE",
        "score": Decimal("8.44"),
        "classification": "Clean bull pullback",
        "delta": None,
        "strength": None,
        "setup_quality": None,
        "entry_quality": None,
        "danger_state": "",
        "setup_type": "",
        "confidence_adjusted_score": None,
    }
    assert display["comparison"]["version"] == "V5"
    assert display["comparison"]["role"] == "SHADOW"
    assert display["comparison"]["score"] == Decimal("7.51")
    assert display["comparison"]["classification"] == "Volatility contraction setup"
    assert display["comparison"]["delta"] == -0.93
    assert display["feeds_combined_decision"] == "V4"
    assert display["active"]["score"] == combined.dual_score == score.dual_score


def test_score_display_reverses_hierarchy_when_v5_is_active() -> None:
    score = TechnicalScore(
        run_id=1,
        ticker="MSFT",
        dual_score=Decimal("8.12"),
        classification="Volatility contraction setup",
        technical_engine_version="5.0.0",
        technical_composite_score=Decimal("8.12"),
        technical_strength_score=Decimal("8.2"),
        setup_quality_score=Decimal("8.0"),
        entry_quality_score=Decimal("8.1"),
        v4_debug_json={
            "final_v4_score": 8.44,
            "final_v4_classification": "Clean bull pullback",
        },
        v5_debug_json={
            "classification": "Volatility contraction setup",
            "rollout": {"mode": "active"},
        },
    )
    combined = CombinedResult(
        run_id=1,
        ticker="MSFT",
        dual_score=Decimal("8.12"),
        technical_classification="Volatility contraction setup",
    )

    display = technical_score_display_fields(score, combined)

    assert display["active"]["version"] == "V5"
    assert display["active"]["role"] == "ACTIVE"
    assert display["active"]["score"] == combined.dual_score == score.dual_score
    assert display["comparison"]["version"] == "V4"
    assert display["comparison"]["role"] == "LEGACY"
    assert display["comparison"]["score"] == 8.44
    assert display["comparison"]["classification"] == "Clean bull pullback"
    assert display["feeds_combined_decision"] == "V5"


def test_score_display_does_not_treat_non_null_v5_columns_as_active() -> None:
    score = TechnicalScore(
        run_id=1,
        ticker="MSFT",
        dual_score=Decimal("8.44"),
        classification="Clean bull pullback",
        technical_engine_version="4.0.0",
        technical_composite_score=Decimal("7.51"),
        v5_debug_json={"classification": "Volatility contraction setup"},
    )

    display = technical_score_display_fields(score)

    assert display["active"]["version"] == "V4"
    assert display["comparison"]["role"] == "SHADOW"
    assert display["v5_rollout_mode"] == "shadow"


def test_score_display_historical_v4_only_has_no_comparison() -> None:
    score = TechnicalScore(
        run_id=1,
        ticker="MSFT",
        dual_score=Decimal("8.44"),
        classification="Clean bull pullback",
        technical_engine_version="4.0.0",
    )

    display = technical_score_display_fields(score)

    assert display["active"]["version"] == "V4"
    assert display["active"]["score"] == Decimal("8.44")
    assert display["comparison"] is None
    assert display["v5_score"] is None
    assert display["v5_rollout_mode"] == ""


def test_technical_v4_detail_fields_prefer_explicit_columns_and_use_v4_debug() -> None:
    score = TechnicalScore(
        run_id=1,
        ticker="MSFT",
        technical_engine_version="4.0.0",
        stage="Stage 2",
        market_regime="Bull trend",
        leadership_score=Decimal("9.2"),
        vcp_score=Decimal("7.4"),
        breakout_quality_score=Decimal("8.8"),
        climax_risk_score=Decimal("2.2"),
        feature_flags_json=["vcp_detected", "stage_2"],
        warning_flags_json=["market_risk_off"],
        sub_tags_json=["VCP", "Stage 2"],
        v4_debug_json={
            "engine_version": "4.0.0",
            "box": {
                "box_breakout": True,
                "box_width_pct": 6.2,
                "box_age": 20,
                "donchian_20_breakout": True,
            },
            "contraction": {"vcp_detected": True},
            "adaptive": {"atr_percentile_252": 42.5},
            "stage": {"stage": "Stage 1"},
            "regime": {"regime": "Choppy"},
        },
    )

    details = technical_v4_detail_fields(score)

    assert details["technical_version"] == "4.0.0"
    assert details["stage"] == "Stage 2"
    assert details["market_regime"] == "Bull trend"
    assert details["leadership_score"] == Decimal("9.2")
    assert details["vcp_score"] == Decimal("7.4")
    assert details["vcp_detected"] is True
    assert details["box_breakout"] is True
    assert details["box_width_pct"] == 6.2
    assert details["box_age"] == 20
    assert details["donchian_20_breakout"] is True
    assert details["atr_percentile_252"] == 42.5
    assert details["feature_flags"] == "vcp_detected; stage_2"
    assert details["warning_flags"] == "market_risk_off"
    assert details["sub_tags"] == "VCP; Stage 2"


def test_technical_v4_summary_fields_use_detail_fields() -> None:
    score = TechnicalScore(
        run_id=1,
        ticker="MSFT",
        technical_engine_version="4.0.0",
        stage="Stage 2",
        market_regime="Bull trend",
        leadership_score=Decimal("9.2"),
        vcp_score=Decimal("7.4"),
        climax_risk_score=Decimal("2.2"),
        feature_flags_json=["vcp_detected"],
        warning_flags_json=["market_risk_off"],
        sub_tags_json=["VCP"],
    )

    summary = technical_v4_summary_fields(score)

    assert summary == {
        "technical_version": "4.0.0",
        "technical_stage": "Stage 2",
        "technical_regime": "Bull trend",
        "technical_leadership_score": Decimal("9.2"),
        "technical_vcp_score": Decimal("7.4"),
        "technical_climax_risk_score": Decimal("2.2"),
        "technical_flags": "vcp_detected",
        "technical_warnings": "market_risk_off",
        "technical_sub_tags": "VCP",
    }
