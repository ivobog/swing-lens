import hashlib
from dataclasses import asdict, dataclass
from typing import Any

from app.services.entry_quality_v5 import (
    EntryQualityResult,
    calculate_entry_quality,
    calculate_execution_quality,
    detect_danger_state,
    stock_specific_base_risk,
)
from app.services.leadership_v5 import LeadershipV5Result
from app.services.pine_replica_engine import PineReplicaScore
from app.services.sector_benchmark_service import SectorBenchmarkResolution
from app.services.setup_quality_v5 import (
    SetupQualityResult,
    calculate_setup_quality,
    select_setup,
)
from app.services.technical_artifact_cache import canonical_json, config_hash
from app.services.technical_strength_v5 import (
    TechnicalStrengthResult,
    calculate_technical_strength,
)
from app.services.trigger_quality import TriggerQualityResult, calculate_trigger_quality


@dataclass(frozen=True)
class TechnicalScoreV5:
    base_score: PineReplicaScore
    engine_version: str
    config_hash: str
    technical_strength_score: float
    setup_quality_score: float
    entry_quality_score: float
    technical_composite_score: float
    confidence_adjusted_score: float
    leadership_v5_score: float | None
    residual_momentum_score: float | None
    trigger_distance_atr: float | None
    stop_distance_atr: float | None
    setup_type: str
    sector_benchmark_symbol: str | None
    stage_modifier: float
    classification: str
    action_bias: str
    technical_confidence: str
    data_quality_score: float
    warning_flags: tuple[str, ...]
    feature_flags: tuple[str, ...]
    technical_strength: TechnicalStrengthResult
    setup_quality: SetupQualityResult
    entry_quality: EntryQualityResult
    trigger_quality: TriggerQualityResult
    debug: dict[str, Any]

    @property
    def ticker(self) -> str:
        return self.base_score.ticker


