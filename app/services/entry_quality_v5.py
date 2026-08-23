from dataclasses import dataclass
from typing import Any

from app.services.pine_replica_engine import PineReplicaScore

DANGER_PRIORITY = (
    "Failed breakout",
    "Climax reversal risk",
    "Blowoff top",
    "Distribution risk",
    "Late-stage extension",
)


@dataclass(frozen=True)
class ExecutionQualityResult:
    score: float
    reward_risk_quality: float
    stop_geometry_quality: float
    liquidity_quality: float
    stop_validity_quality: float
    stop_distance_atr: float | None
    stop_source: str
    target_source: str
    reward_risk: float | None


@dataclass(frozen=True)
class EntryQualityResult:
    score_before_cap: float
    score: float
    base_risk: float
    climax_risk: float
    combined_risk: float
    risk_control: float
    execution: ExecutionQualityResult
    trigger_quality: float
    danger_state: str | None
    danger_cap: float | None
    applied_modifiers: tuple[str, ...]


def stock_specific_base_risk(
    base: PineReplicaScore,
    *,
    use_sector_evidence: bool,
    pine_config: dict[str, Any],
    v5_risk_config: dict[str, Any] | None = None,
) -> float:
    derived = _dict(base.debug.get("derived"))
    risk = {
        "extensionWarnPct": 8.0,
        "extensionDangerPct": 15.0,
        "atrWarnPct": 6.0,
        "atrDangerPct": 10.0,
        **_dict(pine_config.get("risk")),
    }
    market_rs = {"sectorMinScore": 5.0, **_dict(pine_config.get("market_rs"))}
    v5_risk_config = _dict(v5_risk_config)
    thresholds = {
        "rsi_warn": 75.0,
        "rsi_danger": 80.0,
        "distribution_count": 3.0,
        **_dict(v5_risk_config.get("stock_specific_thresholds")),
    }
    points = {
        "extension_warn": 1.0,
        "extension_danger": 1.5,
        "rsi_warn": 0.8,
        "rsi_danger": 0.7,
        "near_resistance": 0.7,
        "heavy_red": 1.5,
        "distribution": 1.5,
        "failed_breakout": 2.0,
        "gap_exhaustion": 1.5,
        "liquidity_warning": 0.8,
        "atr_warn": 0.7,
        "atr_danger": 1.0,
        "rs_weak": 1.0,
        "rs_neutral": 0.4,
        "sector_weak": 0.7,
        "htf_weak": 1.0,
        "htf_neutral": 0.3,
        "heavy_mid_ma_break": 1.0,
        **_dict(v5_risk_config.get("stock_specific_points")),
    }
    score = 0.0
    extension = _num(derived.get("extension_mid_pct"))
    rsi = _num(derived.get("rsi"))
    atr_pct = _num(derived.get("atr_pct"))
    score += float(points["extension_warn"]) if extension > float(risk["extensionWarnPct"]) else 0.0
    score += (
        float(points["extension_danger"]) if extension > float(risk["extensionDangerPct"]) else 0.0
    )
    score += float(points["rsi_warn"]) if rsi > float(thresholds["rsi_warn"]) else 0.0
    score += float(points["rsi_danger"]) if rsi > float(thresholds["rsi_danger"]) else 0.0
    score += (
        float(points["near_resistance"])
        if _bool(derived.get("near_resistance")) and not _bool(derived.get("fresh_breakout"))
        else 0.0
    )
    score += float(points["heavy_red"]) if _bool(derived.get("heavy_red_now")) else 0.0
    score += (
        float(points["distribution"])
        if _num(derived.get("distribution_count")) >= float(thresholds["distribution_count"])
        else 0.0
    )
    score += float(points["failed_breakout"]) if _bool(derived.get("failed_breakout")) else 0.0
    score += float(points["gap_exhaustion"]) if _bool(derived.get("gap_exhaustion")) else 0.0
    score += float(points["liquidity_warning"]) if _bool(derived.get("liquidity_warning")) else 0.0
    score += float(points["atr_warn"]) if atr_pct > float(risk["atrWarnPct"]) else 0.0
    score += float(points["atr_danger"]) if atr_pct > float(risk["atrDangerPct"]) else 0.0
    rs_status = str(derived.get("relative_strength_status") or "")
    score += (
        float(points["rs_weak"])
        if rs_status == "Weak"
        else float(points["rs_neutral"])
        if rs_status == "Neutral"
        else 0.0
    )
    if use_sector_evidence and _num(derived.get("v5_sector_rs_score")) < float(
        market_rs["sectorMinScore"]
    ):
        score += float(points["sector_weak"])
    htf_status = str(derived.get("htf_status") or "")
    score += (
        float(points["htf_weak"])
        if htf_status == "Weak"
        else float(points["htf_neutral"])
        if htf_status == "Neutral"
        else 0.0
    )
    score += (
        float(points["heavy_mid_ma_break"]) if _bool(derived.get("heavy_mid_ma_break")) else 0.0
    )
    return _clamp(score)


def combine_risk(base_risk: float, climax_risk: float, secondary_weight: float) -> float:
    return _clamp(
        max(base_risk, climax_risk) + float(secondary_weight) * min(base_risk, climax_risk)
    )


