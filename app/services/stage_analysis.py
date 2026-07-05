from dataclasses import dataclass
from typing import Any

import pandas as pd

STAGE_1 = "Stage 1"
STAGE_2 = "Stage 2"
STAGE_3 = "Stage 3"
STAGE_4 = "Stage 4"
STAGE_UNKNOWN = "Unknown"


@dataclass(frozen=True)
class StageAnalysisResult:
    stage: str
    stage_score: float
    stage_confidence: str
    stage_tags: list[str]


def add_stage_features(
    df: pd.DataFrame,
    params: dict[str, Any],
) -> pd.DataFrame:
    if not params.get("enabled", True):
        return df

    features = df.copy()
    results = [
        classify_stage(row, params)
        for _, row in features.iterrows()
    ]
    features["stage"] = [result.stage for result in results]
    features["stage_score"] = [result.stage_score for result in results]
    features["stage_confidence"] = [result.stage_confidence for result in results]
    features["stage_tags"] = [result.stage_tags for result in results]
    return features


def classify_stage(row: pd.Series | dict[str, Any], params: dict[str, Any]) -> StageAnalysisResult:
    distribution_min = float(params.get("stage3_distribution_min_count", 3))
    close = _num(row.get("close"))
    sma50 = _num(row.get("sma50"))
    sma150 = _num(row.get("sma150"))
    sma200 = _num(row.get("sma200"))
    sma50_slope = _num(row.get("sma50_slope_pct"), 0.0)
    sma200_slope = _num(row.get("sma200_slope_pct"), 0.0)
    position_52w = _num(row.get("position_52w"), 50.0)
    distribution_count = _num(row.get("distribution_count"), 0.0)

    if None in {close, sma50, sma150, sma200}:
        return StageAnalysisResult(STAGE_UNKNOWN, 0.0, "low", [])

    if close < sma200 and sma200_slope < 0 and (sma50 < sma150 or sma50 < sma200):
        return StageAnalysisResult(
            STAGE_4,
            2.0,
            "normal",
            ["stage_4_downtrend"],
        )

    if distribution_count >= distribution_min and (sma50_slope <= 0 or close < sma50):
        return StageAnalysisResult(
            STAGE_3,
            4.0,
            "normal",
            ["stage_3_distribution"],
        )

    if close > sma50 > sma150 > sma200 and sma200_slope >= 0 and position_52w >= 50:
        tags = ["stage_2_continuation"]
        if bool(params.get("allow_stage_1_to_2_transition", True)) and position_52w < 65:
            tags.append("stage_1_to_2_transition")
        return StageAnalysisResult(STAGE_2, 8.5, "normal", tags)

    near_sma200 = abs(close - sma200) / close * 100 <= 10
    sma200_flat = abs(sma200_slope) <= 2.0
    if near_sma200 and sma200_flat:
        return StageAnalysisResult(STAGE_1, 5.0, "normal", ["stage_1_base"])

    return StageAnalysisResult(STAGE_UNKNOWN, 3.0, "low", [])


def _num(value: Any, default: float | None = None) -> float | None:
    if value is None or pd.isna(value):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
