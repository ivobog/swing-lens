from __future__ import annotations

from typing import Any

from app.services.sector_rotation_dtos import (
    SectorRotationDecision,
    SectorUniverseMetrics,
)

STATE_INSUFFICIENT_DATA = "Insufficient data"
STATE_RISK_OFF = "Risk-off"
STATE_CROWDED_RISK = "Crowded risk"
STATE_IMPROVING = "Improving"
STATE_FADING = "Fading"
STATE_LEADING = "Leading"
STATE_LAGGING = "Lagging"
STATE_NEUTRAL = "Neutral"

PERMISSION_FULL_ALLOWED = "full_allowed"
PERMISSION_REDUCED_SIZE = "reduced_size"
PERMISSION_WATCH_ONLY = "watch_only"
PERMISSION_AVOID_NEW_LONGS = "avoid_new_longs"


class SectorRotationPolicyService:
    def decide(
        self,
        universe: SectorUniverseMetrics,
        etf: Any | None,
        market_regime: Any | None,
        previous: Any | None,
        config: dict[str, Any],
    ) -> SectorRotationDecision:
        return decide_sector_rotation(
            universe=universe,
            etf=etf,
            market_regime=market_regime,
            previous=previous,
            config=config,
        )


def decide_sector_rotation(
    universe: SectorUniverseMetrics,
    etf: Any | None,
    market_regime: Any | None,
    previous: Any | None,
    config: dict[str, Any],
) -> SectorRotationDecision:
    universe_score = universe.universe_leadership_score
    etf_score = _score_value(etf, "etf_rotation_score")
    final_score, score_source = _final_score(universe_score, etf_score, config)
    previous_rank = _int_or_none(_value(previous, "current_rank", "rank"))
    previous_score = _float_or_none(
        _value(
            previous,
            "sector_final_score",
            "final_score",
            "universe_leadership_score",
        )
    )
    score_change = _score_change(final_score, previous_score)
    rotation_state = _rotation_state(
        universe=universe,
        final_score=final_score,
        score_change=score_change,
        config=config,
    )
    market_bucket = _market_bucket(market_regime)
    permission = _permission_for(
        rotation_state=rotation_state,
        market_bucket=market_bucket,
        config=config,
    )
    multiplier = float(
        config["permissions"]["position_size_multipliers"].get(permission, 0.0)
    )
    warnings = _decision_warnings(
        universe=universe,
        rotation_state=rotation_state,
        market_bucket=market_bucket,
        score_source=score_source,
        config=config,
    )
    reasons = _decision_reasons(
        universe=universe,
        rotation_state=rotation_state,
        permission=permission,
        market_bucket=market_bucket,
        score_change=score_change,
        score_source=score_source,
    )

    return SectorRotationDecision(
        sector=universe.sector,
        sector_slug=universe.sector_slug,
        final_score=final_score,
        universe_score=universe_score,
        etf_score=etf_score,
        rotation_state=rotation_state,
        permission=permission,
        position_size_multiplier=multiplier,
        confidence=universe.confidence,
        rank=None,
        previous_rank=previous_rank,
        rank_change=None,
        score_change=score_change,
        reasons=reasons,
        warnings=warnings,
        debug={
            "score_source": score_source,
            "market_bucket": market_bucket,
            "previous_score": previous_score,
            "danger_share": universe.danger_share,
            "top_25_share": _top_25_share(universe),
        },
    )


def _final_score(
    universe_score: float | None,
    etf_score: float | None,
    config: dict[str, Any],
) -> tuple[float | None, str]:
    etf_enabled = bool(config.get("etf_score", {}).get("enabled", False))
    if etf_enabled and universe_score is not None and etf_score is not None:
        weights = config["combined_score"]["weights"]
        score = universe_score * float(weights["universe"]) + etf_score * float(
            weights["etf"]
        )
        return _clamp(score), "combined"

    if universe_score is not None:
        return _clamp(universe_score), "universe_only"

    if etf_enabled and etf_score is not None:
        return _clamp(etf_score), "etf_only"

    return None, "unavailable"


