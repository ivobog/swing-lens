from decimal import Decimal
from typing import Any

from app.models.tables import TechnicalScore

PULLBACK_HEALTH_SCORES = {
    "healthy": 10.0,
    "mixed": 5.5,
    "dangerous": 0.0,
}


def extract_technical_components(technical: TechnicalScore | None) -> dict[str, float]:
    if technical is None:
        return {}

    explainability = _explainability(technical)
    derived = _dict((technical.debug_json or {}).get("derived"))
    contraction = _dict(explainability.get("contraction"))
    box = _dict(explainability.get("box"))
    climax = _dict(explainability.get("climax"))

    trend_quality = _score(technical.trend_score)
    setup_quality = max(
        _score(technical.setup_score),
        _score(_first_present(technical.vcp_score, contraction.get("vcp_score"))),
        _score(
            _first_present(
                technical.breakout_quality_score,
                box.get("breakout_quality_score"),
            )
        ),
    )
    breakout_quality = _score(
        _first_present(technical.breakout_quality_score, box.get("breakout_quality_score"))
    )
    vcp_quality = _score(_first_present(technical.vcp_score, contraction.get("vcp_score")))
    box_tightness = _score(
        _first_present(technical.box_tightness_score, box.get("box_tightness_score"))
    )
    relative_strength = _score(technical.combined_relative_strength_score)
    momentum_strength = _clamp(
        _score(technical.momentum_score) * 0.55
        + relative_strength * 0.30
        + _bool_score(derived.get("rs_new_high")) * 0.15
    )
    climax_risk = _score(
        _first_present(technical.climax_risk_score, climax.get("climax_risk_score"))
    )
    risk_control = _clamp(10.0 - max(_score(technical.risk_score, default=5.0), climax_risk))
    pullback_health = _pullback_health_score(technical.pullback_health)

    momentum_health = _clamp(
        vcp_quality * 0.30
        + box_tightness * 0.15
        + pullback_health * 0.20
        + _score(contraction.get("volume_dry_up_quality")) * 0.15
        + _no_distribution_score(technical, explainability) * 0.20
    )
    momentum_danger = _clamp(
        _percentile_to_score(technical.extension_percentile_252)
        + climax_risk
        + _bool_score(derived.get("failed_breakout"))
        + _distribution_score(technical, explainability)
        + _overheated_rsi_score(derived.get("rsi"))
    )
    relative_strength_acceleration = _clamp(
        _positive_bool_score(derived.get("rs_roc_short")) * 0.40
        + _positive_bool_score(derived.get("rs_roc_medium")) * 0.35
        + _bool_score(derived.get("rs_new_high")) * 0.25
    )
    volume_expansion = _clamp(
        _bool_score(derived.get("bullish_volume_bar")) * 0.35
        + _bool_score(derived.get("breakout_volume_confirmed")) * 0.35
        + _bool_score(derived.get("green_beats_red")) * 0.30
    )
    trend_repair = _clamp(
        _bool_score(technical.classification == "Trend repair") * 0.35
        + trend_quality * 0.35
        + momentum_strength * 0.20
        + relative_strength * 0.10
    )

    return {
        "momentum_strength": momentum_strength,
        "momentum_health": momentum_health,
        "momentum_danger": momentum_danger,
        "trend_quality": trend_quality,
        "setup_quality": setup_quality,
        "breakout_quality": breakout_quality,
        "vcp_quality": vcp_quality,
        "box_tightness": box_tightness,
        "breakout_or_vcp_quality": max(breakout_quality, vcp_quality),
        "pullback_health": pullback_health,
        "relative_strength": relative_strength,
        "relative_strength_acceleration": relative_strength_acceleration,
        "volume_expansion": volume_expansion,
        "trend_repair": trend_repair,
        "risk_control": risk_control,
        "market_regime_alignment": _score(technical.market_score),
    }


def calculate_technical_profile_score(
    components: dict[str, float],
    component_weights: dict[str, float],
) -> float | None:
    if not components:
        return None
    total = sum(
        components.get(component, 0.0) * weight
        for component, weight in component_weights.items()
    )
    return _clamp(total)


def _explainability(technical: TechnicalScore) -> dict[str, Any]:
    if isinstance(technical.v4_debug_json, dict):
        return technical.v4_debug_json
    if isinstance(technical.debug_json, dict):
        return _dict(technical.debug_json.get("explainability"))
    return {}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _score(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    if isinstance(value, Decimal):
        value = float(value)
    try:
        return _clamp(float(value))
    except (TypeError, ValueError):
        return default


def _first_present(primary: Any, fallback: Any) -> Any:
    if primary is None or primary == "":
        return fallback
    return primary


def _bool_score(value: Any) -> float:
    return 10.0 if bool(value) else 0.0


def _positive_bool_score(value: Any) -> float:
    return 10.0 if _score(value) > 0 else 0.0


def _pullback_health_score(label: str | None) -> float:
    if not label:
        return 5.0
    return PULLBACK_HEALTH_SCORES.get(label.strip().casefold(), 5.0)


def _no_distribution_score(
    technical: TechnicalScore,
    explainability: dict[str, Any],
) -> float:
    return _clamp(10.0 - _distribution_score(technical, explainability))


def _distribution_score(
    technical: TechnicalScore,
    explainability: dict[str, Any],
) -> float:
    warning_flags = technical.warning_flags_json or []
    stage = _dict(explainability.get("stage"))
    stage_tags = stage.get("stage_tags") if isinstance(stage.get("stage_tags"), list) else []
    if technical.classification == "Distribution risk":
        return 10.0
    if "heavy_distribution" in warning_flags or "stage_3_distribution" in stage_tags:
        return 8.0
    if "distribution_risk" in warning_flags:
        return 7.0
    return 0.0


def _percentile_to_score(value: Any) -> float:
    if value is None:
        return 0.0
    return _clamp(_score(value) / 10.0)


def _overheated_rsi_score(value: Any) -> float:
    rsi = _score(value)
    if rsi >= 80.0:
        return 10.0
    if rsi >= 75.0:
        return 7.0
    return 0.0


def _clamp(value: float) -> float:
    return max(0.0, min(10.0, round(float(value), 4)))
