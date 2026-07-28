from decimal import Decimal

from app.models.tables import TechnicalScore
from app.services.ranking_profile_components import (
    calculate_technical_profile_score,
    extract_technical_components,
)


def test_extract_technical_components_prefers_persisted_v4_fields() -> None:
    technical = _technical(
        trend_score="8.1",
        setup_score="6.0",
        vcp_score="7.4",
        box_tightness_score="8.1",
        breakout_quality_score="8.8",
        climax_risk_score="2.2",
        risk_score="2.1",
    )
    technical.debug_json = {
        "explainability": {
            "contraction": {"vcp_score": 1.0},
            "box": {"breakout_quality_score": 1.0, "box_tightness_score": 1.0},
            "climax": {"climax_risk_score": 9.0},
        }
    }

    components = extract_technical_components(technical)

    assert components["trend_quality"] == 8.1
    assert components["vcp_quality"] == 7.4
    assert components["box_tightness"] == 8.1
    assert components["breakout_quality"] == 8.8
    assert components["setup_quality"] == 8.8
    assert components["risk_control"] == 7.8


def test_extract_technical_components_falls_back_to_debug_json() -> None:
    technical = _technical(setup_score="6.0", risk_score="3.0")
    technical.debug_json = {
        "derived": {"rs_new_high": True},
        "explainability": {
            "contraction": {"vcp_score": 7.5, "volume_dry_up_quality": 8.0},
            "box": {"breakout_quality_score": 8.2, "box_tightness_score": 7.0},
            "climax": {"climax_risk_score": 2.0},
        },
    }

    components = extract_technical_components(technical)

    assert components["breakout_quality"] == 8.2
    assert components["vcp_quality"] == 7.5
    assert components["breakout_or_vcp_quality"] == 8.2
    assert components["momentum_strength"] == 8.275
    assert components["momentum_health"] == 8.5


def test_extract_technical_components_handles_missing_debug_json() -> None:
    components = extract_technical_components(
        _technical(
            trend_score="7.0",
            setup_score="6.5",
            momentum_score="6.8",
            relative_strength_score="6.3",
            risk_score="4.0",
        )
    )

    assert components["trend_quality"] == 7.0
    assert components["setup_quality"] == 6.5
    assert components["momentum_health"] == 4.0
    assert components["risk_control"] == 6.0


def test_extract_technical_components_handles_missing_technical_score() -> None:
    assert extract_technical_components(None) == {}


def test_calculate_technical_profile_score_returns_weighted_component_score() -> None:
    score = calculate_technical_profile_score(
        {"trend_quality": 8.0, "risk_control": 6.0},
        {"trend_quality": 0.75, "risk_control": 0.25},
    )

    assert score == 7.5


def test_calculate_technical_profile_score_returns_none_without_components() -> None:
    assert calculate_technical_profile_score({}, {"trend_quality": 1.0}) is None


def _technical(
    *,
    trend_score: str = "8.1",
    setup_score: str = "7.8",
    momentum_score: str = "7.9",
    relative_strength_score: str = "8.1",
    risk_score: str = "2.1",
    vcp_score: str | None = None,
    box_tightness_score: str | None = None,
    breakout_quality_score: str | None = None,
    climax_risk_score: str | None = None,
) -> TechnicalScore:
    return TechnicalScore(
        run_id=1,
        ticker="TEST",
        trend_score=Decimal(trend_score),
        setup_score=Decimal(setup_score),
        momentum_score=Decimal(momentum_score),
        risk_score=Decimal(risk_score),
        market_score=Decimal("8.2"),
        combined_relative_strength_score=Decimal(relative_strength_score),
        dual_score=Decimal("8.4"),
        classification="Prime clean pullback",
        pullback_health="Healthy",
        vcp_score=Decimal(vcp_score) if vcp_score is not None else None,
        box_tightness_score=Decimal(box_tightness_score)
        if box_tightness_score is not None
        else None,
        breakout_quality_score=Decimal(breakout_quality_score)
        if breakout_quality_score is not None
        else None,
        climax_risk_score=Decimal(climax_risk_score)
        if climax_risk_score is not None
        else None,
    )