def _rotation_state(
    universe: SectorUniverseMetrics,
    final_score: float | None,
    score_change: float | None,
    config: dict[str, Any],
) -> str:
    thresholds = config["rotation_states"]
    if universe.confidence == "insufficient" or final_score is None:
        return STATE_INSUFFICIENT_DATA

    danger_share = universe.danger_share or 0.0
    top_25_share = _top_25_share(universe)
    if danger_share >= float(thresholds["danger_share_risk_off_min"]):
        return STATE_RISK_OFF

    if (
        final_score >= float(thresholds["leading_min_score"])
        and top_25_share >= float(thresholds["crowded_top25_share_min"])
    ):
        return STATE_CROWDED_RISK

    if score_change is not None and score_change >= float(
        thresholds["score_change_improving_min"]
    ):
        if final_score >= float(thresholds["improving_min_score"]):
            return STATE_IMPROVING

    if score_change is not None and score_change <= float(
        thresholds["score_change_fading_min"]
    ):
        return STATE_FADING

    if final_score >= float(thresholds["leading_min_score"]):
        return STATE_LEADING
    if final_score <= float(thresholds["lagging_max_score"]):
        return STATE_LAGGING
    return STATE_NEUTRAL


def _permission_for(
    rotation_state: str,
    market_bucket: str,
    config: dict[str, Any],
) -> str:
    buckets = config["permissions"]["market_buckets"]
    bucket = buckets.get(market_bucket) or buckets["unknown"]
    return str(bucket[rotation_state])


def _market_bucket(market_regime: Any | None) -> str:
    if market_regime is None:
        return "unknown"

    risk_off = bool(_value(market_regime, "risk_off"))
    risk_state = str(_value(market_regime, "risk_state") or "").strip()
    regime = str(_value(market_regime, "regime") or market_regime or "").strip()

    if risk_off or risk_state == "Red" or regime in {"Correction", "Crash risk"}:
        return "risk_off"
    if risk_state == "Green" or regime in {"Bull trend", "Risk-on breakout"}:
        return "supportive"
    if risk_state in {"Yellow", "Orange"} or regime in {
        "Bull pullback",
        "Choppy",
        "Bear rally",
        "Distribution",
    }:
        return "choppy"
    return "unknown"


def _decision_reasons(
    universe: SectorUniverseMetrics,
    rotation_state: str,
    permission: str,
    market_bucket: str,
    score_change: float | None,
    score_source: str,
) -> list[str]:
    reasons = list(universe.reason_codes)
    reasons = _append_unique(reasons, f"state_{_code(rotation_state)}")
    reasons = _append_unique(reasons, f"permission_{permission}")
    reasons = _append_unique(reasons, f"market_bucket_{market_bucket}")
    reasons = _append_unique(reasons, f"score_source_{score_source}")
    if score_change is not None:
        if score_change > 0:
            reasons = _append_unique(reasons, "score_improved")
        elif score_change < 0:
            reasons = _append_unique(reasons, "score_declined")
    return reasons


def _decision_warnings(
    universe: SectorUniverseMetrics,
    rotation_state: str,
    market_bucket: str,
    score_source: str,
    config: dict[str, Any],
) -> list[str]:
    warnings = list(universe.warnings)
    if rotation_state == STATE_RISK_OFF:
        warnings = _append_unique(warnings, "sector_risk_off")
    if rotation_state == STATE_CROWDED_RISK:
        warnings = _append_unique(warnings, "sector_crowded_risk")
    if market_bucket == "risk_off":
        warnings = _append_unique(warnings, "market_risk_off")
    if score_source == "universe_only" and bool(config.get("etf_score", {}).get("enabled", False)):
        warnings = _append_unique(warnings, "missing_etf_confirmation")
    return warnings


def _top_25_share(universe: SectorUniverseMetrics) -> float:
    top_25_count = int(universe.top_counts.get("top_25", 0))
    if universe.ticker_count <= 0:
        return 0.0
    return round(top_25_count / universe.ticker_count, 4)


def _score_change(
    final_score: float | None,
    previous_score: float | None,
) -> float | None:
    if final_score is None or previous_score is None:
        return None
    return round(final_score - previous_score, 4)


def _score_value(source: Any | None, *keys: str) -> float | None:
    if source is None:
        return None
    return _float_or_none(_value(source, *keys))


def _value(source: Any | None, *keys: str) -> Any:
    if source is None:
        return None
    if isinstance(source, dict):
        for key in keys:
            if key in source:
                return source[key]
        return None
    for key in keys:
        if hasattr(source, key):
            return getattr(source, key)
    return None


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _append_unique(values: list[str], value: str) -> list[str]:
    if value and value not in values:
        return [*values, value]
    return values


def _code(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _clamp(value: float) -> float:
    return max(0.0, min(10.0, round(float(value), 4)))
