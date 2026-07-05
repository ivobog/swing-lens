from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

from app.services.climax_risk import ClimaxRiskResult
from app.services.market_regime import MarketRegimeResult
from app.services.relative_leadership import LeadershipResult
from app.services.stage_analysis import StageAnalysisResult
from app.services.technical_confidence import TechnicalDataReadiness


@dataclass(frozen=True)
class AdaptiveFeatureSnapshot:
    atr_percentile_126: float | None
    atr_percentile_252: float | None
    volume_percentile_252: float | None
    notional_volume_percentile_252: float | None
    range_percentile_252: float | None
    extension_percentile_252: float | None
    atr_expansion_flag: bool
    atr_contraction_flag: bool
    climax_volume_flag: bool
    climax_range_flag: bool


@dataclass(frozen=True)
class ContractionFeatureSnapshot:
    bb_width_pct: float | None
    bb_width_percentile_252: float | None
    squeeze_on: bool
    squeeze_release: bool
    atr_contraction: bool
    range_contraction: bool
    volume_dry_up_quality: float
    tight_close_count_5: int
    tight_close_count_10: int
    vcp_score: float
    vcp_detected: bool


@dataclass(frozen=True)
class BoxFeatureSnapshot:
    donchian_20_high: float | None
    donchian_20_low: float | None
    donchian_55_high: float | None
    donchian_55_low: float | None
    donchian_20_breakout: bool
    donchian_55_breakout: bool
    box_high: float | None
    box_low: float | None
    box_width_pct: float | None
    box_age: int | None
    box_tightness_score: float
    box_breakout: bool
    box_failure: bool
    breakout_quality_score: float


@dataclass(frozen=True)
class TechnicalScoreExplainability:
    engine_version: str
    base_engine_version: str
    data_readiness: TechnicalDataReadiness
    adaptive: AdaptiveFeatureSnapshot
    contraction: ContractionFeatureSnapshot
    box: BoxFeatureSnapshot
    stage: StageAnalysisResult
    regime: MarketRegimeResult
    leadership: LeadershipResult | None
    climax: ClimaxRiskResult
    feature_flags: list[str]
    warning_flags: list[str]
    sub_tags: list[str]
    final_v4_score: float
    final_v4_classification: str
    final_v4_action: str
    debug: dict[str, Any]


def build_technical_explainability(
    *,
    latest: dict[str, Any],
    derived: dict[str, Any],
    data_readiness: TechnicalDataReadiness,
    regime: MarketRegimeResult,
    final_score: float,
    final_classification: str,
    final_action: str,
    warning_flags: list[str] | tuple[str, ...],
    v4_params: dict[str, Any],
) -> dict[str, Any]:
    engine = v4_params.get("engine", {})
    explainability = TechnicalScoreExplainability(
        engine_version=str(engine.get("version", "4.0.0")),
        base_engine_version=str(engine.get("base_engine_version", "3.2.0")),
        data_readiness=data_readiness,
        adaptive=_adaptive_snapshot(latest),
        contraction=_contraction_snapshot(latest),
        box=_box_snapshot(latest),
        stage=_stage_snapshot(latest),
        regime=regime,
        leadership=None,
        climax=_climax_snapshot(latest),
        feature_flags=_feature_flags(latest),
        warning_flags=_warning_flags(latest, derived, warning_flags),
        sub_tags=_sub_tags(latest),
        final_v4_score=round(float(final_score), 4),
        final_v4_classification=final_classification,
        final_v4_action=final_action,
        debug={
            "score_source": "base_v3_2_with_v4_features",
            "component_scores": {
                "trend": _optional_float(derived.get("trend_score")),
                "momentum": _optional_float(derived.get("momentum_score")),
                "setup": _optional_float(derived.get("setup_score")),
                "risk": _optional_float(derived.get("risk_score")),
                "market": _optional_float(derived.get("market_score")),
                "relative_strength": _optional_float(derived.get("combined_rs_score")),
                "htf": _optional_float(derived.get("htf_score")),
            },
        },
    )
    return asdict(explainability)


def add_leadership_to_explainability(
    debug: dict[str, Any],
    leadership: LeadershipResult,
) -> dict[str, Any]:
    updated = {**debug}
    explainability = {
        **updated.get("explainability", {}),
        "leadership": asdict(leadership),
    }
    explainability["feature_flags"] = _append_unique(
        list(explainability.get("feature_flags", [])),
        leadership.leadership_tags,
    )
    if "rs_leader" in leadership.leadership_tags:
        explainability["sub_tags"] = _append_unique(
            list(explainability.get("sub_tags", [])),
            ["RS leader"],
        )
    updated["explainability"] = explainability
    return updated


def _adaptive_snapshot(latest: dict[str, Any]) -> AdaptiveFeatureSnapshot:
    return AdaptiveFeatureSnapshot(
        atr_percentile_126=_optional_float(latest.get("atr_percentile_126")),
        atr_percentile_252=_optional_float(latest.get("atr_percentile_252")),
        volume_percentile_252=_optional_float(latest.get("volume_percentile_252")),
        notional_volume_percentile_252=_optional_float(
            latest.get("notional_volume_percentile_252")
        ),
        range_percentile_252=_optional_float(latest.get("range_percentile_252")),
        extension_percentile_252=_optional_float(latest.get("extension_percentile_252")),
        atr_expansion_flag=_bool(latest.get("atr_expansion_flag")),
        atr_contraction_flag=_bool(latest.get("atr_contraction_flag")),
        climax_volume_flag=_bool(latest.get("climax_volume_flag")),
        climax_range_flag=_bool(latest.get("climax_range_flag")),
    )


