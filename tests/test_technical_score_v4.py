import pandas as pd

from app.services.pine_replica_engine import score_from_feature_result
from app.services.technical_indicators import calculate_technical_features
from app.services.technical_score_service import build_technical_score
from app.services.technical_score_v4 import technical_score_v4_from_base_score


def test_technical_score_v4_wraps_base_score_without_changing_v3_result() -> None:
    base_score = score_from_feature_result(
        calculate_technical_features(_synthetic_uptrend(), ticker="TEST")
    )

    v4_score = technical_score_v4_from_base_score(base_score)

    assert v4_score.base_score is base_score
    assert v4_score.ticker == base_score.ticker
    assert v4_score.engine_version == "4.0.0"
    assert v4_score.final_v4_score == base_score.dual_score
    assert v4_score.final_v4_classification == base_score.classification
    assert v4_score.final_v4_action == base_score.action_bias
    assert v4_score.adaptive == base_score.debug["explainability"]["adaptive"]
    assert v4_score.contraction == base_score.debug["explainability"]["contraction"]
    assert v4_score.box == base_score.debug["explainability"]["box"]
    assert v4_score.stage == base_score.debug["explainability"]["stage"]
    assert v4_score.regime == base_score.debug["explainability"]["regime"]
    assert v4_score.climax == base_score.debug["explainability"]["climax"]
    assert v4_score.feature_flags == base_score.debug["explainability"]["feature_flags"]
    assert v4_score.warning_flags == base_score.debug["explainability"]["warning_flags"]
    assert v4_score.sub_tags == base_score.debug["explainability"]["sub_tags"]


def test_build_technical_score_accepts_v4_wrapper() -> None:
    base_score = score_from_feature_result(
        calculate_technical_features(_synthetic_uptrend(), ticker="TEST")
    )
    v4_score = technical_score_v4_from_base_score(base_score)

    model = build_technical_score(run_id=3, score=v4_score)

    assert model.dual_score is not None
    assert float(model.dual_score) == v4_score.final_v4_score
    assert model.classification == v4_score.final_v4_classification
    assert model.action_bias == v4_score.final_v4_action
    assert model.v4_debug_json == v4_score.debug["explainability"]


def _synthetic_uptrend() -> pd.DataFrame:
    rows = []
    for index in range(320):
        base = 50 + index * 0.35
        rows.append(
            {
                "date": pd.Timestamp("2025-01-01") + pd.Timedelta(days=index),
                "open": base,
                "high": base + 2,
                "low": base - 1,
                "close": base + 1.2,
                "volume": 1_000_000 + index * 2_000,
            }
        )
    return pd.DataFrame(rows)
