from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ClimaxRiskResult:
    climax_risk_score: float
    vertical_move_flag: bool
    volume_climax_flag: bool
    range_climax_flag: bool
    upper_wick_rejection: bool
    momentum_crash_risk: bool
    reasons: list[str]


def add_climax_risk_features(
    df: pd.DataFrame,
    params: dict[str, Any],
) -> pd.DataFrame:
    if not params.get("enabled", True):
        return df

    features = df.copy()
    atr = features["atr14"].replace(0, np.nan)
    close = features["close"]

    features["move_3d_pct"] = (close / close.shift(3) - 1) * 100
    features["move_5d_pct"] = (close / close.shift(5) - 1) * 100
    features["move_10d_pct"] = (close / close.shift(10) - 1) * 100
    features["move_3d_atr"] = (close - close.shift(3)) / atr
    features["move_5d_atr"] = (close - close.shift(5)) / atr
    features["vertical_move_flag"] = (
        (features["move_3d_atr"] >= float(params.get("vertical_move_3d_atr", 3.0)))
        | (features["move_5d_atr"] >= float(params.get("vertical_move_5d_atr", 4.5)))
    ).fillna(False)

    features["volume_climax_flag"] = _bool_series(features, "climax_volume_flag")
    features["range_climax_flag"] = _bool_series(features, "climax_range_flag")
    features["upper_wick_rejection"] = (
        (_series(features, "upper_wick_pct", 0.0) >= float(params.get("upper_wick_pct", 35.0)))
        & (
            _series(features, "strong_close_ratio", 1.0)
            <= float(params.get("weak_close_ratio", 0.45))
        )
    ).fillna(False)

    results = [calculate_climax_risk(row.to_dict(), params) for _, row in features.iterrows()]
    features["climax_risk_score"] = [result.climax_risk_score for result in results]
    features["momentum_crash_risk"] = [result.momentum_crash_risk for result in results]
    features["climax_risk_reasons"] = [result.reasons for result in results]

    return features


def calculate_climax_risk(
    latest: dict[str, Any],
    params: dict[str, Any],
) -> ClimaxRiskResult:
    threshold = float(params.get("climax_risk_threshold", 7.0))
    rsi_warning = float(params.get("rsi_warning", 75.0))
    rsi_danger = float(params.get("rsi_danger", 80.0))

    vertical_move = _bool(latest.get("vertical_move_flag"))
    volume_climax = _bool(latest.get("volume_climax_flag")) or _bool(
        latest.get("climax_volume_flag")
    )
    range_climax = _bool(latest.get("range_climax_flag")) or _bool(
        latest.get("climax_range_flag")
    )
    upper_wick_rejection = _bool(latest.get("upper_wick_rejection"))

    reasons: list[str] = []
    score = 0.0

    extension_atr = max(
        _num(latest.get("extension_above_ema20_atr"), 0.0),
        _num(latest.get("extension_above_sma50_atr"), 0.0),
    )
    extension_pct = max(
        _num(latest.get("extension_above_ema20_pct"), 0.0),
        _num(latest.get("extension_above_sma50_pct"), 0.0),
    )
    extension_percentile = _num(latest.get("extension_percentile_252"), 0.0)
    if extension_atr >= 6.0 or extension_pct >= 25.0:
        score += 2.0
        reasons.append("extreme_extension")
    elif extension_atr >= 4.0 or extension_pct >= 15.0:
        score += 1.25
        reasons.append("extended")
    if extension_percentile >= 90.0:
        score += 1.0
        reasons.append("extension_percentile")

    rsi = _num(latest.get("rsi14"), 0.0)
    if rsi >= rsi_danger:
        score += 1.5
        reasons.append("rsi_danger")
    elif rsi >= rsi_warning:
        score += 1.0
        reasons.append("rsi_warning")

    if vertical_move:
        score += 1.5
        reasons.append("vertical_move")
    if _bool(latest.get("gap_exhaustion")) or _num(latest.get("gap_up_pct"), 0.0) >= 3.0:
        score += 1.0
        reasons.append("gap_up")
    if volume_climax:
        score += 1.5
        reasons.append("volume_climax")
    if range_climax:
        score += 1.0
        reasons.append("range_climax")
    if upper_wick_rejection:
        score += 1.0
        reasons.append("upper_wick_rejection")

    weak_close_after_vertical = (
        vertical_move and _num(latest.get("strong_close_ratio"), 1.0) <= 0.45
    )
    if weak_close_after_vertical:
        score += 1.0
        reasons.append("weak_close_after_vertical_move")

    score = round(min(max(score, 0.0), 10.0), 4)
    momentum_crash_risk = (
        score >= threshold
        and (vertical_move or extension_atr >= 4.0 or extension_pct >= 15.0)
        and (volume_climax or range_climax)
    )

    return ClimaxRiskResult(
        climax_risk_score=score,
        vertical_move_flag=vertical_move,
        volume_climax_flag=volume_climax,
        range_climax_flag=range_climax,
        upper_wick_rejection=upper_wick_rejection,
        momentum_crash_risk=momentum_crash_risk,
        reasons=reasons,
    )


def _series(features: pd.DataFrame, column: str, default: Any) -> pd.Series:
    if column in features:
        return features[column]
    return pd.Series(default, index=features.index)


def _bool_series(features: pd.DataFrame, column: str) -> pd.Series:
    if column in features:
        return features[column].fillna(False).astype(bool)
    return pd.Series(False, index=features.index)


def _num(value: Any, default: float = 0.0) -> float:
    if value is None or pd.isna(value):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bool(value: Any) -> bool:
    if value is None or pd.isna(value):
        return False
    return bool(value)
