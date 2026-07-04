from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import pytest

from app.models.tables import PriceBar
from app.services import price_bar_repository
from app.services.price_bar_repository import load_price_bars_frame
from app.services.technical_indicators import (
    _higher_last_pivot,
    calculate_htf_trend_features,
    calculate_relative_strength_features,
    calculate_technical_features,
    ema,
    prepare_ohlcv_frame,
    resample_weekly_ohlcv,
    roc_pct,
    rsi,
    sma,
)


def test_sma_ema_and_roc_primitives() -> None:
    series = pd.Series([1, 2, 3, 4, 5], dtype="float")

    assert sma(series, 3).iloc[-1] == 4
    assert round(ema(series, 3).iloc[-1], 4) == 4.0625
    assert roc_pct(series, 2).iloc[-1] == pytest.approx(66.66666666666666)


def test_rsi_returns_high_value_for_steady_uptrend() -> None:
    close = pd.Series(range(1, 40), dtype="float")

    latest = rsi(close, 14).iloc[-1]

    assert latest == 100


def test_prepare_ohlcv_uses_trade_volume_when_available() -> None:
    dates = pd.date_range("2026-01-01", periods=3)
    price = pd.DataFrame(
        {
            "date": dates,
            "open": [10, 11, 12],
            "high": [11, 12, 13],
            "low": [9, 10, 11],
            "close": [10.5, 11.5, 12.5],
            "volume": [0, 0, 0],
        }
    )
    trades = price.assign(volume=[100, 200, 300])

    prepared = prepare_ohlcv_frame(price, trades)

    assert prepared["volume"].tolist() == [100, 200, 300]


def test_preferred_ohlcv_uses_trades_prices_for_tradingview_parity(monkeypatch) -> None:
    adjusted = _synthetic_ohlcv(rows=3).assign(close=[10.0, 10.0, 10.0])
    trades = _synthetic_ohlcv(rows=3).assign(close=[11.0, 12.0, 13.0])

    def fake_load_price_bars_frame(db, ticker, what_to_show, timeframe="1 day"):
        return adjusted if what_to_show == "ADJUSTED_LAST" else trades

    monkeypatch.setattr(
        price_bar_repository,
        "load_price_bars_frame",
        fake_load_price_bars_frame,
    )

    price, volume = price_bar_repository.load_preferred_ohlcv_frames(object(), "MSFT")

    assert price["close"].tolist() == [11.0, 12.0, 13.0]
    assert volume is not None
    assert volume["close"].tolist() == [11.0, 12.0, 13.0]


def test_calculate_technical_features_latest_values() -> None:
    frame = _synthetic_ohlcv(rows=320)

    result = calculate_technical_features(frame, ticker="TEST")

    assert result.ticker == "TEST"
    assert result.insufficient_data is False
    assert result.latest["sma200"] is not None
    assert result.latest["rsi14"] is not None
    assert result.latest["atr14"] is not None
    assert result.latest["roc126"] is not None
    assert "fresh_breakout" in result.latest
    assert "suggested_stop" in result.latest
    assert result.debug["row_count"] == 320


def test_green_beats_red_uses_pine_sma_semantics() -> None:
    frame = _synthetic_ohlcv(rows=320)
    last_ten = frame.tail(10).index
    frame.loc[last_ten, ["open", "high", "low", "close"]] = [100.0, 102.0, 99.0, 101.0]
    frame.loc[last_ten, "volume"] = 100
    red_index = last_ten[0]
    frame.loc[red_index, ["open", "close"]] = [101.0, 100.0]
    frame.loc[red_index, "volume"] = 150

    result = calculate_technical_features(frame, ticker="TEST")

    assert result.latest["green_volume_avg"] == pytest.approx(90.0)
    assert result.latest["red_volume_avg"] == pytest.approx(15.0)
    assert result.latest["green_beats_red"] is True


