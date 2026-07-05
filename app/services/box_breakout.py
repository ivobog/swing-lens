from typing import Any

import pandas as pd


def add_box_features(
    df: pd.DataFrame,
    params: dict[str, Any],
) -> pd.DataFrame:
    if not params.get("enabled", True):
        return df

    features = df.copy()
    short_len = int(params.get("donchian_short_len", 20))
    long_len = int(params.get("donchian_long_len", 55))
    box_lookback = int(params.get("box_lookback", 20))
    max_box_width_pct = float(params.get("max_box_width_pct", 15.0))
    min_box_age = int(params.get("min_box_age", 7))
    volume_percentile_min = float(params.get("breakout_volume_percentile_min", 70))
    close_ratio_min = float(params.get("breakout_close_ratio_min", 0.65))
    failure_bars = int(params.get("box_failure_bars", 8))

    features["donchian_20_high"] = _rolling_high(features["high"], short_len)
    features["donchian_20_low"] = _rolling_low(features["low"], short_len)
    features["donchian_55_high"] = _rolling_high(features["high"], long_len)
    features["donchian_55_low"] = _rolling_low(features["low"], long_len)
    features["donchian_20_breakout"] = features["close"] > features["donchian_20_high"]
    features["donchian_55_breakout"] = features["close"] > features["donchian_55_high"]

    features["box_high"] = _rolling_high(features["high"], box_lookback)
    features["box_low"] = _rolling_low(features["low"], box_lookback)
    features["box_width_pct"] = (
        (features["box_high"] - features["box_low"])
        / features["box_low"].replace(0, pd.NA)
        * 100
    )
    features["box_age"] = box_lookback
    features.loc[features["box_high"].isna() | features["box_low"].isna(), "box_age"] = pd.NA
    features["box_tightness_score"] = _box_tightness_score(
        features["box_width_pct"],
        max_box_width_pct,
    )

    box_tight = (
        features["box_width_pct"].notna()
        & (features["box_width_pct"] <= max_box_width_pct)
        & (features["box_age"] >= min_box_age)
    )
    volume_ok = _series(features, "breakout_volume_percentile", 0) >= volume_percentile_min
    close_ok = _series(features, "strong_close_ratio", 0) >= close_ratio_min
    features["box_breakout"] = (features["close"] > features["box_high"]) & box_tight & volume_ok
    features["active_box_breakout_level"] = (
        features["box_high"].where(features["box_breakout"]).ffill()
    )
    features["bars_since_box_breakout"] = _bars_since(features["box_breakout"])
    features["box_failure"] = (
        features["active_box_breakout_level"].notna()
        & (features["bars_since_box_breakout"] <= failure_bars)
        & (features["close"] < features["active_box_breakout_level"])
    )
    features["breakout_quality_score"] = _breakout_quality_score(
        features,
        volume_ok=volume_ok,
        close_ok=close_ok,
    )

    return features


def _rolling_high(series: pd.Series, length: int) -> pd.Series:
    return series.shift(1).rolling(length, min_periods=length).max()


def _rolling_low(series: pd.Series, length: int) -> pd.Series:
    return series.shift(1).rolling(length, min_periods=length).min()


def _box_tightness_score(width_pct: pd.Series, max_width_pct: float) -> pd.Series:
    score = (1.0 - width_pct / max_width_pct).clip(lower=0.0, upper=1.0) * 10
    return score.fillna(0.0).round(4)


def _breakout_quality_score(
    features: pd.DataFrame,
    volume_ok: pd.Series,
    close_ok: pd.Series,
) -> pd.Series:
    score = pd.Series(0.0, index=features.index)
    breakout_signal = (
        features["box_breakout"]
        | features["donchian_20_breakout"]
        | features["donchian_55_breakout"]
    )
    score += breakout_signal.astype(float) * 2.5
    score += volume_ok.fillna(False).astype(float) * 2.0
    score += close_ok.fillna(False).astype(float) * 2.0
    score += (features["box_tightness_score"] / 10 * 1.5).fillna(0.0)
    score += (~_bool_series(features, "gap_exhaustion")).astype(float) * 1.0
    score += (~features["box_failure"]).astype(float) * 1.0
    return score.clip(lower=0.0, upper=10.0).round(4)


def _bars_since(condition: pd.Series) -> pd.Series:
    counter: list[int | None] = []
    last_true_index: int | None = None
    for index, value in enumerate(condition.fillna(False)):
        if bool(value):
            last_true_index = index
            counter.append(0)
        elif last_true_index is None:
            counter.append(None)
        else:
            counter.append(index - last_true_index)
    return pd.Series(counter, index=condition.index, dtype="float")


def _series(features: pd.DataFrame, column: str, default: Any) -> pd.Series:
    if column in features:
        return features[column]
    return pd.Series(default, index=features.index)


def _bool_series(features: pd.DataFrame, column: str) -> pd.Series:
    if column in features:
        return features[column].fillna(False).astype(bool)
    return pd.Series(False, index=features.index)
