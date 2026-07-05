from dataclasses import dataclass
from typing import Any

from app.services.pine_replica_engine import (
    CLASS_BLOWOFF_TOP,
    CLASS_CLEAN_PULLBACK,
    CLASS_DISTRIBUTION_RISK,
    CLASS_FAILED_BREAKOUT,
    CLASS_FILTERED_MOMENTUM,
    CLASS_FILTERED_PULLBACK,
    CLASS_FRESH_BREAKOUT,
    CLASS_PRIME_PULLBACK,
    PineReplicaScore,
)
from app.services.technical_feature_flags import promote_explainability_flags

CLASS_CLIMAX_REVERSAL_RISK = "Climax reversal risk"
CLASS_LATE_STAGE_EXTENSION = "Late-stage extension"
CLASS_TIGHT_BASE_BREAKOUT = "Tight base breakout"
CLASS_VOLATILITY_CONTRACTION = "Volatility contraction setup"

DEFAULT_DANGER_PRIORITY = [
    CLASS_FAILED_BREAKOUT,
    CLASS_CLIMAX_REVERSAL_RISK,
    CLASS_BLOWOFF_TOP,
    CLASS_DISTRIBUTION_RISK,
    CLASS_LATE_STAGE_EXTENSION,
]

V4_DANGER_CLASSIFICATIONS = {
    CLASS_FAILED_BREAKOUT,
    CLASS_CLIMAX_REVERSAL_RISK,
    CLASS_BLOWOFF_TOP,
    CLASS_DISTRIBUTION_RISK,
    CLASS_LATE_STAGE_EXTENSION,
}

V4_BUYABLE_CLASSIFICATIONS = {
    CLASS_PRIME_PULLBACK,
    CLASS_CLEAN_PULLBACK,
    CLASS_FRESH_BREAKOUT,
    CLASS_TIGHT_BASE_BREAKOUT,
    CLASS_VOLATILITY_CONTRACTION,
    "RS leader pullback",
}

DEFAULT_REGIME_WEIGHTS = {
    "bull_trend": {
        "trend": 0.22,
        "momentum": 0.20,
        "setup": 0.20,
        "leadership": 0.14,
        "risk_control": 0.10,
        "market": 0.08,
        "execution": 0.06,
    },
    "choppy": {
        "trend": 0.18,
        "momentum": 0.10,
        "setup": 0.24,
        "leadership": 0.10,
        "risk_control": 0.22,
        "market": 0.10,
        "execution": 0.06,
    },
    "risk_off": {
        "trend": 0.10,
        "momentum": 0.05,
        "setup": 0.10,
        "leadership": 0.05,
        "risk_control": 0.40,
        "market": 0.25,
        "execution": 0.05,
    },
}


@dataclass(frozen=True)
class TechnicalScoreV4:
    base_score: PineReplicaScore
    engine_version: str
    data_readiness: dict[str, Any]
    adaptive: dict[str, Any]
    contraction: dict[str, Any]
    box: dict[str, Any]
    stage: dict[str, Any]
    regime: dict[str, Any]
    leadership: dict[str, Any] | None
    climax: dict[str, Any]
    feature_flags: list[str]
    warning_flags: list[str]
    sub_tags: list[str]
    final_v4_score: float
    final_v4_classification: str
    final_v4_action: str
    debug: dict[str, Any]

    @property
    def ticker(self) -> str:
        return self.base_score.ticker


