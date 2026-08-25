from typing import Any

from app.models.tables import CombinedResult, TechnicalScore


def technical_score_display_fields(
    score: TechnicalScore | None,
    combined: CombinedResult | None = None,
) -> dict[str, Any]:
    """Build the rollout-aware score hierarchy shown in the decision cockpit.

    The combined result is the preferred source for the active score and classification
    because those persisted values are the inputs that produced Final / Decision. V5
    columns alone never imply that V5 is active.
    """
    v5 = _dict(_value(score, "v5_debug_json"))
    rollout_mode = str(_dict(v5.get("rollout")).get("mode") or "").lower()
    engine_version = str(_value(score, "technical_engine_version") or "")
    v5_active = engine_version.startswith("5.") or rollout_mode == "active"
    active_score = _first_present(
        _value(combined, "dual_score"),
        _value(score, "dual_score"),
    )
    active_classification = _first_present(
        _value(combined, "technical_classification"),
        _value(score, "classification"),
    )
    v4_debug = _explainability(score)
    shadow_comparison = _dict(v5.get("shadow_comparison"))
    v5_score = _value(score, "technical_composite_score")

    if v5_active:
        v4_score = _first_present(
            v4_debug.get("final_v4_score"),
            shadow_comparison.get("v4_score"),
        )
        v4_classification = _first_present(
            v4_debug.get("final_v4_classification"),
            shadow_comparison.get("v4_classification", ""),
        )
        active = _score_tier(
            version="V5",
            role="ACTIVE",
            score=active_score,
            classification=active_classification,
            strength=_value(score, "technical_strength_score"),
            setup_quality=_value(score, "setup_quality_score"),
            entry_quality=_value(score, "entry_quality_score"),
            danger_state=_v5_danger_state(v5),
            setup_type=_value(score, "setup_type"),
            confidence_adjusted_score=_value(score, "confidence_adjusted_score"),
        )
        comparison = (
            _score_tier(
                version="V4",
                role="LEGACY",
                score=v4_score,
                classification=v4_classification,
            )
            if v4_score is not None or v4_classification
            else None
        )
    else:
        v4_score = active_score
        v4_classification = active_classification
        active = _score_tier(
            version="V4",
            role="ACTIVE",
            score=active_score,
            classification=active_classification,
        )
        comparison = (
            _score_tier(
                version="V5",
                role="SHADOW",
                score=v5_score,
                classification=v5.get("classification", ""),
                delta=_score_delta(v5_score, active_score),
                strength=_value(score, "technical_strength_score"),
                setup_quality=_value(score, "setup_quality_score"),
                entry_quality=_value(score, "entry_quality_score"),
                danger_state=_v5_danger_state(v5),
                setup_type=_value(score, "setup_type"),
                confidence_adjusted_score=_value(score, "confidence_adjusted_score"),
            )
            if v5_score is not None
            else None
        )

    v5_classification = (
        active_classification
        if v5_active
        else (comparison or {}).get("classification", "")
    )
    effective_rollout_mode = rollout_mode or (
        "active" if v5_active else "shadow" if v5_score is not None else ""
    )
    return {
        "active": active,
        "comparison": comparison,
        "feeds_combined_decision": active["version"],
        "v4_score": v4_score,
        "v5_score": v5_score,
        "v4_classification": v4_classification,
        "v5_classification": v5_classification,
        "v5_rollout_mode": effective_rollout_mode,
    }


def technical_score_displays_by_ticker(
    scores: list[TechnicalScore],
    combined_results: list[CombinedResult],
) -> dict[str, dict[str, Any]]:
    combined_by_ticker = {result.ticker: result for result in combined_results}
    return {
        score.ticker: technical_score_display_fields(
            score,
            combined_by_ticker.get(score.ticker),
        )
        for score in scores
    }


def technical_v4_summary_fields(score: TechnicalScore | None) -> dict[str, Any]:
    details = technical_v4_detail_fields(score)
    summary = {
        "technical_version": details["technical_version"],
        "technical_stage": details["stage"],
        "technical_regime": details["market_regime"],
        "technical_leadership_score": details["leadership_score"],
        "technical_vcp_score": details["vcp_score"],
        "technical_climax_risk_score": details["climax_risk_score"],
        "technical_flags": details["feature_flags"],
        "technical_warnings": details["warning_flags"],
        "technical_sub_tags": details["sub_tags"],
    }
    if details["technical_composite_score"] is not None:
        summary.update(
            technical_strength_score=details["technical_strength_score"],
            technical_setup_quality_score=details["setup_quality_score"],
            technical_entry_quality_score=details["entry_quality_score"],
            technical_composite_score=details["technical_composite_score"],
            technical_confidence_adjusted_score=details["confidence_adjusted_score"],
            technical_setup_type=details["setup_type"],
        )
    return summary