def calculate_execution_quality(
    base: PineReplicaScore,
    config: dict[str, Any],
) -> ExecutionQualityResult:
    derived = _dict(base.debug.get("derived"))
    atr = _optional_num(derived.get("atr"))
    close = _optional_num(derived.get("close"))
    stop = _optional_num(base.suggested_stop)
    distance_atr = None
    if close is not None and stop is not None and atr is not None and atr > 0:
        distance_atr = round((close - stop) / atr, 4)
    stop_geometry = _stop_geometry_quality(distance_atr, config)
    reward_risk = _optional_num(base.reward_risk)
    rr_quality = _clamp((reward_risk or 0.0) / float(config["reward_risk_target"]) * 10.0)
    target_source = str(derived.get("target_source") or "R_MULTIPLE_FALLBACK")
    if target_source == "R_MULTIPLE_FALLBACK":
        rr_quality = _clamp(rr_quality * float(config["fallback_target_discount"]))
    liquidity_quality = 2.0 if _bool(derived.get("liquidity_warning")) else 10.0
    stop_validity = 10.0 if stop is not None and close is not None and stop < close else 0.0
    weights = config["weights"]
    score = _clamp(
        rr_quality * float(weights["reward_risk"])
        + stop_geometry * float(weights["stop_geometry"])
        + liquidity_quality * float(weights["liquidity"])
        + stop_validity * float(weights["stop_validity"])
    )
    return ExecutionQualityResult(
        score=score,
        reward_risk_quality=rr_quality,
        stop_geometry_quality=stop_geometry,
        liquidity_quality=liquidity_quality,
        stop_validity_quality=stop_validity,
        stop_distance_atr=distance_atr,
        stop_source=str(derived.get("stop_source") or "UNKNOWN"),
        target_source=target_source,
        reward_risk=reward_risk,
    )


def calculate_entry_quality(
    *,
    base_risk: float,
    climax_risk: float,
    execution: ExecutionQualityResult,
    trigger_quality: float,
    danger_state: str | None,
    config: dict[str, Any],
) -> EntryQualityResult:
    combined = combine_risk(base_risk, climax_risk, config["risk"]["secondary_risk_weight"])
    risk_control = _clamp(10.0 - combined)
    weights = config["entry_quality"]["weights"]
    before_cap = _clamp(
        risk_control * float(weights["risk_control"])
        + execution.score * float(weights["execution"])
        + trigger_quality * float(weights["trigger"])
    )
    cap = _danger_cap(danger_state, config)
    score = min(before_cap, cap) if cap is not None else before_cap
    modifiers = (
        (f"danger_cap:{danger_state}:{cap}",) if cap is not None and score < before_cap else ()
    )
    return EntryQualityResult(
        score_before_cap=before_cap,
        score=_clamp(score),
        base_risk=_clamp(base_risk),
        climax_risk=_clamp(climax_risk),
        combined_risk=combined,
        risk_control=risk_control,
        execution=execution,
        trigger_quality=_clamp(trigger_quality),
        danger_state=danger_state,
        danger_cap=cap,
        applied_modifiers=modifiers,
    )


def detect_danger_state(
    base: PineReplicaScore,
    explainability: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> str | None:
    derived = _dict(base.debug.get("derived"))
    box = _dict(explainability.get("box"))
    climax = _dict(explainability.get("climax"))
    adaptive = _dict(explainability.get("adaptive"))
    thresholds = {
        "climax_risk_min": 7.0,
        "extension_percentile_min": 90.0,
        "extension_pct_min": 15.0,
        "rsi_min": 75.0,
        **_dict(_dict(config).get("detection")),
    }
    matches = {
        "Failed breakout": _bool(derived.get("failed_breakout"))
        or _bool(box.get("box_failure"))
        or base.classification == "Failed breakout",
        "Climax reversal risk": _num(climax.get("climax_risk_score"))
        >= float(thresholds["climax_risk_min"])
        or _bool(climax.get("momentum_crash_risk")),
        "Blowoff top": base.classification == "Blowoff top",
        "Distribution risk": base.classification == "Distribution risk",
        "Late-stage extension": (
            (
                _num(adaptive.get("extension_percentile_252"))
                >= float(thresholds["extension_percentile_min"])
                or _num(derived.get("extension_mid_pct")) >= float(thresholds["extension_pct_min"])
            )
            and _num(derived.get("rsi")) >= float(thresholds["rsi_min"])
        ),
    }
    return next((danger for danger in DANGER_PRIORITY if matches[danger]), None)


def _danger_cap(danger: str | None, config: dict[str, Any]) -> float | None:
    if danger is None:
        return None
    key = danger.lower().replace("-", " ").replace(" ", "_")
    value = config["danger_caps"]["entry_quality"].get(key)
    return None if value is None else float(value)


def _stop_geometry_quality(distance: float | None, config: dict[str, Any]) -> float:
    if distance is None or distance <= 0:
        return 0.0
    outer_min = float(config["stop_atr_outer_min"])
    preferred_min = float(config["preferred_stop_atr_min"])
    preferred_max = float(config["preferred_stop_atr_max"])
    outer_max = float(config["stop_atr_outer_max"])
    if preferred_min <= distance <= preferred_max:
        return 10.0
    if outer_min < distance < preferred_min:
        return _clamp(3.0 + 7.0 * (distance - outer_min) / (preferred_min - outer_min))
    if preferred_max < distance < outer_max:
        return _clamp(10.0 - 7.0 * (distance - preferred_max) / (outer_max - preferred_max))
    return 2.0 if distance > 0 else 0.0


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _bool(value: Any) -> bool:
    return bool(value) if value is not None else False


def _optional_num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _num(value: Any) -> float:
    return _optional_num(value) or 0.0


def _clamp(value: float) -> float:
    return max(0.0, min(10.0, round(float(value), 4)))
