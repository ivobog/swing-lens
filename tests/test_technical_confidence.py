import pandas as pd

from app.services import technical_score_service
from app.services.pine_replica_engine import score_from_feature_result
from app.services.technical_confidence import build_data_readiness
from app.services.technical_indicators import (
    TechnicalFeatureResult,
    calculate_technical_features,
    load_pine_defaults,
)


def test_data_readiness_is_low_when_required_context_is_missing() -> None:
    features = calculate_technical_features(_synthetic_ohlcv(), ticker="TEST")

    readiness = build_data_readiness(
        feature_result=features,
        htf_features={},
        relative_strength_features={},
        market_features={},
        params=load_pine_defaults(),
    )

    assert readiness.confidence == "low"
    assert readiness.has_price_data is True
    assert readiness.has_sufficient_history is True
    assert readiness.missing_reasons == [
        "missing_benchmark_data",
        "missing_market_data",
        "missing_htf_data",
    ]
    assert readiness.missing_flags()["missing_benchmark_data"] is True
    assert readiness.missing_flags()["missing_market_data"] is True
    assert readiness.missing_flags()["missing_htf_data"] is True


def test_data_readiness_is_high_when_required_context_is_present() -> None:
    features = calculate_technical_features(_synthetic_ohlcv(), ticker="TEST")

    readiness = build_data_readiness(
        feature_result=features,
        htf_features={"htf_sma_slow": 90.0},
        relative_strength_features={"benchmark_rs_line": 1.2},
        market_features={"close": 100.0},
        params=load_pine_defaults(),
    )

    assert readiness.confidence == "high"
    assert readiness.data_quality_score == 10.0
    assert readiness.missing_reasons == []


def test_score_result_carries_missing_context_without_failing_base_score() -> None:
    features = calculate_technical_features(_synthetic_ohlcv(), ticker="TEST")

    score = score_from_feature_result(features)

    assert score.dual_score is not None
    assert score.technical_confidence == "low"
    assert score.missing_data["missing_benchmark_data"] is True
    assert score.missing_data["missing_market_data"] is True
    assert score.missing_data["missing_htf_data"] is True
    assert score.debug["data_readiness"]["confidence"] == "low"


def test_score_run_technicals_marks_missing_benchmark_low_confidence(monkeypatch) -> None:
    class FakeDb:
        def __init__(self) -> None:
            self.executed = False
            self.flushed = False

        def execute(self, statement):
            self.executed = True

        def add_all(self, rows):
            self.rows = rows

        def flush(self):
            self.flushed = True

    empty_frame = pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    stock_frame = _synthetic_ohlcv()
    monkeypatch.setattr(
        technical_score_service,
        "_load_price_frame",
        lambda *args: empty_frame,
    )
    monkeypatch.setattr(
        technical_score_service,
        "load_preferred_ohlcv_frames",
        lambda *args: (stock_frame, stock_frame),
    )

    rows = technical_score_service.score_run_technicals(FakeDb(), run_id=7, tickers=["msft"])

    assert rows[0].technical_confidence == "low"
    assert rows[0].missing_data_json["missing_benchmark_data"] is True
    assert rows[0].missing_data_json["missing_market_data"] is True


def test_missing_price_data_is_error_confidence() -> None:
    empty = TechnicalFeatureResult(
        ticker="EMPTY",
        insufficient_data=True,
        missing_data={"empty": True, "insufficient_history": True},
        latest={},
        debug={},
    )

    readiness = build_data_readiness(
        feature_result=empty,
        htf_features={},
        relative_strength_features={},
        market_features={},
        params=load_pine_defaults(),
    )

    assert readiness.confidence == "error"
    assert readiness.data_quality_score == 0.0
    assert "missing_price_data" in readiness.missing_reasons


def _synthetic_ohlcv(rows: int = 320) -> pd.DataFrame:
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
                "volume": 1_000_000 + index * 1_000,
            }
        )
    return pd.DataFrame(records)