def technical_score_v4_from_base_score(
    base_score: PineReplicaScore,
    v4_params: dict[str, Any] | None = None,
) -> TechnicalScoreV4:
    debug = {**(base_score.debug or {})}
    explainability = _dict(debug.get("explainability"))
    engine_config = (v4_params or {}).get("engine", {})
    engine_version = str(
        explainability.get("engine_version")
        or engine_config.get("version")
        or "4.0.0"
    )
    feature_flags = _list(explainability.get("feature_flags"))
    warning_flags = _list(explainability.get("warning_flags")) or list(
        base_score.warning_flags
    )
    sub_tags = _list(explainability.get("sub_tags"))
    promoted_flags = promote_explainability_flags(
        explainability,
        feature_flags=feature_flags,
        warning_flags=warning_flags,
        sub_tags=sub_tags,
    )
    feature_flags = promoted_flags["feature_flags"]
    warning_flags = promoted_flags["warning_flags"]
    sub_tags = promoted_flags["sub_tags"]
    scoring = _regime_weighted_score(
        base_score=base_score,
        explainability=explainability,
        v4_params=v4_params or {},
    )
    classification = _classify_v4(
        base_score=base_score,
        explainability=explainability,
        v4_params=v4_params or {},
    )
    sub_tags = _append_unique(sub_tags, classification["sub_tags"])
    warning_flags = _append_unique(warning_flags, classification["warning_flags"])
    final_v4_score = scoring["final_v4_score"]
    final_v4_classification = str(
        explainability.get("final_v4_classification")
        or classification["classification"]
    )
    final_v4_action = str(
        explainability.get("final_v4_action")
        or _v4_action_bias(
            final_v4_classification,
            base_score.action_bias,
            classification["reasons"],
        )
    )

    explainability = {
        **explainability,
        "engine_version": engine_version,
        "data_readiness": _dict(explainability.get("data_readiness")),
        "adaptive": _dict(explainability.get("adaptive")),
        "contraction": _dict(explainability.get("contraction")),
        "box": _dict(explainability.get("box")),
        "stage": _dict(explainability.get("stage")),
        "regime": _dict(explainability.get("regime")),
        "leadership": _optional_dict(explainability.get("leadership")),
        "climax": _dict(explainability.get("climax")),
        "feature_flags": feature_flags,
        "warning_flags": warning_flags,
        "sub_tags": sub_tags,
        "final_v4_score": final_v4_score,
        "final_v4_classification": final_v4_classification,
        "final_v4_action": final_v4_action,
        "debug": {
            **_dict(explainability.get("debug")),
            "score_source": "regime_weighted_v4",
            "base_dual_score": base_score.dual_score,
            "regime_weight_key": scoring["regime_weight_key"],
            "regime_weights": scoring["regime_weights"],
            "v4_components": scoring["components"],
            "classification_source": "v4_priority",
            "base_classification": base_score.classification,
            "classification_reasons": classification["reasons"],
            "stage_gate": classification["stage_gate"],
        },
    }
    debug["explainability"] = explainability

    return TechnicalScoreV4(
        base_score=base_score,
        engine_version=engine_version,
        data_readiness=explainability["data_readiness"],
        adaptive=explainability["adaptive"],
        contraction=explainability["contraction"],
        box=explainability["box"],
        stage=explainability["stage"],
        regime=explainability["regime"],
        leadership=explainability["leadership"],
        climax=explainability["climax"],
        feature_flags=feature_flags,
        warning_flags=warning_flags,
        sub_tags=sub_tags,
        final_v4_score=final_v4_score,
        final_v4_classification=final_v4_classification,
        final_v4_action=final_v4_action,
        debug=debug,
    )


def _regime_weighted_score(
    *,
    base_score: PineReplicaScore,
    explainability: dict[str, Any],
    v4_params: dict[str, Any],
) -> dict[str, Any]:
    adaptive = _dict(explainability.get("adaptive"))
    contraction = _dict(explainability.get("contraction"))
    box = _dict(explainability.get("box"))
    regime = _dict(explainability.get("regime"))
    leadership = _dict(explainability.get("leadership"))
    climax = _dict(explainability.get("climax"))
    weight_key = _regime_weight_key(regime)
    weights = _regime_weights(v4_params, weight_key)

    setup_quality = max(
        base_score.setup_score,
        _num(contraction.get("vcp_score"), 0.0),
        _num(box.get("breakout_quality_score"), 0.0),
    )
    climax_risk_score = _num(climax.get("climax_risk_score"), 0.0)
    risk_control = _clamp(10.0 - max(base_score.risk_score, climax_risk_score))
    leadership_score = _num(
        leadership.get("leadership_score"),
        base_score.combined_relative_strength_score,
    )
    market_score = _num(regime.get("score"), base_score.market_score)
    execution_quality = _execution_quality(base_score)
    components = {
        "trend_quality": base_score.trend_score,
        "momentum_quality": base_score.momentum_score,
        "setup_quality": setup_quality,
        "leadership_score": leadership_score,
        "risk_control": risk_control,
        "market_regime": market_score,
        "execution_quality": execution_quality,
        "base_risk_score": base_score.risk_score,
        "climax_risk_score": climax_risk_score,
        "atr_percentile_252": _optional_float(adaptive.get("atr_percentile_252")),
    }
    score = (
        components["trend_quality"] * weights["trend"]
        + components["momentum_quality"] * weights["momentum"]
        + components["setup_quality"] * weights["setup"]
        + components["leadership_score"] * weights["leadership"]
        + components["risk_control"] * weights["risk_control"]
        + components["market_regime"] * weights["market"]
        + components["execution_quality"] * weights["execution"]
    )
    return {
        "final_v4_score": _clamp(score),
        "regime_weight_key": weight_key,
        "regime_weights": weights,
        "components": components,
    }


