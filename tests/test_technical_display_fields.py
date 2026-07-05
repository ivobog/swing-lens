from decimal import Decimal

from app.models.tables import TechnicalScore
from app.services.technical_display_fields import (
    technical_v4_detail_fields,
    technical_v4_summary_fields,
)


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
