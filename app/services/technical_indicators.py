from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

REQUIRED_COLUMNS = ("date", "open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class TechnicalFeatureResult:
    ticker: str
    insufficient_data: bool
    missing_data: dict[str, Any]
    latest: dict[str, Any]
    debug: dict[str, Any]


def load_pine_defaults(path: Path = Path("config/pine_defaults.yaml")) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def calculate_technical_features(
    price_df: pd.DataFrame,
    trades_df: pd.DataFrame | None = None,
    ticker: str = "",
    params: dict[str, Any] | None = None,
) -> TechnicalFeatureResult:
    params = params or load_pine_defaults()
    df = prepare_ohlcv_frame(price_df, trades_df)
    missing_data = _missing_data(df, params)
    insufficient_data = bool(missing_data["insufficient_history"])

    if df.empty:
        return TechnicalFeatureResult(
            ticker=ticker,
            insufficient_data=True,
            missing_data={"empty": True, "insufficient_history": True},
            latest={},
            debug={},
        )

    features = _calculate_feature_frame(df, params)
    latest = _latest_features(features)
    debug = {
        "row_count": len(df),
        "start_date": str(df["date"].iloc[0].date()),
        "end_date": str(df["date"].iloc[-1].date()),
        "columns": list(df.columns),
    }

    return TechnicalFeatureResult(
        ticker=ticker,
        insufficient_data=insufficient_data,
        missing_data=missing_data,
        latest=latest,
        debug=debug,
    )


def calculate_relative_strength_features(
    stock_df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    sector_df: pd.DataFrame | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    params = params or load_pine_defaults()
    market_rs = params["market_rs"]
    stock = prepare_ohlcv_frame(stock_df)
    benchmark = prepare_ohlcv_frame(benchmark_df)
    aligned = stock[["date", "close"]].merge(
        benchmark[["date", "close"]],
        on="date",
        suffixes=("_stock", "_benchmark"),
    )
    aligned["rs_line"] = aligned["close_stock"] / aligned["close_benchmark"]
    aligned["rs_sma"] = sma(aligned["rs_line"], market_rs["rsSmaLen"])
    aligned["rs_roc21"] = roc_pct(aligned["rs_line"], market_rs["rocShortLen"])
    aligned["rs_roc63"] = roc_pct(aligned["rs_line"], market_rs["rocMediumLen"])
    aligned["rs_roc126"] = roc_pct(aligned["rs_line"], market_rs["rocLongLen"])
    aligned["rs_new_high"] = aligned["rs_line"] >= aligned["rs_line"].rolling(
        market_rs["rsNewHighLookback"],
        min_periods=market_rs["rsNewHighLookback"],
    ).max()

    latest = {
        f"benchmark_{key}": value
        for key, value in _latest_features(aligned).items()
        if key not in {"close_stock", "close_benchmark"}
    }

    if sector_df is not None and not sector_df.empty:
        sector = prepare_ohlcv_frame(sector_df)
        sector_aligned = stock[["date", "close"]].merge(
            sector[["date", "close"]],
            on="date",
            suffixes=("_stock", "_sector"),
        )
        sector_aligned["rs_line"] = sector_aligned["close_stock"] / sector_aligned[
            "close_sector"
        ]
        sector_aligned["rs_sma"] = sma(sector_aligned["rs_line"], market_rs["rsSmaLen"])
        sector_aligned["rs_roc21"] = roc_pct(sector_aligned["rs_line"], market_rs["rocShortLen"])
        latest.update(
            {
                f"sector_{key}": value
                for key, value in _latest_features(sector_aligned).items()
                if key not in {"close_stock", "close_sector"}
            }
        )

    return latest


def calculate_htf_trend_features(
    price_df: pd.DataFrame,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    params = params or load_pine_defaults()
    htf = params["htf"]
    weekly = resample_weekly_ohlcv(prepare_ohlcv_frame(price_df))
    weekly["htf_ema_fast"] = ema(weekly["close"], htf["htfFastLen"])
    weekly["htf_sma_mid"] = sma(weekly["close"], htf["htfMidLen"])
    weekly["htf_sma_slow"] = sma(weekly["close"], htf["htfSlowLen"])
    weekly["htf_mid_slope_pct"] = slope_pct(weekly["htf_sma_mid"], htf["htfSlopeLookback"])
    weekly["htf_slow_slope_pct"] = slope_pct(weekly["htf_sma_slow"], htf["htfSlopeLookback"])
    weekly["htf_roc"] = roc_pct(weekly["close"], htf["htfRocLookback"])
    weekly["htf_close_above_mid"] = weekly["close"] > weekly["htf_sma_mid"]
    weekly["htf_mid_above_slow"] = weekly["htf_sma_mid"] > weekly["htf_sma_slow"]
    if htf.get("useConfirmedHtf", True) and len(weekly) > 1:
        return _features_at(weekly, -2)
    return _latest_features(weekly)


def resample_weekly_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    normalized = prepare_ohlcv_frame(df)
    weekly = (
        normalized.set_index("date")
        .resample("W-FRI")
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        .dropna(subset=["open", "high", "low", "close"])
        .reset_index()
    )
    return weekly


def prepare_ohlcv_frame(
    price_df: pd.DataFrame,
    trades_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    _validate_columns(price_df)
    prepared = _normalize_frame(price_df)

    if trades_df is not None and not trades_df.empty:
        _validate_columns(trades_df)
        trades = _normalize_frame(trades_df)
        prepared = prepared.drop(columns=["volume"]).merge(
            trades[["date", "volume"]],
            on="date",
            how="left",
        )
        prepared["volume"] = prepared["volume"].fillna(0)

    return prepared.sort_values("date").reset_index(drop=True)


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False, min_periods=length).mean()


def sma(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(length, min_periods=length).mean()


def rma(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()


def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = rma(gain, length)
    avg_loss = rma(loss, length)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    value = 100 - (100 / (1 + rs))
    return value.fillna(100).where(avg_loss != 0, 100)


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    previous_close = close.shift(1)
    ranges = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    return rma(true_range(high, low, close), length)


def dmi_adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    length: int = 14,
    smoothing: int = 14,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=high.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=high.index,
    )
    atr_value = atr(high, low, close, length)
    plus_di = 100 * rma(plus_dm, length) / atr_value.replace(0, np.nan)
    minus_di = 100 * rma(minus_dm, length) / atr_value.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = rma(dx, smoothing)
    return plus_di, minus_di, adx


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff()).fillna(0)
    return (direction * volume).cumsum()


def roc_pct(series: pd.Series, length: int) -> pd.Series:
    return (series / series.shift(length) - 1) * 100


def slope_pct(series: pd.Series, length: int) -> pd.Series:
    return (series - series.shift(length)) / series.shift(length).abs() * 100


def rolling_sum(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(length, min_periods=length).sum()


def pivot_high(high: pd.Series, left: int, right: int) -> pd.Series:
    window = left + right + 1
    centered_max = high.rolling(window, center=True, min_periods=window).max()
    return high.where(high == centered_max)


def pivot_low(low: pd.Series, left: int, right: int) -> pd.Series:
    window = left + right + 1
    centered_min = low.rolling(window, center=True, min_periods=window).min()
    return low.where(low == centered_min)


def _calculate_feature_frame(df: pd.DataFrame, params: dict[str, Any]) -> pd.DataFrame:
    trend = params["trend"]
    momentum = params["momentum"]
    pullback_breakout = params["pullback_breakout"]
    risk = params["risk"]
    stop_target = params["stop_target"]

    features = df.copy()
    close = features["close"]
    high = features["high"]
    low = features["low"]
    open_ = features["open"]
    volume = features["volume"]

    features["ema10"] = ema(close, trend["emaFastLen"])
    features["ema20"] = ema(close, trend["emaPullbackLen"])
    features["sma50"] = sma(close, trend["smaMidLen"])
    features["sma150"] = sma(close, trend["smaTrendLen"])
    features["sma200"] = sma(close, trend["smaSlowLen"])
    features["sma50_slope_pct"] = slope_pct(features["sma50"], trend["midSlopeLookback"])
    features["sma200_slope_pct"] = slope_pct(features["sma200"], trend["slowSlopeLookback"])

    features["rsi14"] = rsi(close, momentum["rsiLen"])
    features["atr14"] = atr(high, low, close, momentum["atrLen"])
    features["atr_pct"] = features["atr14"] / close * 100
    features["sma50_slope_atr"] = (
        (features["sma50"] - features["sma50"].shift(trend["midSlopeLookback"]))
        / features["atr14"].replace(0, np.nan)
    )
    features["sma200_slope_atr"] = (
        (features["sma200"] - features["sma200"].shift(trend["slowSlopeLookback"]))
        / features["atr14"].replace(0, np.nan)
    )
    features["avg_volume"] = sma(volume, momentum["volLen"])
    features["volume_ratio"] = volume / features["avg_volume"].replace(0, np.nan)
    features["obv"] = obv(close, volume)
    features["obv_sma"] = sma(features["obv"], momentum["obvSmaLen"])
    features["obv_rising"] = features["obv"] > features["obv"].shift(momentum["obvSlopeLookback"])

    plus_di, minus_di, adx = dmi_adx(high, low, close, trend["adxLen"], trend["adxSmoothing"])
    features["plus_di"] = plus_di
    features["minus_di"] = minus_di
    features["adx"] = adx
    features["adx_rising"] = features["adx"] > features["adx"].shift(1)
    features["plus_di_rising"] = features["plus_di"] > features["plus_di"].shift(1)
    features["rsi_rising"] = features["rsi14"] > features["rsi14"].shift(1)

    features["roc21"] = roc_pct(close, params["market_rs"]["rocShortLen"])
    features["roc63"] = roc_pct(close, params["market_rs"]["rocMediumLen"])
    features["roc126"] = roc_pct(close, params["market_rs"]["rocLongLen"])
    features["high_52w"] = high.rolling(
        trend["highLow52Len"],
        min_periods=trend["highLow52Len"],
    ).max()
    features["low_52w"] = low.rolling(
        trend["highLow52Len"],
        min_periods=trend["highLow52Len"],
    ).min()
    features["position_52w"] = (
        (close - features["low_52w"])
        / (features["high_52w"] - features["low_52w"]).replace(0, np.nan)
        * 100
    )
    features["near_52_high"] = ((features["high_52w"] - close) / features["high_52w"] * 100) <= 15
    features["close_10_high"] = close.shift(1).rolling(10, min_periods=10).max()
    features["close_20_high"] = close.shift(1).rolling(20, min_periods=20).max()
    features["above_close_10_high"] = close > features["close_10_high"]
    features["above_close_20_high"] = close > features["close_20_high"]

    features["pivot_high"] = pivot_high(high, trend["pivotLeftBars"], trend["pivotRightBars"])
    features["pivot_low"] = pivot_low(low, trend["pivotLeftBars"], trend["pivotRightBars"])
    features["higher_high"] = _higher_last_pivot(features["pivot_high"])
    features["higher_low"] = _higher_last_pivot(features["pivot_low"])

    features["prior_high"], features["recent_low_after_high"] = _pullback_geometry(
        high,
        low,
        pullback_breakout["pullbackLookback"],
    )
    features["pullback_depth_pct"] = (
        (features["prior_high"] - features["recent_low_after_high"])
        / features["prior_high"].replace(0, np.nan)
        * 100
    ).fillna(0)
    features["rsi_pullback_low"] = features["rsi14"].rolling(
        pullback_breakout["pullbackLookback"],
        min_periods=pullback_breakout["pullbackLookback"],
    ).min()
    features["had_pullback"] = features["pullback_depth_pct"] >= pullback_breakout["minPullbackPct"]
    features["not_too_deep"] = features["pullback_depth_pct"] <= pullback_breakout["maxPullbackPct"]
    features["near_ema20"] = _near_level_within_lookback(
        low,
        features["ema20"],
        close,
        pullback_breakout["maTouchPct"],
        pullback_breakout["pullbackLookback"],
    )
    features["near_sma50"] = _near_level_within_lookback(
        low,
        features["sma50"],
        close,
        pullback_breakout["maTouchPct"],
        pullback_breakout["pullbackLookback"],
    )
    features["near_sma200"] = _near_level_within_lookback(
        low,
        features["sma200"],
        close,
        pullback_breakout["maTouchPct"],
        pullback_breakout["pullbackLookback"],
    )
    features["held_near_support"] = (
        features["near_ema20"] | features["near_sma50"] | features["near_sma200"]
    )

    features["previous_resistance"] = (
        high.shift(1).rolling(pullback_breakout["breakoutLookback"]).max()
    )
    features["near_resistance"] = _near_level(
        close,
        features["previous_resistance"],
        risk["nearResistancePct"],
    ) & (features["previous_resistance"] >= close)
    features["fresh_breakout"] = (
        (close > features["previous_resistance"])
        & (features["volume_ratio"] >= pullback_breakout["breakoutVolRatio"])
    )
    features["active_breakout_level"] = (
        features["previous_resistance"].where(features["fresh_breakout"]).ffill()
    )
    features["bars_since_breakout"] = _bars_since(features["fresh_breakout"])
    features["failed_breakout"] = (
        features["active_breakout_level"].notna()
        & (features["bars_since_breakout"] <= risk["failedBreakoutBars"])
        & (close < features["active_breakout_level"])
        & (features["volume_ratio"] >= pullback_breakout["failureVolRatio"])
    )

    green_volume = volume.where(close >= open_)
    red_volume = volume.where(close < open_)
    features["green_volume_avg"] = sma(green_volume, momentum["greenRedVolLookback"])
    features["red_volume_avg"] = sma(red_volume, momentum["greenRedVolLookback"])
    features["green_beats_red"] = features["green_volume_avg"] > features["red_volume_avg"]
    features["recent_red_volume"] = red_volume.rolling(momentum["recentRedVolLookback"]).mean()
    features["prior_red_volume"] = features["recent_red_volume"].shift(
        momentum["recentRedVolLookback"]
    )
    features["red_volume_declining"] = features["recent_red_volume"] < features["prior_red_volume"]
    features["volume_dry_up"] = volume < (features["avg_volume"] * 0.6)
    features["notional_volume"] = volume * close
    features["avg_notional_volume"] = sma(
        features["notional_volume"],
        risk["notionalVolumeLookback"],
    )
    features["liquidity_warning"] = (
        (features["avg_volume"] < risk["minAvgVolume"])
        | (features["avg_notional_volume"] < risk["minNotionalVolume"])
    )

    candle_range = (high - low).replace(0, np.nan)
    features["candle_range"] = high - low
    features["strong_close_ratio"] = (close - low) / candle_range
    features["strong_close"] = features["strong_close_ratio"] >= 0.65
    features["upper_wick_pct"] = (high - close.combine(open_, max)) / candle_range * 100
    features["heavy_red_candle"] = (close < open_) & (
        features["volume_ratio"] >= risk["heavyRedVolRatio"]
    )
    features["gap_up_pct"] = (open_ / close.shift(1) - 1) * 100
    features["gap_exhaustion"] = (
        (features["gap_up_pct"] >= risk["gapExhaustionPct"])
        & (features["volume_ratio"] >= risk["gapExhaustionVolRatio"])
        & (close < open_)
    )
    distribution_bar = (close < close.shift(1)) & (volume > volume.shift(1))
    features["distribution_count"] = rolling_sum(
        distribution_bar.astype(float),
        momentum["distributionLookback"],
    )
    features["extension_above_sma50_pct"] = (close / features["sma50"] - 1) * 100
    features["price_rising"] = close > close.shift(1)
    features["blowoff_top"] = (
        (features["extension_above_sma50_pct"] >= risk["extensionDangerPct"])
        & (features["rsi14"] >= 80)
        & (features["gap_exhaustion"] | features["heavy_red_candle"])
    )
    features["distribution_risk"] = (
        features["distribution_count"] >= 4
    ) | features["heavy_red_candle"] | features["failed_breakout"]

    structure_stop = (
        features["pivot_low"].ffill() - features["atr14"] * stop_target["structureAtrBuffer"]
    )
    atr_stop = close - features["atr14"] * stop_target["atrStopMultiple"]
    features["suggested_stop"] = pd.concat([structure_stop, atr_stop], axis=1).max(axis=1)
    features["entry_risk_pct"] = (close - features["suggested_stop"]) / close * 100
    features["suggested_target"] = close + (close - features["suggested_stop"]) * stop_target[
        "targetRewardMultiple"
    ]
    features["reward_risk"] = (features["suggested_target"] - close) / (
        close - features["suggested_stop"]
    ).replace(0, np.nan)

    return features


def _latest_features(features: pd.DataFrame) -> dict[str, Any]:
    row = features.iloc[-1]
    return _row_features(features, row)


def _features_at(features: pd.DataFrame, index: int) -> dict[str, Any]:
    row = features.iloc[index]
    return _row_features(features, row)


def _row_features(features: pd.DataFrame, row: pd.Series) -> dict[str, Any]:
    return {
        column: _to_python_value(row[column])
        for column in features.columns
        if column != "date"
    }


def _higher_last_pivot(pivots: pd.Series) -> pd.Series:
    values: list[bool] = []
    last_pivot: float | None = None
    previous_pivot: float | None = None
    for value in pivots:
        if pd.notna(value):
            previous_pivot = last_pivot
            last_pivot = float(value)
        values.append(
            last_pivot is not None
            and previous_pivot is not None
            and last_pivot > previous_pivot
        )
    return pd.Series(values, index=pivots.index)


def _near_level(series: pd.Series, level: pd.Series, pct: float) -> pd.Series:
    return ((series - level).abs() / level.replace(0, np.nan) * 100) <= pct


def _near_level_within_lookback(
    series: pd.Series,
    level: pd.Series,
    denominator: pd.Series,
    pct: float,
    lookback: int,
) -> pd.Series:
    distance = (series - level).abs() / denominator.replace(0, np.nan) * 100
    return distance.rolling(lookback, min_periods=1).min() <= pct


def _pullback_geometry(
    high: pd.Series,
    low: pd.Series,
    lookback: int,
) -> tuple[pd.Series, pd.Series]:
    prior_highs: list[float | None] = []
    recent_lows: list[float | None] = []
    for index in range(len(high)):
        window_start = max(0, index - lookback)
        prior_window = high.iloc[window_start:index]
        if prior_window.empty or prior_window.isna().all():
            prior_highs.append(None)
            recent_lows.append(None)
            continue

        prior_high_index = int(prior_window.idxmax())
        prior_highs.append(float(high.iloc[prior_high_index]))
        low_after_high = low.iloc[prior_high_index + 1 : index + 1]
        if low_after_high.empty or low_after_high.isna().all():
            recent_lows.append(None)
        else:
            recent_lows.append(float(low_after_high.min()))
    return pd.Series(prior_highs, index=high.index), pd.Series(recent_lows, index=low.index)


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


def _missing_data(df: pd.DataFrame, params: dict[str, Any]) -> dict[str, Any]:
    slow_len = int(params["trend"]["smaSlowLen"])
    high_low_len = int(params["trend"]["highLow52Len"])
    roc_long_len = int(params["market_rs"]["rocLongLen"])
    required = max(slow_len, high_low_len, roc_long_len)
    return {
        "row_count": len(df),
        "required_rows": required,
        "insufficient_history": len(df) < required,
        "missing_columns": [column for column in REQUIRED_COLUMNS if column not in df.columns],
    }


def _validate_columns(df: pd.DataFrame) -> None:
    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f"OHLCV frame is missing columns: {', '.join(missing)}")


def _normalize_frame(df: pd.DataFrame) -> pd.DataFrame:
    normalized = df.loc[:, REQUIRED_COLUMNS].copy()
    normalized["date"] = pd.to_datetime(normalized["date"])
    for column in ("open", "high", "low", "close", "volume"):
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    normalized = normalized.dropna(subset=["date", "open", "high", "low", "close"])
    normalized["volume"] = normalized["volume"].fillna(0)
    return normalized


def _to_python_value(value: Any) -> Any:
    if pd.isna(value):
        return None
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