def _classify_v4(
    *,
    base_score: PineReplicaScore,
    explainability: dict[str, Any],
    v4_params: dict[str, Any],
) -> dict[str, Any]:
    debug = base_score.debug or {}
    derived = _dict(debug.get("derived"))
    adaptive = _dict(explainability.get("adaptive"))
    contraction = _dict(explainability.get("contraction"))
    box = _dict(explainability.get("box"))
    stage = _dict(explainability.get("stage"))
    regime = _dict(explainability.get("regime"))
    climax = _dict(explainability.get("climax"))

    climax_threshold = _threshold(
        v4_params,
        section="climax_risk",
        key="climax_risk_threshold",
        default=7.0,
    )
    vcp_min_score = _threshold(
        v4_params,
        section="volatility_contraction",
        key="vcp_min_score",
        default=7.0,
    )
    breakout_quality_min = _threshold(
        v4_params,
        section="donchian_darvas",
        key="breakout_quality_min",
        default=7.0,
    )

    climax_risk_score = _num(climax.get("climax_risk_score"), 0.0)
    vcp_score = _num(contraction.get("vcp_score"), 0.0)
    breakout_quality_score = _num(box.get("breakout_quality_score"), 0.0)

    danger_matches = {
        CLASS_FAILED_BREAKOUT: _bool(derived.get("failed_breakout"))
        or _bool(box.get("box_failure"))
        or base_score.classification == CLASS_FAILED_BREAKOUT,
        CLASS_CLIMAX_REVERSAL_RISK: climax_risk_score >= climax_threshold
        or _bool(climax.get("momentum_crash_risk")),
        CLASS_BLOWOFF_TOP: base_score.classification == CLASS_BLOWOFF_TOP,
        CLASS_DISTRIBUTION_RISK: base_score.classification == CLASS_DISTRIBUTION_RISK,
        CLASS_LATE_STAGE_EXTENSION: _late_stage_extension(
            derived=derived,
            adaptive=adaptive,
            climax=climax,
        ),
    }
    for classification in _danger_priority(v4_params):
        if danger_matches.get(classification):
            return _with_stage_metadata(
                _classification_result(
                    classification,
                    reasons=[_reason_key(classification)],
                    warning_flags=[_warning_key(classification)],
                    sub_tags=_classification_sub_tags(
                        adaptive=adaptive,
                        box=box,
                        contraction=contraction,
                        stage=stage,
                        regime=regime,
                        climax=climax,
                    ),
                ),
                stage=stage,
                v4_params=v4_params,
            )

    if (
        _bool(box.get("box_breakout"))
        and breakout_quality_score >= breakout_quality_min
    ):
        return _apply_stage_gate(
            _classification_result(
                CLASS_TIGHT_BASE_BREAKOUT,
                reasons=["tight_base_breakout"],
                sub_tags=_append_unique(
                    _classification_sub_tags(
                        adaptive=adaptive,
                        box=box,
                        contraction=contraction,
                        stage=stage,
                        regime=regime,
                        climax=climax,
                    ),
                    ["Darvas box"],
                ),
            ),
            stage=stage,
            v4_params=v4_params,
        )

    if (
        vcp_score >= vcp_min_score
        and base_score.trend_score >= 6.5
    ):
        return _apply_stage_gate(
            _classification_result(
                CLASS_VOLATILITY_CONTRACTION,
                reasons=["volatility_contraction_setup"],
                sub_tags=_append_unique(
                    _classification_sub_tags(
                        adaptive=adaptive,
                        box=box,
                        contraction=contraction,
                        stage=stage,
                        regime=regime,
                        climax=climax,
                    ),
                    ["VCP"],
                ),
            ),
            stage=stage,
            v4_params=v4_params,
        )

    if base_score.classification in {
        CLASS_PRIME_PULLBACK,
        CLASS_CLEAN_PULLBACK,
        CLASS_FRESH_BREAKOUT,
    }:
        return _apply_stage_gate(
            _classification_result(
                base_score.classification,
                reasons=["base_classifier"],
                sub_tags=_classification_sub_tags(
                    adaptive=adaptive,
                    box=box,
                    contraction=contraction,
                    stage=stage,
                    regime=regime,
                    climax=climax,
                ),
            ),
            stage=stage,
            v4_params=v4_params,
        )

    return _with_stage_metadata(
        _classification_result(
            base_score.classification,
            reasons=["fallback_base_classifier"],
            sub_tags=_classification_sub_tags(
                adaptive=adaptive,
                box=box,
                contraction=contraction,
                stage=stage,
                regime=regime,
                climax=climax,
            ),
        ),
        stage=stage,
        v4_params=v4_params,
    )


