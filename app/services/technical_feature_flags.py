from typing import Any

import pandas as pd

VCP_SCORE_TAG_MIN = 7.0
VOLUME_DRY_UP_TAG_MIN = 7.0
TIGHT_CLOSE_COUNT_MIN = 3
CLIMAX_RISK_TAG_MIN = 7.0


def feature_flags_from_latest(latest: dict[str, Any]) -> list[str]:
    return _active_keys(_feature_flag_checks(latest))


def warning_flags_from_latest(
    *,
    latest: dict[str, Any],
    derived: dict[str, Any],
    warning_flags: list[str] | tuple[str, ...],
    data_readiness: Any | None = None,
) -> list[str]:
    flags = list(warning_flags)
    checks = {
        "failed_breakout": latest.get("failed_breakout"),
        "distribution_risk": latest.get("distribution_risk"),
        "blowoff_top": latest.get("blowoff_top"),
        "box_failure": latest.get("box_failure"),
        "liquidity_warning": latest.get("liquidity_warning"),
        "market_risk_off": derived.get("market_risk_off")
        or latest.get("market_risk_off"),
        "climax_reversal_risk": latest.get("momentum_crash_risk")
        or _num(latest.get("climax_risk_score")) >= CLIMAX_RISK_TAG_MIN,
        "stage_3_distribution": _stage(latest) == "Stage 3",
        "stage_4_downtrend": _stage(latest) == "Stage 4",
        "low_technical_confidence": _low_confidence(data_readiness),
    }
    return _append_active(flags, checks)


def sub_tags_from_latest(
    latest: dict[str, Any],
    data_readiness: Any | None = None,
) -> list[str]:
    return _active_keys(_sub_tag_checks(latest, data_readiness=data_readiness))


def promote_explainability_flags(
    explainability: dict[str, Any],
    *,
    feature_flags: list[str],
    warning_flags: list[str],
    sub_tags: list[str],
) -> dict[str, list[str]]:
    latest = _latest_from_explainability(explainability)
    data_readiness = explainability.get("data_readiness")
    return {
        "feature_flags": _append_unique(feature_flags, feature_flags_from_latest(latest)),
        "warning_flags": _append_unique(
            warning_flags,
            warning_flags_from_latest(
                latest=latest,
                derived={},
                warning_flags=[],
                data_readiness=data_readiness,
            ),
        ),
        "sub_tags": _append_unique(
            sub_tags,
            sub_tags_from_latest(latest, data_readiness=data_readiness),
        ),
    }


def _feature_flag_checks(latest: dict[str, Any]) -> dict[str, Any]:
    stage_tags = _list(latest.get("stage_tags"))
    return {
        "vcp_detected": latest.get("vcp_detected")
        or _num(latest.get("vcp_score")) >= VCP_SCORE_TAG_MIN,
        "squeeze_release": latest.get("squeeze_release"),
        "volume_dry_up": _num(latest.get("volume_dry_up_quality"))
        >= VOLUME_DRY_UP_TAG_MIN,
        "tight_closes": _num(latest.get("tight_close_count_5"))
        >= TIGHT_CLOSE_COUNT_MIN,
        "box_breakout": latest.get("box_breakout"),
        "box_failure": latest.get("box_failure"),
        "donchian_20_breakout": latest.get("donchian_20_breakout"),
        "donchian_55_breakout": latest.get("donchian_55_breakout"),
        "stage_2": _stage(latest) == "Stage 2",
        "stage_1_to_2_transition": "stage_1_to_2_transition" in stage_tags,
        "stage_2_continuation": "stage_2_continuation" in stage_tags,
        "late_stage_2_extension": "late_stage_2_extension" in stage_tags,
        "stage_3_distribution": "stage_3_distribution" in stage_tags
        or _stage(latest) == "Stage 3",
        "stage_4_downtrend": "stage_4_downtrend" in stage_tags
        or _stage(latest) == "Stage 4",
        "vertical_move": latest.get("vertical_move_flag"),
        "volume_climax": latest.get("volume_climax_flag")
        or latest.get("climax_volume_flag"),
        "range_climax": latest.get("range_climax_flag")
        or latest.get("climax_range_flag"),
        "momentum_crash_risk": latest.get("momentum_crash_risk"),
        "market_risk_off": latest.get("market_risk_off"),
    }


def _sub_tag_checks(
    latest: dict[str, Any],
    *,
    data_readiness: Any | None,
) -> dict[str, Any]:
    stage_tags = _list(latest.get("stage_tags"))
    return {
        "VCP": latest.get("vcp_detected")
        or _num(latest.get("vcp_score")) >= VCP_SCORE_TAG_MIN,
        "Darvas box": latest.get("box_breakout"),
        "Donchian breakout": latest.get("donchian_20_breakout")
        or latest.get("donchian_55_breakout"),
        "Stage 2": _stage(latest) == "Stage 2",
        "Stage 2 continuation": "stage_2_continuation" in stage_tags,
        "Stage 1-to-2 transition": "stage_1_to_2_transition" in stage_tags,
        "Late-stage extension": "late_stage_2_extension" in stage_tags,
        "Volume dry-up": _num(latest.get("volume_dry_up_quality"))
        >= VOLUME_DRY_UP_TAG_MIN,
        "Tight closes": _num(latest.get("tight_close_count_5"))
        >= TIGHT_CLOSE_COUNT_MIN,
        "Low confidence": _low_confidence(data_readiness),
        "Market risk": latest.get("market_risk_off"),
        "Climax risk": latest.get("momentum_crash_risk")
        or _num(latest.get("climax_risk_score")) >= CLIMAX_RISK_TAG_MIN,
    }


def _latest_from_explainability(explainability: dict[str, Any]) -> dict[str, Any]:
    adaptive = _dict(explainability.get("adaptive"))
    contraction = _dict(explainability.get("contraction"))
    box = _dict(explainability.get("box"))
    stage = _dict(explainability.get("stage"))
    regime = _dict(explainability.get("regime"))
    climax = _dict(explainability.get("climax"))
    return {
        **adaptive,
        **contraction,
        **box,
        "stage": stage.get("stage"),
        "stage_tags": stage.get("stage_tags"),
        "market_risk_off": regime.get("risk_off"),
        **climax,
    }


def _active_keys(checks: dict[str, Any]) -> list[str]:
    return [key for key, active in checks.items() if _bool(active)]


def _append_active(values: list[str], checks: dict[str, Any]) -> list[str]:
    return _append_unique(values, _active_keys(checks))


def _append_unique(values: list[str], additions: list[str]) -> list[str]:
    result = list(values)
    seen = set(result)
    for addition in additions:
        if addition not in seen:
            result.append(addition)
            seen.add(addition)
    return result


def _stage(latest: dict[str, Any]) -> str:
    return str(latest.get("stage") or "")


def _low_confidence(data_readiness: Any | None) -> bool:
    confidence = _readiness_value(data_readiness, "confidence")
    return str(confidence or "").lower() in {"low", "error"}


def _readiness_value(data_readiness: Any | None, key: str) -> Any:
    if data_readiness is None:
        return None
    if isinstance(data_readiness, dict):
        return data_readiness.get(key)
    return getattr(data_readiness, key, None)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _num(value: Any) -> float:
    if _is_missing(value):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _bool(value: Any) -> bool:
    if _is_missing(value):
        return False
    return bool(value)


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (dict, list, tuple)):
        return False
    return bool(pd.isna(value))
