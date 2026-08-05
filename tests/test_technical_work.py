from dataclasses import asdict
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from app.services import technical_score_service
from app.services.ib_fetch_executor import TickerReadyEvent
from app.services.technical_indicators import load_pine_defaults
from app.services.technical_score_service import TechnicalScoringOverlapCoordinator
from app.services.technical_scoring_config import load_technical_scoring_v4_config
from app.services.technical_work import (
    build_technical_work_item,
    execute_technical_work_item,
)
from app.settings import Settings


def test_technical_work_item_is_database_free_and_produces_a_score() -> None:
    frame = _synthetic_frame()
    item = build_technical_work_item(
        ticker="test",
        price=frame,
        trades=frame,
        benchmark_price=frame,
        sector_price=None,
        technical_config=load_technical_scoring_v4_config(),
        pine_config=load_pine_defaults(),
        relative_config={},
        market_features={},
    )

    result = execute_technical_work_item(item)

    assert item.ticker == "TEST"
    assert item.price_records
    assert result.error is None
    assert result.score is not None
    assert result.score.ticker == "TEST"
    assert result.feature_result["ticker"] == "TEST"


def test_technical_work_item_matches_legacy_ticker_calculation(monkeypatch) -> None:
    frame = _synthetic_frame()
    pine_config = load_pine_defaults()
    technical_config = load_technical_scoring_v4_config()
    item = build_technical_work_item(
        ticker="test",
        price=frame,
        trades=frame,
        benchmark_price=frame,
        sector_price=None,
        technical_config=technical_config,
        pine_config=pine_config,
        relative_config=technical_config.get("relative_leadership", {}),
        market_features={},
    )

    class FakeDb:
        pass

    monkeypatch.setattr(
        technical_score_service,
        "load_preferred_ohlcv_frames",
        lambda _db, _ticker: (frame, frame),
    )

    pure_result = execute_technical_work_item(item)
    legacy_score = technical_score_service._score_ticker(
        db=FakeDb(),
        ticker="TEST",
        benchmark_price=frame,
        sector_price=None,
        market_features={},
        qqq_market_features={},
        v4_params=technical_config,
    )

    assert pure_result.score is not None
    assert asdict(pure_result.score) == asdict(legacy_score)


def test_process_pool_mode_returns_scores_in_input_order(monkeypatch) -> None:
    frame = _synthetic_frame()

    class FakeDb:
        def execute(self, _statement):
            pass

        def add_all(self, rows):
            self.rows = rows

        def flush(self):
            pass

    monkeypatch.setattr(
        technical_score_service,
        "load_preferred_ohlcv_frames",
        lambda _db, _ticker: (frame, frame),
    )
    monkeypatch.setattr(
        technical_score_service,
        "get_settings",
        lambda: Settings(
            _env_file=None,
            technical_process_pool_enabled=True,
            technical_worker_processes=1,
        ),
    )

    db = FakeDb()
    rows = technical_score_service.score_run_technicals(
        db,
        run_id=7,
        tickers=["bbb", "aaa"],
    )

    assert [row.ticker for row in rows] == ["BBB", "AAA"]
    assert len(db.rows) == 2


def test_artifact_write_mode_persists_local_work_result(monkeypatch) -> None:
    frame = _synthetic_frame()
    writes = []

    class FakeDb:
        def execute(self, _statement):
            pass

        def add_all(self, rows):
            self.rows = rows

        def flush(self):
            pass

    monkeypatch.setattr(
        technical_score_service,
        "load_preferred_ohlcv_frames",
        lambda _db, _ticker: (frame, frame),
    )
    monkeypatch.setattr(
        technical_score_service,
        "load_series_versions",
        lambda _db, _ticker: {"ADJUSTED_LAST": 12, "TRADES": 15},
    )
    monkeypatch.setattr(
        technical_score_service,
        "upsert_local_artifact",
        lambda _db, key, **kwargs: writes.append((key, kwargs)),
    )
    monkeypatch.setattr(
        technical_score_service,
        "get_settings",
        lambda: Settings(
            _env_file=None,
            technical_artifact_cache_write_enabled=True,
        ),
    )

    rows = technical_score_service.score_run_technicals(
        FakeDb(),
        run_id=7,
        tickers=["aaa"],
    )

    assert rows[0].ticker == "AAA"
    assert len(writes) == 1
    assert writes[0][0].input_versions["adjusted_series_version"] == 12
    assert "feature_result" in writes[0][1]["artifact_json"]


def test_overlap_coordinator_submits_ready_ticker_and_finalizes(monkeypatch) -> None:
    frame = _synthetic_frame()

    class FakeDb:
        def execute(self, _statement):
            pass

        def add_all(self, rows):
            self.rows = rows

        def flush(self):
            pass

    monkeypatch.setattr(
        technical_score_service,
        "load_preferred_ohlcv_frames",
        lambda _db, _ticker: (frame, frame),
    )

    coordinator = TechnicalScoringOverlapCoordinator(
        FakeDb(),
        run_id=7,
        tickers=["AAA"],
        settings=Settings(_env_file=None, technical_worker_processes=1),
    )
    coordinator.on_ticker_ready(
        TickerReadyEvent(
            ticker="AAA",
            statuses=("SUCCESS",),
            failed=False,
            completed_at=datetime.now(UTC),
        )
    )
    rows = coordinator.finalize()

    assert [row.ticker for row in rows] == ["AAA"]
    assert rows[0].technical_confidence != "error"


def _synthetic_frame() -> pd.DataFrame:
    dates = pd.date_range("2020-01-01", periods=320, freq="D")
    close = np.linspace(100.0, 180.0, len(dates))
    return pd.DataFrame(
        {
            "date": dates,
            "open": close - 1.0,
            "high": close + 2.0,
            "low": close - 2.0,
            "close": close,
            "volume": np.linspace(100_000.0, 120_000.0, len(dates)),
        }
    )