def _classification_result(
    classification: str,
    *,
    reasons: list[str],
    sub_tags: list[str] | None = None,
    warning_flags: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "classification": classification,
        "reasons": reasons,
        "sub_tags": sub_tags or [],
        "warning_flags": warning_flags or [],
        "stage_gate": {"checked": False, "passed": None, "stage": None},
    }


def _danger_priority(v4_params: dict[str, Any]) -> list[str]:
    configured = _dict(v4_params.get("classification_v4")).get("danger_priority")
    if isinstance(configured, list) and configured:
        return _append_unique([str(item) for item in configured], DEFAULT_DANGER_PRIORITY)
    return DEFAULT_DANGER_PRIORITY


def _classification_sub_tags(
    *,
    adaptive: dict[str, Any],
    box: dict[str, Any],
    contraction: dict[str, Any],
    stage: dict[str, Any],
    regime: dict[str, Any],
    climax: dict[str, Any],
) -> list[str]:
    tags: list[str] = []
    if stage.get("stage") == "Stage 2":
        tags.append("Stage 2")
    if _bool(contraction.get("vcp_detected")) or _num(contraction.get("vcp_score"), 0.0) >= 7.0:
        tags.append("VCP")
    if _bool(box.get("box_breakout")):
        tags.append("Darvas box")
    if _bool(box.get("donchian_20_breakout")) or _bool(box.get("donchian_55_breakout")):
        tags.append("Donchian breakout")
    if _num(contraction.get("volume_dry_up_quality"), 0.0) >= 7.0:
        tags.append("Volume dry-up")
    if _num(contraction.get("tight_close_count_5"), 0.0) >= 3.0:
        tags.append("Tight closes")
    if _bool(regime.get("risk_off")):
        tags.append("Market risk")
    if _num(climax.get("climax_risk_score"), 0.0) >= 7.0:
        tags.append("Climax risk")
    return tags


def _apply_stage_gate(
    result: dict[str, Any],
    *,
    stage: dict[str, Any],
    v4_params: dict[str, Any],
) -> dict[str, Any]:
    result = _with_stage_metadata(result, stage=stage, v4_params=v4_params)
    if result["classification"] not in V4_BUYABLE_CLASSIFICATIONS:
        return result

    stage_gate = result["stage_gate"]
    if not stage_gate["required"] or stage_gate["passed"]:
        return result

    blocked_classification = (
        CLASS_FILTERED_MOMENTUM
        if result["classification"] in {CLASS_FRESH_BREAKOUT, CLASS_TIGHT_BASE_BREAKOUT}
        else CLASS_FILTERED_PULLBACK
    )
    return {
        **result,
        "classification": blocked_classification,
        "reasons": _append_unique(result["reasons"], ["stage_gate_blocked"]),
        "warning_flags": _append_unique(
            result["warning_flags"],
            ["stage_gate_blocked"],
        ),
        "stage_gate": {**stage_gate, "blocked_classification": result["classification"]},
    }


def _with_stage_metadata(
    result: dict[str, Any],
    *,
    stage: dict[str, Any],
    v4_params: dict[str, Any],
) -> dict[str, Any]:
    warnings = _append_unique(result["warning_flags"], _stage_warning_flags(stage))
    return {
        **result,
        "warning_flags": warnings,
        "stage_gate": _stage_gate_snapshot(stage, v4_params),
    }


def _stage_gate_snapshot(
    stage: dict[str, Any],
    v4_params: dict[str, Any],
) -> dict[str, Any]:
    required = _stage_gate_required(v4_params)
    return {
        "checked": True,
        "required": required,
        "passed": _stage_ok_for_setup(stage, v4_params) if required else True,
        "stage": str(stage.get("stage") or "Unknown"),
        "stage_tags": _list(stage.get("stage_tags")),
    }


def _stage_gate_required(v4_params: dict[str, Any]) -> bool:
    stage_params = _dict(v4_params.get("stage_analysis"))
    return _bool_default(stage_params.get("allow_prime_only_in_stage_2"), True)


def _stage_ok_for_setup(stage: dict[str, Any], v4_params: dict[str, Any]) -> bool:
    stage_name = str(stage.get("stage") or "")
    stage_tags = _list(stage.get("stage_tags"))
    if stage_name == "Stage 2":
        return True
    allow_transition = _bool_default(
        _dict(v4_params.get("stage_analysis")).get("allow_stage_1_to_2_transition"),
        True,
    )
    return allow_transition and "stage_1_to_2_transition" in stage_tags


