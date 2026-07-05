import pandas as pd

from app.services.technical_indicators import calculate_technical_features
from app.services.volatility_contraction import add_contraction_features


def test_add_contraction_features_detects_squeeze_release() -> None:
    frame = _base_feature_frame(rows=30)
    frame["close"] = [100.0] * 29 + [120.0]
    frame["high"] = frame["close"] + 1
    frame["low"] = frame["close"] - 1
    frame["atr14"] = 1.0
    frame["atr_contraction_flag"] = False
    frame["narrow_range_flag"] = False

    result = add_contraction_features(frame, _params())

    assert bool(result.iloc[-2]["squeeze_on"]) is True
    assert bool(result.iloc[-1]["squeeze_on"]) is False
    assert bool(result.iloc[-1]["squeeze_release"]) is True
    assert result.iloc[-1]["bb_width_pct"] is not None
    assert result.iloc[-1]["kc_upper"] is not None


def test_add_contraction_features_scores_vcp_setup() -> None:
    frame = _base_feature_frame(rows=30)
    frame["close"] = [100.0 + index * 0.1 for index in range(30)]
    frame["atr14"] = [3.0 - index * 0.05 for index in range(30)]
    frame["candle_range"] = [2.0] * 25 + [1.0] * 5
    frame["atr_contraction_flag"] = True
    frame["narrow_range_flag"] = True
    frame["volume_dry_up"] = True
    frame["red_volume_declining"] = True
    frame["higher_low"] = True
    frame["had_pullback"] = True
    frame["not_too_deep"] = True
    frame["held_near_support"] = True
    frame["distribution_count"] = 0.0
    frame["avg_volume"] = 1_000_000.0
    frame["short_volume_avg"] = 500_000.0

    result = add_contraction_features(frame, _params())
    latest = result.iloc[-1]

    assert latest["tight_close_count_5"] == 5
    assert latest["tight_close_score"] >= 7.0
    assert bool(latest["atr_contraction"]) is True
    assert bool(latest["range_contraction"]) is True
    assert latest["volume_dry_up_quality"] == 5.0
    assert latest["vcp_score"] >= 7.0
    assert bool(latest["vcp_detected"]) is True


def test_calculate_technical_features_includes_contraction_fields() -> None:
    result = calculate_technical_features(_synthetic_ohlcv(rows=320), ticker="TEST")

    assert result.latest["bb_mid"] is not None
    assert result.latest["bb_upper"] is not None
    assert result.latest["bb_lower"] is not None
    assert result.latest["bb_width_pct"] is not None
    assert result.latest["bb_width_percentile_252"] is not None
    assert result.latest["kc_mid"] is not None
    assert "squeeze_on" in result.latest
    assert "squeeze_release" in result.latest
    assert "tight_close_count_5" in result.latest
    assert "vcp_score" in result.latest
    assert result.debug["volatility_contraction_enabled"] is True


def _params() -> dict:
    return {
        "enabled": True,
        "bb_length": 5,
        "bb_stddev": 2.0,
        "bb_width_percentile_lookback": 5,
        "kc_length": 5,
        "kc_atr_multiple": 1.5,
        "squeeze_enabled": True,
        "vcp_min_score": 7.0,
        "tight_close_lookback_short": 5,
        "tight_close_lookback_long": 10,
        "tight_close_max_pct": 2.0,
    }


def _base_feature_frame(rows: int) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "close": [100.0] * rows,
            "high": [101.0] * rows,
            "low": [99.0] * rows,
            "volume": [1_000_000.0] * rows,
            "atr14": [1.0] * rows,
            "candle_range": [2.0] * rows,
            "avg_volume": [1_000_000.0] * rows,
            "short_volume_avg": [1_000_000.0] * rows,
            "had_pullback": [False] * rows,
            "not_too_deep": [False] * rows,
            "held_near_support": [False] * rows,
            "volume_dry_up": [False] * rows,
            "red_volume_declining": [False] * rows,
            "higher_low": [False] * rows,
            "distribution_count": [0.0] * rows,
            "atr_contraction_flag": [False] * rows,
            "narrow_range_flag": [False] * rows,
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