def technical_v4_detail_fields(score: TechnicalScore | None) -> dict[str, Any]:
    explainability = _explainability(score)
    adaptive = _dict(explainability.get("adaptive"))
    contraction = _dict(explainability.get("contraction"))
    box = _dict(explainability.get("box"))
    stage = _dict(explainability.get("stage"))
    regime = _dict(explainability.get("regime"))
    leadership = _dict(explainability.get("leadership"))
    climax = _dict(explainability.get("climax"))
    v5 = _dict(_value(score, "v5_debug_json"))
    v5_entry = _dict(v5.get("entry_quality"))
    v5_execution = _dict(v5_entry.get("execution"))
    v5_trigger = _dict(_dict(v5.get("setup_quality")).get("trigger"))

    return {
        "technical_version": _first_present(
            _value(score, "technical_engine_version"),
            explainability.get("engine_version", ""),
        ),
        "stage": _first_present(_value(score, "stage"), stage.get("stage", "")),
        "market_regime": _first_present(
            _value(score, "market_regime"),
            regime.get("regime", ""),
        ),
        "leadership_score": _first_present(
            _value(score, "leadership_score"),
            leadership.get("leadership_score", ""),
        ),
        "vcp_score": _first_present(
            _value(score, "vcp_score"),
            contraction.get("vcp_score", ""),
        ),
        "vcp_detected": contraction.get("vcp_detected", ""),
        "box_breakout": box.get("box_breakout", ""),
        "box_tightness_score": _first_present(
            _value(score, "box_tightness_score"),
            box.get("box_tightness_score", ""),
        ),
        "breakout_quality_score": _first_present(
            _value(score, "breakout_quality_score"),
            box.get("breakout_quality_score", ""),
        ),
        "box_width_pct": box.get("box_width_pct", ""),
        "box_age": box.get("box_age", ""),
        "donchian_20_breakout": box.get("donchian_20_breakout", ""),
        "donchian_55_breakout": box.get("donchian_55_breakout", ""),
        "atr_percentile_252": _first_present(
            _value(score, "atr_percentile_252"),
            adaptive.get("atr_percentile_252", ""),
        ),
        "volume_percentile_252": _first_present(
            _value(score, "volume_percentile_252"),
            adaptive.get("volume_percentile_252", ""),
        ),
        "range_percentile_252": _first_present(
            _value(score, "range_percentile_252"),
            adaptive.get("range_percentile_252", ""),
        ),
        "extension_percentile_252": _first_present(
            _value(score, "extension_percentile_252"),
            adaptive.get("extension_percentile_252", ""),
        ),
        "climax_risk_score": _first_present(
            _value(score, "climax_risk_score"),
            climax.get("climax_risk_score", ""),
        ),
        "feature_flags": _list_text(_value(score, "feature_flags_json"))
        or _list_text(explainability.get("feature_flags")),
        "warning_flags": _list_text(_value(score, "warning_flags_json"))
        or _list_text(explainability.get("warning_flags")),
        "sub_tags": _list_text(_value(score, "sub_tags_json"))
        or _list_text(explainability.get("sub_tags")),
        "technical_strength_score": _value(score, "technical_strength_score"),
        "setup_quality_score": _value(score, "setup_quality_score"),
        "entry_quality_score": _value(score, "entry_quality_score"),
        "technical_composite_score": _value(score, "technical_composite_score"),
        "confidence_adjusted_score": _value(score, "confidence_adjusted_score"),
        "leadership_v5_score": _value(score, "leadership_v5_score"),
        "residual_momentum_score": _value(score, "residual_momentum_score"),
        "trigger_distance_atr": _value(score, "trigger_distance_atr"),
        "stop_distance_atr": _value(score, "stop_distance_atr"),
        "setup_type": _value(score, "setup_type") or "",
        "sector_benchmark_symbol": _value(score, "sector_benchmark_symbol") or "",
        "stage_modifier": _value(score, "stage_modifier"),
        "risk_control": v5_entry.get("risk_control", ""),
        "combined_risk": v5_entry.get("combined_risk", ""),
        "execution_quality": v5_execution.get("score", ""),
        "trigger_quality": v5_trigger.get("quality", ""),
        "danger_state": v5_entry.get("danger_state", ""),
        "v5_classification": v5.get("classification", ""),
        "v5_action_bias": v5.get("action_bias", ""),
        "v5_rollout_mode": _dict(v5.get("rollout")).get("mode", ""),
    }


def technical_v4_details_by_ticker(
    scores: list[TechnicalScore],
) -> dict[str, dict[str, Any]]:
    return {
        score.ticker: technical_v4_detail_fields(score)
        for score in scores
    }


def _score_tier(
    *,
    version: str,
    role: str,
    score: Any,
    classification: Any,
    delta: float | None = None,
    strength: Any = None,
    setup_quality: Any = None,
    entry_quality: Any = None,
    danger_state: Any = "",
    setup_type: Any = "",
    confidence_adjusted_score: Any = None,
) -> dict[str, Any]:
    return {
        "version": version,
        "role": role,
        "score": score,
        "classification": classification or "",
        "delta": delta,
        "strength": strength,
        "setup_quality": setup_quality,
        "entry_quality": entry_quality,
        "danger_state": danger_state or "",
        "setup_type": setup_type or "",
        "confidence_adjusted_score": confidence_adjusted_score,
    }


def _v5_danger_state(v5: dict[str, Any]) -> str:
    return str(_dict(v5.get("entry_quality")).get("danger_state") or "")


def _score_delta(comparison: Any, active: Any) -> float | None:
    if comparison is None or active is None:
        return None
    try:
        return round(float(comparison) - float(active), 4)
    except (TypeError, ValueError):
        return None


def _explainability(score: TechnicalScore | None) -> dict[str, Any]:
    if score is None:
        return {}
    if isinstance(score.v4_debug_json, dict):
        return score.v4_debug_json
    if isinstance(score.debug_json, dict):
        return _dict(score.debug_json.get("explainability"))
    return {}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _value(score: Any, attribute: str) -> Any:
    if score is None:
        return None
    return getattr(score, attribute, None)


def _first_present(primary: Any, fallback: Any) -> Any:
    if primary is None or primary == "":
        return fallback
    return primary


def _list_text(values: Any) -> str:
    if not isinstance(values, list):
        return ""
    return "; ".join(str(value) for value in values)