def technical_score_v5_from_base_score(
    base: PineReplicaScore,
    *,
    leadership: LeadershipV5Result | None,
    sector_resolution: SectorBenchmarkResolution,
    v5_config: dict[str, Any],
    pine_config: dict[str, Any],
) -> TechnicalScoreV5:
    explainability_v4 = _dict(_dict(base.debug).get("explainability"))
    derived = _dict(_dict(base.debug).get("derived"))
    htf_available = _bool(base.missing_data.get("has_htf_data"))
    strength = calculate_technical_strength(
        local_trend_score=base.local_trend_score,
        htf_score=base.htf_score,
        htf_available=htf_available,
        base_momentum_score=base.momentum_score,
        roc10=_optional_num(derived.get("stock_roc10")),
        roc63=_optional_num(derived.get("stock_roc_medium")),
        leadership_score=leadership.leadership_score if leadership else None,
        config=v5_config,
    )
    selection = select_setup(base, explainability_v4, v5_config)
    trigger = calculate_trigger_quality(
        close=_optional_num(derived.get("close")),
        atr14=_optional_num(derived.get("atr")),
        trigger_price=selection.trigger_price,
        invalidation_price=selection.invalidation_price,
        invalidated=selection.invalidated,
        config=v5_config["trigger"],
    )
    execution = calculate_execution_quality(base, v5_config["execution"])
    setup = calculate_setup_quality(
        base=base,
        explainability=explainability_v4,
        selection=selection,
        trigger_quality=trigger.quality,
        execution_readiness=execution.score,
        config=v5_config,
    )
    danger = detect_danger_state(base, explainability_v4, v5_config["danger_caps"])
    climax_risk = _num(_dict(explainability_v4.get("climax")).get("climax_risk_score"))
    sector_available = sector_resolution.status == "RESOLVED"
    base_risk = stock_specific_base_risk(
        base,
        use_sector_evidence=sector_available,
        pine_config=pine_config,
        v5_risk_config=v5_config["risk"],
    )
    entry = calculate_entry_quality(
        base_risk=base_risk,
        climax_risk=climax_risk,
        execution=execution,
        trigger_quality=trigger.quality,
        danger_state=danger,
        config=v5_config,
    )
    regime = str(_dict(explainability_v4.get("regime")).get("regime") or "Unknown")
    regime_key = regime_weight_key(regime, _dict(explainability_v4.get("regime")))
    composite_weights = {
        key: float(value) for key, value in v5_config["composite"][regime_key].items()
    }
    composite = _clamp(
        strength.score * composite_weights["technical_strength"]
        + setup.score * composite_weights["setup_quality"]
        + entry.score * composite_weights["entry_quality"]
    )
    warnings = list(base.warning_flags)
    warnings.extend(strength.missing_evidence)
    if leadership:
        warnings.extend(f"missing_leadership_{item}" for item in leadership.missing_components)
    if sector_resolution.status != "RESOLVED":
        warnings.append(f"sector_benchmark_{sector_resolution.status.lower()}")
    if regime.strip().lower() == "unknown":
        warnings.append("unknown_market_regime")
    warnings = _unique(warnings)
    confidence = _v5_confidence(
        base.technical_confidence,
        warnings,
        sector_required=bool(v5_config["sector_benchmarks"].get("required_for_confidence", False)),
    )
    factor = float(v5_config["confidence"]["factors"].get(confidence, 0.0))
    confidence_adjusted = _clamp(5.0 + (composite - 5.0) * factor)
    classification = _classification(base, setup, danger)
    action = _action(classification, regime_key, setup.setup_type)
    residual = _optional_num(derived.get("residual_momentum_score"))
    cfg_hash = config_hash(v5_config)
    input_signature = hashlib.sha256(
        canonical_json(
            {
                "ticker": base.ticker.upper(),
                "derived": derived,
                "leadership": asdict(leadership) if leadership else None,
                "sector_resolution": asdict(sector_resolution),
                "config_hash": cfg_hash,
            }
        ).encode("utf-8")
    ).hexdigest()
    debug = _explainability(
        base=base,
        config_hash_value=cfg_hash,
        input_signature=input_signature,
        strength=strength,
        leadership=leadership,
        sector=sector_resolution,
        setup=setup,
        selection=selection,
        trigger=trigger,
        entry=entry,
        composite=composite,
        regime=regime,
        regime_key=regime_key,
        composite_weights=composite_weights,
        confidence=confidence,
        confidence_adjusted=confidence_adjusted,
        classification=classification,
        action=action,
        warnings=warnings,
        config=v5_config,
    )
    return TechnicalScoreV5(
        base_score=base,
        engine_version="5.0.0",
        config_hash=cfg_hash,
        technical_strength_score=strength.score,
        setup_quality_score=setup.score,
        entry_quality_score=entry.score,
        technical_composite_score=composite,
        confidence_adjusted_score=confidence_adjusted,
        leadership_v5_score=leadership.leadership_score if leadership else None,
        residual_momentum_score=residual,
        trigger_distance_atr=trigger.distance_atr,
        stop_distance_atr=execution.stop_distance_atr,
        setup_type=setup.setup_type,
        sector_benchmark_symbol=sector_resolution.benchmark_symbol if sector_available else None,
        stage_modifier=setup.stage_modifier,
        classification=classification,
        action_bias=action,
        technical_confidence=confidence,
        data_quality_score=base.data_quality_score,
        warning_flags=tuple(warnings),
        feature_flags=tuple(_list(explainability_v4.get("feature_flags"))),
        technical_strength=strength,
        setup_quality=setup,
        entry_quality=entry,
        trigger_quality=trigger,
        debug=debug,
    )


def regime_weight_key(regime: str, regime_payload: dict[str, Any] | None = None) -> str:
    if _bool(_dict(regime_payload).get("risk_off")):
        return "risk_off"
    normalized = regime.strip().lower()
    if normalized in {"distribution", "correction", "crash risk", "risk-off"}:
        return "risk_off"
    if normalized in {"bull trend", "bull pullback", "risk-on breakout", "bullish"}:
        return "bull_trend"
    return "choppy"


def _classification(base: PineReplicaScore, setup: SetupQualityResult, danger: str | None) -> str:
    if danger:
        return danger
    if setup.stage == "Stage 4" and setup.setup_type in {
        "pullback",
        "vcp",
        "breakout",
        "momentum_continuation",
    }:
        return (
            "Filtered momentum"
            if setup.setup_type in {"breakout", "momentum_continuation"}
            else "Filtered pullback"
        )
    mapping = {
        "breakout": "Tight base breakout",
        "vcp": "Volatility contraction setup",
        "pullback": base.classification
        if base.classification in {"Prime clean pullback", "Clean bull pullback"}
        else "Clean bull pullback",
        "momentum_continuation": "Momentum continuation",
        "extended_momentum": "Extended momentum",
        "trend_repair": "Trend repair",
        "none": "No trade",
    }
    return mapping[setup.setup_type]


