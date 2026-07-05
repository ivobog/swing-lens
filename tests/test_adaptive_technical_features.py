import pandas as pd
import pytest

from app.services.adaptive_technical_features import (
    add_adaptive_features,
    rolling_percentile,
)
from app.services.technical_indicators import calculate_technical_features


def test_rolling_percentile_uses_current_window_without_lookahead() -> None:
    values = pd.Series([1, 2, 3, 4, 5, 0], dtype="float")

    percentile = rolling_percentile(values, lookback=5)

    assert percentile.iloc[4] == 100.0
    assert percentile.iloc[5] == 20.0


def test_add_adaptive_features_calculates_percentiles_and_flags() -> None:
    frame = pd.DataFrame(
        {
            "close": [100, 100, 100, 100, 100, 100],
            "high": [101, 101, 101, 101, 101, 120],
            "low": [99, 99, 99, 99, 99, 90],
            "volume": [10, 20, 30, 40, 50, 1000],
            "atr14": [1, 1, 1, 1, 1, 1],
            "atr_pct": [1, 2, 3, 4, 5, 1],
            "ema20": [98, 98, 98, 98, 98, 90],
            "sma50": [97, 97, 97, 97, 97, 80],
        }
    )

    result = add_adaptive_features(
        frame,
        {
            "enabled": True,
            "long_lookback": 5,
            "medium_lookback": 3,
            "atr_contraction_percentile": 35,
            "atr_expansion_percentile": 80,
            "volume_climax_percentile": 90,
            "range_climax_percentile": 90,
        },
    )
    latest = result.iloc[-1]

    assert latest["atr_percentile_252"] == 20.0
    assert latest["atr_percentile_126"] == pytest.approx(33.33333333333333)
    assert latest["volume_percentile_252"] == 100.0
    assert latest["notional_volume_percentile_252"] == 100.0
    assert latest["range_percentile_252"] == 100.0
    assert latest["extension_percentile_252"] == 100.0
    assert bool(latest["atr_contraction_flag"]) is True
    assert bool(latest["atr_expansion_flag"]) is False
    assert bool(latest["climax_volume_flag"]) is True
    assert bool(latest["wide_range_flag"]) is True
    assert bool(latest["climax_range_flag"]) is True


def test_calculate_technical_features_includes_adaptive_fields() -> None:
    result = calculate_technical_features(_synthetic_ohlcv(rows=320), ticker="TEST")

    assert result.latest["atr_percentile_252"] is not None
    assert result.latest["atr_percentile_126"] is not None
    assert result.latest["volume_percentile_252"] is not None
    assert result.latest["notional_volume_percentile_252"] is not None
    assert result.latest["range_percentile_252"] is not None
    assert result.latest["extension_percentile_252"] is not None
    assert "atr_expansion_flag" in result.latest
    assert result.debug["adaptive_percentiles_enabled"] is True


def test_adaptive_percentiles_require_full_lookback() -> None:
    result = calculate_technical_features(_synthetic_ohlcv(rows=40), ticker="SHORT")

    assert result.latest["atr_percentile_252"] is None
    assert result.latest["volume_percentile_252"] is None
    assert result.latest["range_percentile_252"] is None


def _synthetic_ohlcv(rows: int) -> pd.DataFrame:
    records = []
    for index in range(rows):
        base = 50 + index * 0.25
        records.append(
            {
                "date": pd.Timestamp("2025-01-01") + pd.Timedelta(days=index),
                "open": base,
                "high": base + 1.5,
                "low": base - 1.0,
                "close": base + 0.8,
                "volume": 1_000_000 + index * 1000,
            }
        )
    return pd.DataFrame(records)
