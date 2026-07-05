import pandas as pd

from app.services.climax_risk import (
    add_climax_risk_features,
    calculate_climax_risk,
)
from app.services.technical_indicators import calculate_technical_features


def test_calculate_climax_risk_flags_momentum_crash_setup() -> None:
    result = calculate_climax_risk(
        {
            "rsi14": 84.0,
            "extension_above_ema20_pct": 18.0,
            "extension_above_sma50_pct": 28.0,
            "extension_above_ema20_atr": 4.2,
            "extension_above_sma50_atr": 7.1,
            "extension_percentile_252": 96.0,
            "gap_up_pct": 4.0,
            "volume_climax_flag": True,
            "range_climax_flag": True,
            "vertical_move_flag": True,
            "upper_wick_rejection": True,
            "strong_close_ratio": 0.25,
        },
        _params(),
    )

    assert result.climax_risk_score == 10.0
    assert result.vertical_move_flag is True
    assert result.volume_climax_flag is True
    assert result.range_climax_flag is True
    assert result.upper_wick_rejection is True
    assert result.momentum_crash_risk is True
    assert "vertical_move" in result.reasons
    assert "weak_close_after_vertical_move" in result.reasons


def test_add_climax_risk_features_calculates_moves_and_flags() -> None:
    frame = pd.DataFrame(
        {
            "close": [100, 101, 102, 103, 104, 105, 110, 116, 123, 130],
            "atr14": [5.0] * 10,
            "rsi14": [55, 56, 58, 60, 62, 65, 72, 78, 82, 85],
            "extension_above_ema20_pct": [2, 2, 3, 3, 4, 5, 8, 12, 18, 26],
            "extension_above_sma50_pct": [4, 4, 5, 5, 6, 7, 12, 18, 24, 32],
            "extension_above_ema20_atr": [0.5, 0.5, 0.8, 1.0, 1.2, 1.8, 2.5, 3.5, 5.0, 7.0],
            "extension_above_sma50_atr": [0.8, 0.9, 1.1, 1.3, 1.5, 2.0, 3.0, 4.2, 5.8, 8.0],
            "extension_percentile_252": [20, 20, 25, 25, 30, 40, 65, 80, 92, 99],
            "climax_volume_flag": [False] * 9 + [True],
            "climax_range_flag": [False] * 9 + [True],
            "gap_up_pct": [0.2] * 9 + [4.5],
            "gap_exhaustion": [False] * 10,
            "upper_wick_pct": [5] * 9 + [42],
            "strong_close_ratio": [0.7] * 9 + [0.3],
        }
    )

    result = add_climax_risk_features(frame, _params())
    latest = result.iloc[-1]

    assert latest["move_3d_pct"] > 10.0
    assert latest["move_5d_pct"] > 20.0
    assert latest["move_10d_pct"] != latest["move_10d_pct"]
    assert latest["move_3d_atr"] >= 3.0
    assert bool(latest["vertical_move_flag"]) is True
    assert bool(latest["volume_climax_flag"]) is True
    assert bool(latest["range_climax_flag"]) is True
    assert bool(latest["upper_wick_rejection"]) is True
    assert latest["climax_risk_score"] >= 7.0
    assert bool(latest["momentum_crash_risk"]) is True
    assert "volume_climax" in latest["climax_risk_reasons"]


def test_add_climax_risk_features_keeps_quiet_setup_low_risk() -> None:
    frame = pd.DataFrame(
        {
            "close": [100, 100.5, 101, 101.2, 101.4, 101.7, 102.0],
            "atr14": [4.0] * 7,
            "rsi14": [55.0] * 7,
            "extension_above_ema20_pct": [2.0] * 7,
            "extension_above_sma50_pct": [4.0] * 7,
            "extension_above_ema20_atr": [0.5] * 7,
            "extension_above_sma50_atr": [1.0] * 7,
            "extension_percentile_252": [50.0] * 7,
            "climax_volume_flag": [False] * 7,
            "climax_range_flag": [False] * 7,
            "gap_up_pct": [0.0] * 7,
            "upper_wick_pct": [10.0] * 7,
            "strong_close_ratio": [0.7] * 7,
        }
    )

    latest = add_climax_risk_features(frame, _params()).iloc[-1]

    assert latest["climax_risk_score"] == 0.0
    assert bool(latest["momentum_crash_risk"]) is False
    assert latest["climax_risk_reasons"] == []


def test_calculate_technical_features_includes_climax_risk_fields() -> None:
    result = calculate_technical_features(_synthetic_ohlcv(rows=320), ticker="TEST")

    assert "move_3d_pct" in result.latest
    assert "move_5d_atr" in result.latest
    assert "vertical_move_flag" in result.latest
    assert "volume_climax_flag" in result.latest
    assert "range_climax_flag" in result.latest
    assert "upper_wick_rejection" in result.latest
    assert "climax_risk_score" in result.latest
    assert "momentum_crash_risk" in result.latest
    assert "climax_risk_reasons" in result.latest
    assert result.debug["climax_risk_enabled"] is True


def _params() -> dict[str, float | bool]:
    return {
        "enabled": True,
        "rsi_warning": 75,
        "rsi_danger": 80,
        "vertical_move_3d_atr": 3.0,
        "vertical_move_5d_atr": 4.5,
        "climax_risk_threshold": 7.0,
    }


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
