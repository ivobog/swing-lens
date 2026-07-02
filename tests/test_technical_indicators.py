from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import pytest

from app.models.tables import PriceBar
from app.services.price_bar_repository import load_price_bars_frame
from app.services.technical_indicators import (
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
    assert features["htf_sma_slow"] is not None
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
