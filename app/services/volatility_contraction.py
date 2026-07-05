from typing import Any

import numpy as np
import pandas as pd

from app.services.adaptive_technical_features import rolling_percentile


def add_contraction_features(
    df: pd.DataFrame,
    params: dict[str, Any],
) -> pd.DataFrame:
    if not params.get("enabled", True):
        return df

    features = df.copy()
    bb_length = int(params.get("bb_length", 20))
    bb_stddev = float(params.get("bb_stddev", 2.0))
    kc_length = int(params.get("kc_length", 20))
    kc_atr_multiple = float(params.get("kc_atr_multiple", 1.5))
    tight_short = int(params.get("tight_close_lookback_short", 5))
    tight_long = int(params.get("tight_close_lookback_long", 10))
    tight_close_max_pct = float(params.get("tight_close_max_pct", 2.0))
    vcp_min_score = float(params.get("vcp_min_score", 7.0))

    close = features["close"]
    features["bb_mid"] = close.rolling(bb_length, min_periods=bb_length).mean()
    bb_std = close.rolling(bb_length, min_periods=bb_length).std()
    features["bb_upper"] = features["bb_mid"] + bb_std * bb_stddev
    features["bb_lower"] = features["bb_mid"] - bb_std * bb_stddev
    features["bb_width_pct"] = (
        (features["bb_upper"] - features["bb_lower"])
        / features["bb_mid"].replace(0, np.nan)
        * 100
    )
    features["bb_width_percentile_252"] = rolling_percentile(
        features["bb_width_pct"],
        int(params.get("bb_width_percentile_lookback", 252)),
    )

    features["kc_mid"] = close.ewm(
        span=kc_length,
        adjust=False,
        min_periods=kc_length,
    ).mean()
    kc_atr = _atr_input(features, kc_length)
    features["kc_upper"] = features["kc_mid"] + kc_atr * kc_atr_multiple
    features["kc_lower"] = features["kc_mid"] - kc_atr * kc_atr_multiple

    squeeze_enabled = bool(params.get("squeeze_enabled", True))
    squeeze_on = (
        (features["bb_upper"] < features["kc_upper"])
        & (features["bb_lower"] > features["kc_lower"])
    )
    features["squeeze_on"] = squeeze_on.fillna(False) if squeeze_enabled else False
    features["squeeze_release"] = (
        features["squeeze_on"].shift(1).fillna(False) & ~features["squeeze_on"]
    )

    close_change_pct = close.pct_change().abs() * 100
    tight_close = close_change_pct <= tight_close_max_pct
    features["tight_close_count_5"] = tight_close.rolling(
        tight_short,
        min_periods=tight_short,
    ).sum()
    features["tight_close_count_10"] = tight_close.rolling(
        tight_long,
        min_periods=tight_long,
    ).sum()
    features["tight_close_score"] = (
        features["tight_close_count_10"] / max(tight_long, 1) * 10
    )

    features["atr_contraction"] = _atr_contraction(features)
    features["range_contraction"] = _range_contraction(features)
    features["volume_dry_up_quality"] = _volume_dry_up_quality(features)
    features["vcp_score"] = _vcp_score(features)
    features["vcp_detected"] = features["vcp_score"] >= vcp_min_score

    return features


def _atr_input(features: pd.DataFrame, kc_length: int) -> pd.Series:
    if "atr14" in features:
        return features["atr14"]
    previous_close = features["close"].shift(1)
    true_range = pd.concat(
        [
            features["high"] - features["low"],
            (features["high"] - previous_close).abs(),
            (features["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / kc_length, adjust=False, min_periods=kc_length).mean()


def _atr_contraction(features: pd.DataFrame) -> pd.Series:
    percentile_ok = _bool_series(features.get("atr_contraction_flag"), features.index)
    atr_declining = features["atr14"] < features["atr14"].shift(5)
    return (percentile_ok & atr_declining.fillna(False)).fillna(False)


def _range_contraction(features: pd.DataFrame) -> pd.Series:
    percentile_ok = _bool_series(features.get("narrow_range_flag"), features.index)
    range_declining = features["candle_range"] < features["candle_range"].shift(5)
    return (percentile_ok | range_declining.fillna(False)).fillna(False)


def _volume_dry_up_quality(features: pd.DataFrame) -> pd.Series:
    avg_volume = features.get("avg_volume")
    short_volume = features.get("short_volume_avg")
    if avg_volume is None or short_volume is None:
        return pd.Series(0.0, index=features.index)
    ratio = short_volume / avg_volume.replace(0, np.nan)
    quality = (1.0 - ratio).clip(lower=0.0, upper=1.0) * 10
    return quality.fillna(0.0)


def _vcp_score(features: pd.DataFrame) -> pd.Series:
    score = pd.Series(0.0, index=features.index)
    score += _bool_series(features.get("had_pullback"), features.index).astype(float) * 1.5
    score += _bool_series(features.get("not_too_deep"), features.index).astype(float) * 1.2
    score += _bool_series(features.get("held_near_support"), features.index).astype(float) * 1.2
    score += _bool_series(features.get("atr_contraction"), features.index).astype(float) * 1.0
    score += _bool_series(features.get("range_contraction"), features.index).astype(float) * 1.0
    score += _bool_series(features.get("volume_dry_up"), features.index).astype(float) * 1.0
    score += _bool_series(features.get("red_volume_declining"), features.index).astype(float) * 0.8
    score += _bool_series(features.get("higher_low"), features.index).astype(float) * 0.8
    score += (features["tight_close_score"] >= 7.0).fillna(False).astype(float) * 0.8
    score -= (features.get("distribution_count", 0) >= 3).astype(float) * 1.5
    return score.clip(lower=0.0, upper=10.0).round(4)


def _bool_series(value: Any, index: pd.Index) -> pd.Series:
    if value is None:
        return pd.Series(False, index=index)
    if isinstance(value, pd.Series):
        return value.fillna(False).astype(bool)
    return pd.Series(bool(value), index=index)
