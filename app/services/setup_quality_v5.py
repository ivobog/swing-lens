from dataclasses import dataclass
from typing import Any

from app.services.pine_replica_engine import PineReplicaScore


@dataclass(frozen=True)
class SetupSelection:
    setup_type: str
    reasons: tuple[str, ...]
    trigger_price: float | None
    invalidation_price: float | None
    invalidated: bool


@dataclass(frozen=True)
class SetupQualityResult:
    score_before_stage: float
    score: float
    setup_type: str
    selection_reasons: tuple[str, ...]
    components: dict[str, float]
    weights: dict[str, float]
    stage: str
    stage_modifier: float
    stage_reason: str


def select_setup(
    base: PineReplicaScore,
    explainability: dict[str, Any],
    config: dict[str, Any],
) -> SetupSelection:
    derived = _dict(base.debug.get("derived"))
    contraction = _dict(explainability.get("contraction"))
    box = _dict(explainability.get("box"))
    selection = _dict(config["setup_quality"].get("selection"))
    invalidated = _bool(derived.get("failed_breakout")) or _bool(box.get("box_failure"))
    trigger = _first_number(
        box.get("box_high"), box.get("donchian_20_high"), derived.get("previous_resistance")
    )
    invalidation = _first_number(box.get("box_low"), base.suggested_stop)

    if (_bool(box.get("box_breakout")) or _bool(derived.get("fresh_breakout"))) and _num(
        box.get("breakout_quality_score")
    ) >= float(selection.get("breakout_min_score", 7.0)):
        return SetupSelection(
            "breakout", ("qualified_fresh_or_tight_breakout",), trigger, invalidation, invalidated
        )
    if _bool(contraction.get("vcp_detected")) and _num(contraction.get("vcp_score")) >= float(
        selection.get("vcp_min_score", 7.0)
    ):
        return SetupSelection(
            "vcp", ("qualified_volatility_contraction",), trigger, invalidation, invalidated
        )
    if base.classification in {"Prime clean pullback", "Clean bull pullback"} or (
        _bool(derived.get("had_pullback"))
        and _bool(derived.get("held_near_support"))
        and _bool(derived.get("not_too_deep"))
    ):
        return SetupSelection(
            "pullback",
            ("qualified_support_holding_pullback",),
            _first_number(derived.get("ema20"), derived.get("ema10")),
            base.suggested_stop,
            invalidated,
        )
    if base.classification == "Momentum continuation":
        return SetupSelection(
            "momentum_continuation",
            ("base_momentum_continuation",),
            _first_number(derived.get("ema10"), derived.get("close")),
            base.suggested_stop,
            invalidated,
        )
    if base.classification in {"Extended momentum", "Overheated momentum"}:
        return SetupSelection(
            "extended_momentum",
            ("base_extended_momentum",),
            trigger,
            base.suggested_stop,
            invalidated,
        )
    if base.classification == "Trend repair":
        return SetupSelection(
            "trend_repair",
            ("base_trend_repair",),
            _first_number(derived.get("sma50"), derived.get("ema20")),
            base.suggested_stop,
            invalidated,
        )
    return SetupSelection("none", ("no_qualified_setup",), None, base.suggested_stop, invalidated)


