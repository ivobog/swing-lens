import pandas as pd

from app.services.stage_analysis import (
    STAGE_1,
    STAGE_2,
    STAGE_3,
    STAGE_4,
    STAGE_UNKNOWN,
    add_stage_features,
    classify_stage,
)
from app.services.technical_indicators import calculate_technical_features


def test_classify_stage_2_uptrend() -> None:
    result = classify_stage(
        {
            "close": 120.0,
            "sma50": 110.0,
            "sma150": 100.0,
            "sma200": 90.0,
            "sma50_slope_pct": 2.0,
            "sma200_slope_pct": 1.0,
            "position_52w": 80.0,
            "distribution_count": 0.0,
        },
        _params(),
    )

    assert result.stage == STAGE_2
    assert result.stage_score == 8.5
    assert "stage_2_continuation" in result.stage_tags


def test_classify_stage_4_downtrend() -> None:
    result = classify_stage(
        {
            "close": 80.0,
            "sma50": 85.0,
            "sma150": 90.0,
            "sma200": 100.0,
            "sma50_slope_pct": -2.0,
            "sma200_slope_pct": -3.0,
            "position_52w": 20.0,
            "distribution_count": 1.0,
        },
        _params(),
    )

    assert result.stage == STAGE_4
    assert result.stage_tags == ["stage_4_downtrend"]


def test_classify_stage_3_distribution() -> None:
    result = classify_stage(
        {
            "close": 105.0,
            "sma50": 110.0,
            "sma150": 100.0,
            "sma200": 95.0,
            "sma50_slope_pct": -1.0,
            "sma200_slope_pct": 0.5,
            "position_52w": 70.0,
            "distribution_count": 4.0,
        },
        _params(),
    )

    assert result.stage == STAGE_3
    assert result.stage_tags == ["stage_3_distribution"]


def test_classify_stage_1_base_and_unknown() -> None:
    stage_1 = classify_stage(
        {
            "close": 101.0,
            "sma50": 100.0,
            "sma150": 100.0,
            "sma200": 100.0,
            "sma50_slope_pct": 0.0,
            "sma200_slope_pct": 0.5,
            "position_52w": 45.0,
            "distribution_count": 0.0,
        },
        _params(),
    )
    unknown = classify_stage({"close": 100.0, "sma50": None}, _params())

    assert stage_1.stage == STAGE_1
    assert stage_1.stage_tags == ["stage_1_base"]
    assert unknown.stage == STAGE_UNKNOWN
    assert unknown.stage_confidence == "low"


def test_add_stage_features_adds_stage_columns() -> None:
    frame = pd.DataFrame(
        {
            "close": [80.0, 120.0],
            "sma50": [85.0, 110.0],
            "sma150": [90.0, 100.0],
            "sma200": [100.0, 90.0],
            "sma50_slope_pct": [-2.0, 2.0],
            "sma200_slope_pct": [-3.0, 1.0],
            "position_52w": [20.0, 80.0],
            "distribution_count": [1.0, 0.0],
        }
    )

    result = add_stage_features(frame, _params())

    assert result.iloc[0]["stage"] == STAGE_4
    assert result.iloc[1]["stage"] == STAGE_2
    assert result.iloc[1]["stage_tags"] == ["stage_2_continuation"]


def test_calculate_technical_features_includes_stage_fields() -> None:
    result = calculate_technical_features(_synthetic_ohlcv(rows=320), ticker="TEST")

    assert result.latest["stage"] in {STAGE_1, STAGE_2, STAGE_3, STAGE_4, STAGE_UNKNOWN}
    assert "stage_score" in result.latest
    assert "stage_confidence" in result.latest
    assert isinstance(result.latest["stage_tags"], list)
    assert result.debug["stage_analysis_enabled"] is True


def _params() -> dict:
    return {
        "enabled": True,
        "allow_prime_only_in_stage_2": True,
        "allow_stage_1_to_2_transition": True,
        "stage3_distribution_min_count": 3,
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