def test_failed_breakout_window_uses_zero_based_bar_count() -> None:
    frame = _flat_ohlcv(rows=320)
    breakout_index = frame.index[-9]
    frame.loc[breakout_index, ["open", "high", "low", "close", "volume"]] = [
        100.0,
        106.0,
        99.0,
        105.0,
        2_000_000,
    ]
    after_breakout = frame.index[-8:]
    frame.loc[after_breakout, ["open", "high", "low", "close", "volume"]] = [
        101.0,
        102.0,
        100.0,
        101.0,
        1_000_000,
    ]
    frame.loc[frame.index[-1], ["open", "high", "low", "close", "volume"]] = [
        101.0,
        101.0,
        98.0,
        99.0,
        2_000_000,
    ]

    result = calculate_technical_features(frame, ticker="TEST")

    assert result.latest["bars_since_breakout"] == 8.0
    assert result.latest["volume_ratio"] >= 1.1
    assert result.latest["failed_breakout"] is False


def test_higher_last_pivot_tracks_previous_confirmed_pivot() -> None:
    pivots = pd.Series([None, 10.0, None, None, 12.0, None, None])

    higher = _higher_last_pivot(pivots)

    assert higher.tolist() == [False, False, False, False, True, True, True]


def test_calculate_technical_features_marks_insufficient_history() -> None:
    result = calculate_technical_features(_synthetic_ohlcv(rows=40), ticker="SHORT")

    assert result.insufficient_data is True
    assert result.missing_data["required_rows"] == 252
    assert result.latest["sma200"] is None


def test_relative_strength_features_compare_stock_to_benchmark() -> None:
    stock = _synthetic_ohlcv(rows=180)
    benchmark = _synthetic_ohlcv(rows=180).assign(
        close=lambda frame: frame["close"] * 0.9,
        high=lambda frame: frame["high"] * 0.9,
        low=lambda frame: frame["low"] * 0.9,
        open=lambda frame: frame["open"] * 0.9,
    )

    features = calculate_relative_strength_features(stock, benchmark)

    assert features["benchmark_rs_line"] is not None
    assert features["benchmark_rs_sma"] is not None
    assert "benchmark_rs_new_high" in features


def test_weekly_resample_and_htf_features() -> None:
    frame = _synthetic_ohlcv(rows=320)

    weekly = resample_weekly_ohlcv(frame)
    features = calculate_htf_trend_features(frame)

    assert len(weekly) > 40
    assert features["close"] == weekly.iloc[-2]["close"]
    assert features["htf_sma_slow"] is not None
    assert features["htf_slow_slope_pct"] is not None
    assert features["htf_roc"] is not None
    assert "htf_close_above_mid" in features


def test_load_price_bars_frame_converts_rows_to_dataframe() -> None:
    class FakeDb:
        def scalars(self, statement):
            return self

        def all(self):
            return [
                PriceBar(
                    ticker="MSFT",
                    bar_date=date(2026, 1, 1),
                    timeframe="1 day",
                    open=Decimal("10"),
                    high=Decimal("11"),
                    low=Decimal("9"),
                    close=Decimal("10.5"),
                    volume=Decimal("1000"),
                    source="IB",
                    what_to_show="TRADES",
                )
            ]

    frame = load_price_bars_frame(FakeDb(), "MSFT", "TRADES")

    assert frame.iloc[0].to_dict() == {
        "date": date(2026, 1, 1),
        "open": 10.0,
        "high": 11.0,
        "low": 9.0,
        "close": 10.5,
        "volume": 1000.0,
    }


def _synthetic_ohlcv(rows: int) -> pd.DataFrame:
    start = date(2025, 1, 1)
    records = []
    for index in range(rows):
        base = 50 + index * 0.25
        records.append(
            {
                "date": start + timedelta(days=index),
                "open": base,
                "high": base + 1.5,
                "low": base - 1.0,
                "close": base + 0.8,
                "volume": 1_000_000 + index * 1000,
            }
        )
    return pd.DataFrame(records)


def _flat_ohlcv(rows: int) -> pd.DataFrame:
    start = date(2025, 1, 1)
    records = []
    for index in range(rows):
        records.append(
            {
                "date": start + timedelta(days=index),
                "open": 95.0,
                "high": 100.0,
                "low": 94.0,
                "close": 95.0,
                "volume": 1_000_000,
            }
        )
    return pd.DataFrame(records)
