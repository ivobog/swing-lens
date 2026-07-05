from typing import Any

import numpy as np
import pandas as pd


def add_adaptive_features(
    df: pd.DataFrame,
    params: dict[str, Any],
) -> pd.DataFrame:
    if not params.get("enabled", True):
        return df

    features = df.copy()
    long_lookback = int(params.get("long_lookback", 252))
    medium_lookback = int(params.get("medium_lookback", 126))

    _ensure_base_columns(features)

    features["atr_percentile_252"] = rolling_percentile(
        features["atr_pct"],
        long_lookback,
    )
    features["atr_percentile_126"] = rolling_percentile(
        features["atr_pct"],
        medium_lookback,
    )
    features["volume_percentile_252"] = rolling_percentile(
        features["volume"],
        long_lookback,
    )
    features["notional_volume_percentile_252"] = rolling_percentile(
        features["notional_volume"],
        long_lookback,
    )
    features["breakout_volume_percentile"] = features["volume_percentile_252"]
    features["range_percentile_252"] = rolling_percentile(
        features["candle_range_pct"],
        long_lookback,
    )

    features["extension_above_ema20_pct"] = (
        (features["close"] / features["ema20"] - 1) * 100
    )
    features["extension_above_sma50_pct"] = (
        (features["close"] / features["sma50"] - 1) * 100
    )
    features["extension_above_ema20_atr"] = (
        (features["close"] - features["ema20"]) / features["atr14"].replace(0, np.nan)
    )
    features["extension_above_sma50_atr"] = (
        (features["close"] - features["sma50"]) / features["atr14"].replace(0, np.nan)
    )
    extension_magnitude = pd.concat(
        [
            features["extension_above_ema20_pct"].clip(lower=0),
            features["extension_above_sma50_pct"].clip(lower=0),
        ],
        axis=1,
    ).max(axis=1)
    features["extension_percentile_252"] = rolling_percentile(
        extension_magnitude,
        long_lookback,
    )

    features["atr_expansion_flag"] = features["atr_percentile_252"] >= float(
        params.get("atr_expansion_percentile", 80)
    )
    features["atr_contraction_flag"] = features["atr_percentile_252"] <= float(
        params.get("atr_contraction_percentile", 35)
    )
    features["climax_volume_flag"] = features["volume_percentile_252"] >= float(
        params.get("volume_climax_percentile", 90)
    )
    features["narrow_range_flag"] = features["range_percentile_252"] <= float(
        params.get("atr_contraction_percentile", 35)
    )
    features["wide_range_flag"] = features["range_percentile_252"] >= float(
        params.get("atr_expansion_percentile", 80)
    )
    features["climax_range_flag"] = features["range_percentile_252"] >= float(
        params.get("range_climax_percentile", 90)
    )

    return features


def rolling_percentile(series: pd.Series, lookback: int) -> pd.Series:
    return series.rolling(lookback, min_periods=lookback).apply(
        _percentile_of_current,
        raw=True,
    )


def _ensure_base_columns(features: pd.DataFrame) -> None:
    if "notional_volume" not in features:
        features["notional_volume"] = features["volume"] * features["close"]
    if "candle_range" not in features:
        features["candle_range"] = features["high"] - features["low"]
    if "candle_range_pct" not in features:
        features["candle_range_pct"] = (
            features["candle_range"] / features["close"].replace(0, np.nan) * 100
        )


def _percentile_of_current(window: np.ndarray) -> float:
    current = window[-1]
    if np.isnan(current):
        return np.nan
    valid = window[~np.isnan(window)]
    if len(valid) == 0:
        return np.nan
    return float((valid <= current).sum() / len(valid) * 100)