def calculate_setup_quality(
    *,
    base: PineReplicaScore,
    explainability: dict[str, Any],
    selection: SetupSelection,
    trigger_quality: float,
    execution_readiness: float,
    config: dict[str, Any],
) -> SetupQualityResult:
    derived = _dict(base.debug.get("derived"))
    contraction = _dict(explainability.get("contraction"))
    box = _dict(explainability.get("box"))
    setup_config = config["setup_quality"]
    trend_confirmation = _clamp(base.trend_score)
    volume_confirmation = _volume_confirmation(derived, explainability)
    setup_type = selection.setup_type
    if setup_type == "pullback":
        components = {
            "primary": _clamp(base.setup_score),
            "volume_confirmation": _pullback_volume_confirmation(derived, contraction),
            "trigger_readiness": trigger_quality,
        }
    elif setup_type == "vcp":
        components = {
            "primary": _clamp(_num(contraction.get("vcp_score"))),
            "trend_confirmation": trend_confirmation,
            "trigger_readiness": trigger_quality,
        }
    elif setup_type == "breakout":
        components = {
            "primary": _clamp(_num(box.get("breakout_quality_score"))),
            "volume_confirmation": volume_confirmation,
            "base_tightness": _clamp(_num(box.get("box_tightness_score"))),
        }
    elif setup_type == "momentum_continuation":
        components = {
            "primary": _clamp(base.momentum_score),
            "trend_confirmation": trend_confirmation,
            "execution_readiness": _clamp(execution_readiness),
        }
    elif setup_type == "extended_momentum":
        components = {
            "primary": _clamp(base.momentum_score),
            "execution_readiness": _clamp(execution_readiness),
        }
    elif setup_type == "trend_repair":
        components = {
            "primary": _clamp(base.setup_score),
            "trend_confirmation": trend_confirmation,
        }
    else:
        components = {"primary": 0.0}

    weights = _weights_for(setup_type, setup_config, components)
    before_stage = _clamp(sum(components[name] * weights[name] for name in components))
    stage = str(_dict(explainability.get("stage")).get("stage") or "Unknown")
    stage_tags = _list(_dict(explainability.get("stage")).get("stage_tags"))
    modifier, stage_reason = stage_modifier(stage, stage_tags, config["stage"])
    score = _clamp(before_stage + modifier)
    return SetupQualityResult(
        score_before_stage=before_stage,
        score=score,
        setup_type=setup_type,
        selection_reasons=selection.reasons,
        components=components,
        weights=weights,
        stage=stage,
        stage_modifier=modifier,
        stage_reason=stage_reason,
    )


def stage_modifier(stage: str, tags: list[str], config: dict[str, Any]) -> tuple[float, str]:
    modifiers = config["modifiers"]
    if "stage_1_to_2_transition" in tags:
        return float(modifiers["stage_1_to_2"]), "stage_1_to_2_transition"
    key = stage.strip().lower().replace(" ", "_")
    if key not in modifiers:
        key = "unknown"
    return float(modifiers[key]), key


def _weights_for(
    setup_type: str, config: dict[str, Any], components: dict[str, float]
) -> dict[str, float]:
    configured = config.get(setup_type)
    if not isinstance(configured, dict):
        return {name: 1.0 / len(components) for name in components}
    weights = {name: float(configured.get(name, 0.0)) for name in components}
    total = sum(weights.values())
    return (
        {name: round(value / total, 8) for name, value in weights.items()}
        if total
        else {name: 1.0 / len(components) for name in components}
    )


def _volume_confirmation(derived: dict[str, Any], explainability: dict[str, Any]) -> float:
    adaptive = _dict(explainability.get("adaptive"))
    percentile = _optional_num(adaptive.get("volume_percentile_252"))
    if percentile is not None:
        return _clamp(percentile / 10.0)
    return 10.0 if _bool(derived.get("breakout_volume_confirmed")) else 5.0


def _pullback_volume_confirmation(derived: dict[str, Any], contraction: dict[str, Any]) -> float:
    evidence = [
        10.0 if _bool(derived.get("volume_dry_up")) else 0.0,
        10.0 if _bool(derived.get("red_vol_declining")) else 0.0,
        _clamp(_num(contraction.get("volume_dry_up_quality"))),
    ]
    return _clamp(sum(evidence) / len(evidence))


def _first_number(*values: Any) -> float | None:
    for value in values:
        number = _optional_num(value)
        if number is not None and number > 0:
            return number
    return None


def _optional_num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _num(value: Any) -> float:
    return _optional_num(value) or 0.0


def _bool(value: Any) -> bool:
    return bool(value) if value is not None else False


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _clamp(value: float) -> float:
    return max(0.0, min(10.0, round(float(value), 4)))
