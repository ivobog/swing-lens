from typing import Any

from app.models.tables import TechnicalScore


def technical_v4_summary_fields(score: TechnicalScore | None) -> dict[str, Any]:
    details = technical_v4_detail_fields(score)
    return {
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


def technical_v4_detail_fields(score: TechnicalScore | None) -> dict[str, Any]:
    explainability = _explainability(score)
    adaptive = _dict(explainability.get("adaptive"))
    contraction = _dict(explainability.get("contraction"))
    box = _dict(explainability.get("box"))
    stage = _dict(explainability.get("stage"))
    regime = _dict(explainability.get("regime"))
    leadership = _dict(explainability.get("leadership"))
    climax = _dict(explainability.get("climax"))

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