def _action(classification: str, regime_key: str, setup_type: str) -> str:
    if classification in {
        "Failed breakout",
        "Climax reversal risk",
        "Blowoff top",
        "Distribution risk",
        "Late-stage extension",
    }:
        return "Avoid"
    if classification.startswith("Filtered"):
        return "Wait for trend repair"
    if setup_type == "none":
        return "No qualified setup"
    if regime_key == "risk_off":
        return "Defensive / wait for confirmation"
    if setup_type in {"vcp", "pullback", "trend_repair"}:
        return "Setup candidate, wait for trigger"
    return "Entry candidate, confirm R/R"


def _v5_confidence(base: str, warnings: list[str], *, sector_required: bool) -> str:
    if base == "error":
        return "error"
    material = [
        warning
        for warning in warnings
        if not warning.startswith("sector_benchmark_") or sector_required
    ]
    if "unknown_market_regime" in material or len(material) >= 2:
        return "low"
    if material and base == "high":
        return "normal"
    if material and base == "normal":
        return "low"
    return base if base in {"high", "normal", "low"} else "normal"


def _explainability(**values: Any) -> dict[str, Any]:
    base = values["base"]
    strength = values["strength"]
    leadership = values["leadership"]
    setup = values["setup"]
    selection = values["selection"]
    trigger = values["trigger"]
    entry = values["entry"]
    sector = values["sector"]
    return {
        "engine_version": "5.0.0",
        "base_engine_version": "3.2.0",
        "config_hash": values["config_hash_value"],
        "input_signature": values["input_signature"],
        "technical_strength": {
            "score": strength.score,
            "configured_weights": strength.debug["configured_weights"],
            "applied_weights": strength.debug["applied_weights"],
            "components": {
                "trend": strength.trend_quality,
                "momentum": strength.momentum_quality,
                "leadership": strength.leadership_quality,
            },
            "trend": {
                "local": strength.local_trend_score,
                "htf": strength.htf_score,
                "weights": strength.debug["trend_weights"],
            },
            "momentum": {
                "base": base.momentum_score,
                "roc10": _dict(base.debug.get("derived")).get("stock_roc10"),
                "roc63": _dict(base.debug.get("derived")).get("stock_roc_medium"),
                "acceleration_10_63": strength.momentum_acceleration_10_63,
                "acceleration_quality": strength.acceleration_quality,
                "weights": strength.debug["momentum_weights"],
            },
            "missing_evidence": list(strength.missing_evidence),
        },
        "leadership": asdict(leadership) if leadership else None,
        "residual_momentum": {
            key: _dict(base.debug.get("derived")).get(key)
            for key in (
                "rolling_beta_63",
                "rolling_beta_126",
                "residual_return_21",
                "residual_return_63",
                "residual_momentum_score",
            )
        },
        "sector_benchmark": {
            **asdict(sector),
            "sector_relative_strength_score": _dict(base.debug.get("derived")).get(
                "v5_sector_rs_score"
            ),
            "fallback_to_market_only": sector.status != "RESOLVED",
        },
        "setup_quality": {
            **asdict(setup),
            "selection": asdict(selection),
            "trigger": asdict(trigger),
        },
        "entry_quality": asdict(entry),
        "composite": {
            "score": values["composite"],
            "regime": values["regime"],
            "weight_key": values["regime_key"],
            "weights": values["composite_weights"],
        },
        "confidence": {
            "data_quality_score": base.data_quality_score,
            "technical_confidence": values["confidence"],
            "factor": float(values["config"]["confidence"]["factors"][values["confidence"]]),
            "confidence_adjusted_score": values["confidence_adjusted"],
        },
        "classification": values["classification"],
        "action_bias": values["action"],
        "warning_flags": values["warnings"],
        "feature_flags": _list(_dict(base.debug.get("explainability")).get("feature_flags")),
        "caps_and_modifiers": {
            "stage_modifier": setup.stage_modifier,
            "danger_cap": entry.danger_cap,
            "applied": list(entry.applied_modifiers),
        },
    }


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


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


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if value))


def _clamp(value: float) -> float:
    return max(0.0, min(10.0, round(float(value), 4)))