def _stage_warning_flags(stage: dict[str, Any]) -> list[str]:
    stage_name = str(stage.get("stage") or "")
    if stage_name == "Stage 3":
        return ["stage_3_distribution"]
    if stage_name == "Stage 4":
        return ["stage_4_downtrend"]
    return []


def _late_stage_extension(
    *,
    derived: dict[str, Any],
    adaptive: dict[str, Any],
    climax: dict[str, Any],
) -> bool:
    reasons = _list(climax.get("reasons"))
    extension_percentile = _num(adaptive.get("extension_percentile_252"), 0.0)
    rsi = _num(derived.get("rsi"), 0.0)
    extension_mid_pct = _num(derived.get("extension_mid_pct"), 0.0)
    has_extension_reason = bool(
        {"extended", "extreme_extension", "extension_percentile"}.intersection(reasons)
    )
    return (
        (extension_percentile >= 90.0 and rsi >= 75.0)
        or (extension_mid_pct >= 15.0 and rsi >= 75.0)
        or (has_extension_reason and rsi >= 75.0)
    )


def _v4_action_bias(
    classification: str,
    base_action: str,
    reasons: list[str],
) -> str:
    if classification == CLASS_CLIMAX_REVERSAL_RISK:
        return "Avoid / reversal risk"
    if classification == CLASS_LATE_STAGE_EXTENSION:
        return "Avoid chasing, wait reset"
    if classification == CLASS_TIGHT_BASE_BREAKOUT:
        return "Breakout candidate, confirm R/R"
    if classification == CLASS_VOLATILITY_CONTRACTION:
        return "Setup candidate, wait for trigger"
    if classification == CLASS_FILTERED_PULLBACK and "stage_gate_blocked" in reasons:
        return "Good chart, stage gate failed"
    if classification == CLASS_FILTERED_MOMENTUM and "stage_gate_blocked" in reasons:
        return "Momentum, stage gate failed"
    if classification in V4_DANGER_CLASSIFICATIONS:
        return "Avoid"
    return base_action


def _reason_key(classification: str) -> str:
    return classification.lower().replace("-", " ").replace(" ", "_")


def _warning_key(classification: str) -> str:
    return _reason_key(classification)


def _regime_weight_key(regime: dict[str, Any]) -> str:
    if bool(regime.get("risk_off")):
        return "risk_off"
    name = str(regime.get("regime") or "").lower()
    if name in {"distribution", "correction", "crash risk"}:
        return "risk_off"
    if name in {"bull trend", "bull pullback", "risk-on breakout"}:
        return "bull_trend"
    return "choppy"


def _regime_weights(v4_params: dict[str, Any], key: str) -> dict[str, float]:
    configured = v4_params.get("regime_weights", {})
    weights = configured.get(key) if isinstance(configured, dict) else None
    if not isinstance(weights, dict):
        weights = DEFAULT_REGIME_WEIGHTS[key]
    return {name: float(value) for name, value in weights.items()}


def _execution_quality(base_score: PineReplicaScore) -> float:
    reward_risk = _num(base_score.reward_risk, 0.0)
    entry_risk_pct = _num(base_score.entry_risk_pct, 99.0)
    reward_risk_score = _clamp(reward_risk / 3.0 * 10.0)
    entry_risk_score = _clamp((12.0 - entry_risk_pct) / 12.0 * 10.0)
    liquidity_score = 2.0 if _liquidity_warning(base_score) else 10.0
    stop_validity_score = (
        10.0
        if base_score.suggested_stop is not None and entry_risk_pct > 0
        else 0.0
    )
    return _clamp(
        reward_risk_score * 0.35
        + entry_risk_score * 0.30
        + liquidity_score * 0.20
        + stop_validity_score * 0.15
    )


def _liquidity_warning(base_score: PineReplicaScore) -> bool:
    debug = base_score.debug or {}
    derived = _dict(debug.get("derived"))
    return bool(derived.get("liquidity_warning"))


def _threshold(
    v4_params: dict[str, Any],
    *,
    section: str,
    key: str,
    default: float,
) -> float:
    return _num(_dict(v4_params.get(section)).get(key), default)


def _append_unique(base: list[str], additions: list[str]) -> list[str]:
    result = list(base)
    seen = set(result)
    for item in additions:
        if item not in seen:
            result.append(item)
            seen.add(item)
    return result


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    try:
        if value != value:
            return False
    except TypeError:
        return False
    return bool(value)


def _bool_default(value: Any, default: bool) -> bool:
    if value is None:
        return default
    return _bool(value)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _optional_dict(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _num(value: Any, default: float) -> float:
    if value is None:
        return round(float(default), 4)
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return round(float(default), 4)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _clamp(value: float) -> float:
    return max(0.0, min(10.0, round(float(value), 4)))
