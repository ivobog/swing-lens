from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TechnicalStrengthResult:
    score: float
    trend_quality: float
    momentum_quality: float
    leadership_quality: float | None
    local_trend_score: float
    htf_score: float | None
    momentum_acceleration_10_63: float | None
    acceleration_quality: float | None
    missing_evidence: tuple[str, ...]
    debug: dict[str, Any]


def calculate_technical_strength(
    *,
    local_trend_score: float,
    htf_score: float | None,
    htf_available: bool,
    base_momentum_score: float,
    roc10: float | None,
    roc63: float | None,
    leadership_score: float | None,
    config: dict[str, Any],
) -> TechnicalStrengthResult:
    missing: list[str] = []
    trend_config = config["trend"]
    if htf_available and htf_score is not None:
        trend = _clamp(
            local_trend_score * float(trend_config["local_weight"])
            + htf_score * float(trend_config["htf_weight"])
        )
    else:
        trend = _clamp(local_trend_score)
        missing.append("missing_htf_data")

    acceleration = None
    acceleration_quality = None
    if roc10 is not None and roc63 is not None:
        acceleration = round(float(roc10) - (10.0 / 63.0) * float(roc63), 4)
        momentum_config = config["momentum"]
        acceleration_quality = _clamp(
            5.0 + acceleration * float(momentum_config["acceleration_points_per_pct"])
        )
        momentum = _clamp(
            base_momentum_score * float(momentum_config["base_weight"])
            + acceleration_quality * float(momentum_config["acceleration_weight"])
        )
    else:
        momentum = _clamp(base_momentum_score)
        missing.append("missing_momentum_acceleration")

    leadership = None if leadership_score is None else _clamp(leadership_score)
    if leadership is None:
        missing.append("missing_leadership")
    weights = config["technical_strength"]["weights"]
    available = {"trend": trend, "momentum": momentum}
    if leadership is not None:
        available["leadership"] = leadership
    denominator = sum(float(weights[name]) for name in available)
    applied_weights = {name: round(float(weights[name]) / denominator, 8) for name in available}
    score = _clamp(sum(available[name] * applied_weights[name] for name in available))
    return TechnicalStrengthResult(
        score=score,
        trend_quality=trend,
        momentum_quality=momentum,
        leadership_quality=leadership,
        local_trend_score=_clamp(local_trend_score),
        htf_score=_optional_score(htf_score) if htf_available else None,
        momentum_acceleration_10_63=acceleration,
        acceleration_quality=acceleration_quality,
        missing_evidence=tuple(missing),
        debug={
            "configured_weights": {key: float(value) for key, value in weights.items()},
            "applied_weights": applied_weights,
            "trend_weights": {
                "local": float(trend_config["local_weight"]),
                "htf": float(trend_config["htf_weight"]),
            },
            "momentum_weights": {
                "base": float(config["momentum"]["base_weight"]),
                "acceleration": float(config["momentum"]["acceleration_weight"]),
            },
        },
    )


def _optional_score(value: float | None) -> float | None:
    return None if value is None else _clamp(value)


def _clamp(value: float) -> float:
    return max(0.0, min(10.0, round(float(value), 4)))