def _contraction_snapshot(latest: dict[str, Any]) -> ContractionFeatureSnapshot:
    return ContractionFeatureSnapshot(
        bb_width_pct=_optional_float(latest.get("bb_width_pct")),
        bb_width_percentile_252=_optional_float(latest.get("bb_width_percentile_252")),
        squeeze_on=_bool(latest.get("squeeze_on")),
        squeeze_release=_bool(latest.get("squeeze_release")),
        atr_contraction=_bool(latest.get("atr_contraction")),
        range_contraction=_bool(latest.get("range_contraction")),
        volume_dry_up_quality=_num(latest.get("volume_dry_up_quality")),
        tight_close_count_5=int(_num(latest.get("tight_close_count_5"))),
        tight_close_count_10=int(_num(latest.get("tight_close_count_10"))),
        vcp_score=_num(latest.get("vcp_score")),
        vcp_detected=_bool(latest.get("vcp_detected")),
    )


def _box_snapshot(latest: dict[str, Any]) -> BoxFeatureSnapshot:
    return BoxFeatureSnapshot(
        donchian_20_high=_optional_float(latest.get("donchian_20_high")),
        donchian_20_low=_optional_float(latest.get("donchian_20_low")),
        donchian_55_high=_optional_float(latest.get("donchian_55_high")),
        donchian_55_low=_optional_float(latest.get("donchian_55_low")),
        donchian_20_breakout=_bool(latest.get("donchian_20_breakout")),
        donchian_55_breakout=_bool(latest.get("donchian_55_breakout")),
        box_high=_optional_float(latest.get("box_high")),
        box_low=_optional_float(latest.get("box_low")),
        box_width_pct=_optional_float(latest.get("box_width_pct")),
        box_age=_optional_int(latest.get("box_age")),
        box_tightness_score=_num(latest.get("box_tightness_score")),
        box_breakout=_bool(latest.get("box_breakout")),
        box_failure=_bool(latest.get("box_failure")),
        breakout_quality_score=_num(latest.get("breakout_quality_score")),
    )


def _stage_snapshot(latest: dict[str, Any]) -> StageAnalysisResult:
    tags = latest.get("stage_tags") or []
    return StageAnalysisResult(
        stage=str(latest.get("stage") or "Unknown"),
        stage_score=_num(latest.get("stage_score")),
        stage_confidence=str(latest.get("stage_confidence") or "low"),
        stage_tags=list(tags) if isinstance(tags, list) else [],
    )


def _climax_snapshot(latest: dict[str, Any]) -> ClimaxRiskResult:
    reasons = latest.get("climax_risk_reasons") or []
    return ClimaxRiskResult(
        climax_risk_score=_num(latest.get("climax_risk_score")),
        vertical_move_flag=_bool(latest.get("vertical_move_flag")),
        volume_climax_flag=_bool(latest.get("volume_climax_flag"))
        or _bool(latest.get("climax_volume_flag")),
        range_climax_flag=_bool(latest.get("range_climax_flag"))
        or _bool(latest.get("climax_range_flag")),
        upper_wick_rejection=_bool(latest.get("upper_wick_rejection")),
        momentum_crash_risk=_bool(latest.get("momentum_crash_risk")),
        reasons=list(reasons) if isinstance(reasons, list) else [],
    )


def _feature_flags(latest: dict[str, Any]) -> list[str]:
    checks = {
        "vcp_detected": latest.get("vcp_detected"),
        "squeeze_release": latest.get("squeeze_release"),
        "box_breakout": latest.get("box_breakout"),
        "box_failure": latest.get("box_failure"),
        "donchian_20_breakout": latest.get("donchian_20_breakout"),
        "donchian_55_breakout": latest.get("donchian_55_breakout"),
        "stage_2": latest.get("stage") == "Stage 2",
        "vertical_move": latest.get("vertical_move_flag"),
        "volume_climax": latest.get("volume_climax_flag")
        or latest.get("climax_volume_flag"),
        "range_climax": latest.get("range_climax_flag") or latest.get("climax_range_flag"),
        "momentum_crash_risk": latest.get("momentum_crash_risk"),
    }
    return [flag for flag, active in checks.items() if _bool(active)]


def _warning_flags(
    latest: dict[str, Any],
    derived: dict[str, Any],
    warning_flags: list[str] | tuple[str, ...],
) -> list[str]:
    flags = list(warning_flags)
    checks = {
        "failed_breakout": latest.get("failed_breakout"),
        "distribution_risk": latest.get("distribution_risk"),
        "blowoff_top": latest.get("blowoff_top"),
        "box_failure": latest.get("box_failure"),
        "liquidity_warning": latest.get("liquidity_warning"),
        "market_risk_off": derived.get("market_risk_off"),
        "climax_reversal_risk": latest.get("momentum_crash_risk"),
    }
    for flag, active in checks.items():
        if _bool(active) and flag not in flags:
            flags.append(flag)
    return flags


def _sub_tags(latest: dict[str, Any]) -> list[str]:
    checks = {
        "VCP": latest.get("vcp_detected"),
        "Darvas box": latest.get("box_breakout"),
        "Donchian breakout": latest.get("donchian_20_breakout")
        or latest.get("donchian_55_breakout"),
        "Stage 2": latest.get("stage") == "Stage 2",
    }
    return [tag for tag, active in checks.items() if _bool(active)]


def _append_unique(values: list[str], additions: list[str]) -> list[str]:
    for addition in additions:
        if addition not in values:
            values.append(addition)
    return values


def _optional_float(value: Any) -> float | None:
    if _is_missing(value):
        return None
    return round(float(value), 4)


def _optional_int(value: Any) -> int | None:
    if _is_missing(value):
        return None
    return int(float(value))


def _num(value: Any, default: float = 0.0) -> float:
    if _is_missing(value):
        return default
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return default


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
