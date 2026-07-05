import pandas as pd

from app.services.box_breakout import add_box_features
from app.services.technical_indicators import calculate_technical_features


def test_add_box_features_calculates_donchian_without_lookahead() -> None:
    frame = _box_frame(rows=30)
    frame.loc[frame.index[-1], ["close", "high", "low"]] = [106.0, 107.0, 104.0]

    result = add_box_features(frame, _params())
    latest = result.iloc[-1]

    assert latest["donchian_20_high"] == 100.0
    assert latest["donchian_20_low"] == 90.0
    assert bool(latest["donchian_20_breakout"]) is True
    assert latest["box_high"] == 100.0
    assert latest["box_low"] == 90.0
    assert latest["box_width_pct"] == 11.11111111111111


def test_add_box_features_detects_tight_box_breakout() -> None:
    frame = _box_frame(rows=30)
    frame.loc[frame.index[-1], ["close", "high", "low"]] = [106.0, 107.0, 104.0]

    result = add_box_features(frame, _params())
    latest = result.iloc[-1]

    assert latest["box_tightness_score"] > 0
    assert bool(latest["box_breakout"]) is True
    assert latest["breakout_quality_score"] >= 7.0


def test_add_box_features_marks_failed_box_breakout() -> None:
    frame = _box_frame(rows=32)
    frame.loc[frame.index[-2], ["close", "high", "low"]] = [106.0, 107.0, 104.0]
    frame.loc[frame.index[-1], ["close", "high", "low"]] = [98.0, 101.0, 97.0]

    result = add_box_features(frame, _params())
    latest = result.iloc[-1]

    assert bool(result.iloc[-2]["box_breakout"]) is True
    assert bool(latest["box_failure"]) is True


def test_calculate_technical_features_includes_box_fields() -> None:
    result = calculate_technical_features(_synthetic_ohlcv(rows=320), ticker="TEST")

    assert "donchian_20_high" in result.latest
    assert "donchian_55_high" in result.latest
    assert "box_high" in result.latest
    assert "box_tightness_score" in result.latest
    assert "breakout_quality_score" in result.latest
    assert result.debug["donchian_darvas_enabled"] is True


def _params() -> dict:
    return {
        "enabled": True,
        "donchian_short_len": 20,
        "donchian_long_len": 25,
        "box_lookback": 20,
        "max_box_width_pct": 15.0,
        "min_box_age": 7,
        "breakout_volume_percentile_min": 70,
        "breakout_close_ratio_min": 0.65,
        "box_failure_bars": 8,
    }


def _box_frame(rows: int) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "close": [95.0] * rows,
            "high": [100.0] * rows,
            "low": [90.0] * rows,
            "breakout_volume_percentile": [80.0] * rows,
            "strong_close_ratio": [0.75] * rows,
            "gap_exhaustion": [False] * rows,
        }
    )


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
