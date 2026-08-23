from typing import Any

from app.models.tables import TechnicalScore


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


def _value(score: TechnicalScore | None, attribute: str) -> Any:
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
