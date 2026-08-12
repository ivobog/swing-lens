from dataclasses import asdict
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from app.services import technical_score_service
from app.services.ib_fetch_executor import TickerReadyEvent
from app.services.operational_metrics import operational_metrics
from app.services.technical_artifact_cache import build_local_artifact_key
from app.services.technical_indicators import load_pine_defaults
from app.services.technical_score_service import TechnicalScoringOverlapCoordinator
from app.services.technical_scoring_config import load_technical_scoring_v4_config
from app.services.technical_work import (
    build_technical_work_item,
    execute_technical_work_item,
)
from app.settings import Settings, TechnicalArtifactCacheMode


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


def test_shadow_artifact_matches_fresh_with_current_benchmark_and_sector_inputs() -> None:
    frame = _synthetic_frame()
    pine_config = load_pine_defaults()
    technical_config = load_technical_scoring_v4_config()
    initial = execute_technical_work_item(
        build_technical_work_item(
            ticker="test",
            price=frame,
            trades=frame,
            benchmark_price=frame,
            sector_price=frame,
            technical_config=technical_config,
            pine_config=pine_config,
            relative_config=technical_config.get("relative_leadership", {}),
            market_features={},
        )
    )
    assert initial.score is not None
    artifact = {
        "feature_result": initial.feature_result,
        "htf_features": initial.htf_features,
    }
    changed_dependency = _declining_frame()

    shadow = execute_technical_work_item(
        build_technical_work_item(
            ticker="test",
            price=frame,
            trades=frame,
            benchmark_price=changed_dependency,
            sector_price=changed_dependency,
            technical_config=technical_config,
            pine_config=pine_config,
            relative_config=technical_config.get("relative_leadership", {}),
            market_features={},
            shadow_local_artifact=artifact,
        )
    )

    assert shadow.score is not None
    assert shadow.shadow_score is not None
    assert shadow.shadow_error is None
    assert asdict(shadow.score) == asdict(shadow.shadow_score)
    assert shadow.relative_strength_features != initial.relative_strength_features


def test_shadow_artifact_decode_failure_never_replaces_fresh_result() -> None:
    frame = _synthetic_frame()
    result = execute_technical_work_item(
        build_technical_work_item(
            ticker="test",
            price=frame,
            trades=frame,
            benchmark_price=frame,
            sector_price=None,
            technical_config=load_technical_scoring_v4_config(),
            pine_config=load_pine_defaults(),
            relative_config={},
            market_features={},
            shadow_local_artifact={"feature_result": {}, "htf_features": {}},
        )
    )

    assert result.score is not None
    assert result.error is None
    assert result.shadow_score is None
    assert result.shadow_error


def test_process_pool_shadow_mode_returns_fresh_score_and_validation_candidate(
    monkeypatch,
) -> None:
    frame = _synthetic_frame()
    pine_config = load_pine_defaults()
    technical_config = load_technical_scoring_v4_config()
    fresh = execute_technical_work_item(
        build_technical_work_item(
            ticker="AAA",
            price=frame,
            trades=frame,
            benchmark_price=frame,
            sector_price=None,
            technical_config=technical_config,
            pine_config=pine_config,
            relative_config=technical_config.get("relative_leadership", {}),
            market_features={},
        )
    )
    key = build_local_artifact_key(
        ticker="AAA",
        adjusted_series_version=1,
        trades_series_version=1,
        indicator_config_hash="indicator",
        scoring_config_hash="scoring",
        technical_engine_version="3.2.0",
    )
    validations = []
    monkeypatch.setattr(
        technical_score_service,
        "load_preferred_ohlcv_frames",
        lambda _db, _ticker: (frame, frame),
    )
    monkeypatch.setattr(
        technical_score_service,
        "_artifact_cache_context",
        lambda **_kwargs: (
            key,
            {
                "feature_result": fresh.feature_result,
                "htf_features": fresh.htf_features,
            },
        ),
    )
    monkeypatch.setattr(
        technical_score_service,
        "_persist_local_artifact",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        technical_score_service,
        "_record_shadow_validation",
        lambda _db, result, *_args, **_kwargs: validations.append(result),
    )
    settings = Settings(
        _env_file=None,
        technical_process_pool_enabled=True,
        technical_worker_processes=1,
        technical_series_version_maintenance_enabled=True,
        technical_artifact_cache_mode=TechnicalArtifactCacheMode.SHADOW_VALIDATE,
    )

    rows = technical_score_service._score_tickers_process_pool(
        db=object(),
        symbols=["AAA"],
        benchmark_price=frame,
        sector_price=None,
        market_features={},
        qqq_market_features={},
        pine_params=pine_config,
        v4_params=technical_config,
        settings=settings,
        run_id=7,
        indicator_config_hash="indicator",
        scoring_config_hash="scoring",
    )

    assert rows[0].ticker == "AAA"
    assert len(validations) == 1
    assert validations[0].score is not None
    assert validations[0].shadow_score is not None
    assert asdict(validations[0].score) == asdict(validations[0].shadow_score)


def test_process_pool_mode_returns_scores_in_input_order(monkeypatch) -> None:
    operational_metrics.reset()
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
    assert operational_metrics.total(
        "swinglens_technical_input_load_ms_total", run_id=7
    ) > 0
    assert operational_metrics.total(
        "swinglens_technical_worker_span_ms_total", run_id=7
    ) > 0
    assert operational_metrics.total(
        "swinglens_technical_finalize_ms_total", run_id=7
    ) > 0


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
            technical_series_version_maintenance_enabled=True,
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


def test_overlap_waits_for_current_spy_and_qqq_before_submitting(monkeypatch) -> None:
    frames = {
        "AAA": _synthetic_frame(),
        "SPY": _synthetic_frame(),
        "QQQ": _synthetic_frame().iloc[:-1].copy(),
    }
    _install_immediate_overlap_executor(monkeypatch)
    monkeypatch.setattr(
        technical_score_service,
        "load_preferred_ohlcv_frames",
        lambda _db, ticker: (frames[ticker.upper()], frames[ticker.upper()]),
    )
    monkeypatch.setattr(
        technical_score_service,
        "_market_features",
        lambda frame, ticker: _market_features_for_test(
            ticker,
            qqq_distribution=4 if len(frame) < 320 else 3,
        ),
    )

    coordinator = TechnicalScoringOverlapCoordinator(
        _FakeTechnicalDb(),
        run_id=7,
        tickers=["AAA"],
        settings=Settings(_env_file=None, technical_worker_processes=1),
        required_market_tickers=["SPY", "QQQ"],
        wait_for_market_events=True,
    )

    coordinator.on_ticker_ready(_ready_event("AAA"))
    assert coordinator._futures == {}
    coordinator.on_ticker_ready(_ready_event("SPY"))
    assert coordinator._futures == {}

    frames["QQQ"] = _synthetic_frame()
    coordinator.on_ticker_ready(_ready_event("QQQ"))
    assert list(coordinator._futures.values()) == ["AAA"]

    rows = coordinator.finalize()

    assert rows[0].market_regime == "Bull trend"
    assert "market_risk_off" not in (rows[0].warning_flags_json or [])


def test_overlap_rescores_result_when_final_market_signature_changes(monkeypatch) -> None:
    frames = {
        "AAA": _synthetic_frame(),
        "SPY": _synthetic_frame(),
        "QQQ": _synthetic_frame().iloc[:-1].copy(),
    }
    execution_count = {"value": 0}
    real_execute = technical_score_service.execute_technical_work_item
    _install_immediate_overlap_executor(monkeypatch)
    monkeypatch.setattr(
        technical_score_service,
        "load_preferred_ohlcv_frames",
        lambda _db, ticker: (frames[ticker.upper()], frames[ticker.upper()]),
    )
    monkeypatch.setattr(
        technical_score_service,
        "_market_features",
        lambda frame, ticker: _market_features_for_test(
            ticker,
            qqq_distribution=4 if len(frame) < 320 else 3,
        ),
    )

    def execute_and_count(item):
        execution_count["value"] += 1
        return real_execute(item)

    monkeypatch.setattr(
        technical_score_service,
        "execute_technical_work_item",
        execute_and_count,
    )

    coordinator = TechnicalScoringOverlapCoordinator(
        _FakeTechnicalDb(),
        run_id=7,
        tickers=["AAA"],
        settings=Settings(_env_file=None, technical_worker_processes=1),
    )
    coordinator.on_ticker_ready(_ready_event("AAA"))

    frames["QQQ"] = _synthetic_frame()
    rows = coordinator.finalize()

    assert execution_count["value"] == 2
    assert rows[0].market_regime == "Bull trend"
    assert "market_risk_off" not in (rows[0].warning_flags_json or [])


def test_overlap_dependency_signature_includes_sector_benchmark() -> None:
    frame = _synthetic_frame()

    initial = technical_score_service._market_frames_signature(
        frame,
        frame,
        sector_ticker="XLK",
        sector_price=frame,
    )
    changed = technical_score_service._market_frames_signature(
        frame,
        frame,
        sector_ticker="XLK",
        sector_price=frame.iloc[:-1].copy(),
    )

    assert changed != initial


def test_overlap_submission_is_bounded_without_blocking_fetch_callback(monkeypatch) -> None:
    frame = _synthetic_frame()

    class PendingFuture:
        def cancel(self):
            return True

    class PendingExecutor:
        def __init__(self, max_workers):
            self.submitted = []

        def submit(self, _fn, item):
            self.submitted.append(item.ticker)
            return PendingFuture()

        def shutdown(self, wait=True, cancel_futures=False):
            pass

    executor = PendingExecutor(1)
    monkeypatch.setattr(
        technical_score_service,
        "ProcessPoolExecutor",
        lambda max_workers: executor,
    )
    monkeypatch.setattr(
        technical_score_service,
        "wait",
        lambda futures, timeout=None, return_when=None: (set(), set(futures)),
    )
    monkeypatch.setattr(
        technical_score_service,
        "load_preferred_ohlcv_frames",
        lambda _db, _ticker: (frame, frame),
    )

    coordinator = TechnicalScoringOverlapCoordinator(
        _FakeTechnicalDb(),
        run_id=7,
        tickers=["AAA", "BBB", "CCC"],
        settings=Settings(
            _env_file=None,
            technical_worker_processes=1,
            technical_max_in_flight=2,
        ),
    )
    coordinator.on_ticker_ready(_ready_event("AAA"))
    coordinator.on_ticker_ready(_ready_event("BBB"))
    coordinator.on_ticker_ready(_ready_event("CCC"))

    assert executor.submitted == ["AAA", "BBB"]
    assert coordinator._pending == {"CCC"}
    assert len(coordinator._futures) == 2
    coordinator.abort()


def test_overlap_process_creation_failure_falls_back_to_sequential(monkeypatch) -> None:
    frame = _synthetic_frame()
    monkeypatch.setattr(
        technical_score_service,
        "ProcessPoolExecutor",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("process creation failed")),
    )
    monkeypatch.setattr(
        technical_score_service,
        "load_preferred_ohlcv_frames",
        lambda _db, _ticker: (frame, frame),
    )

    coordinator = TechnicalScoringOverlapCoordinator(
        _FakeTechnicalDb(),
        run_id=7,
        tickers=["AAA"],
        settings=Settings(_env_file=None, technical_worker_processes=1),
    )
    rows = coordinator.finalize()

    assert coordinator.fallback_reason == "OSError"
    assert [row.ticker for row in rows] == ["AAA"]
    assert rows[0].technical_confidence != "error"


def test_overlap_callback_error_falls_back_to_sequential(monkeypatch) -> None:
    frame = _synthetic_frame()
    first_load = {"value": True}
    _install_immediate_overlap_executor(monkeypatch)

    def load_frames(_db, ticker):
        if ticker.upper() == "AAA" and first_load["value"]:
            first_load["value"] = False
            raise RuntimeError("callback read failed")
        return frame, frame

    monkeypatch.setattr(
        technical_score_service,
        "load_preferred_ohlcv_frames",
        load_frames,
    )
    coordinator = TechnicalScoringOverlapCoordinator(
        _FakeTechnicalDb(),
        run_id=7,
        tickers=["AAA"],
        settings=Settings(_env_file=None, technical_worker_processes=1),
    )
    coordinator.on_ticker_ready(_ready_event("AAA"))
    rows = coordinator.finalize()

    assert coordinator.fallback_reason == "RuntimeError"
    assert rows[0].technical_confidence != "error"


def test_overlap_broken_worker_pool_falls_back_to_sequential(monkeypatch) -> None:
    frame = _synthetic_frame()

    class BrokenFuture:
        def result(self):
            raise technical_score_service.BrokenProcessPool("worker exited")

        def cancel(self):
            return False

    class BrokenExecutor:
        def __init__(self, max_workers):
            pass

        def submit(self, _fn, _item):
            return BrokenFuture()

        def shutdown(self, wait=True, cancel_futures=False):
            pass

    monkeypatch.setattr(technical_score_service, "ProcessPoolExecutor", BrokenExecutor)
    monkeypatch.setattr(
        technical_score_service,
        "wait",
        lambda futures, timeout=None, return_when=None: (set(futures), set()),
    )
    monkeypatch.setattr(
        technical_score_service,
        "load_preferred_ohlcv_frames",
        lambda _db, _ticker: (frame, frame),
    )
    coordinator = TechnicalScoringOverlapCoordinator(
        _FakeTechnicalDb(),
        run_id=7,
        tickers=["AAA"],
        settings=Settings(_env_file=None, technical_worker_processes=1),
    )
    coordinator.on_ticker_ready(_ready_event("AAA"))
    rows = coordinator.finalize()

    assert coordinator.fallback_reason == "BrokenProcessPool"
    assert rows[0].technical_confidence != "error"


def test_overlap_cancellation_never_persists_partial_scores(monkeypatch) -> None:
    frame = _synthetic_frame()
    cancelled = {"value": False}
    _install_immediate_overlap_executor(monkeypatch)
    monkeypatch.setattr(
        technical_score_service,
        "load_preferred_ohlcv_frames",
        lambda _db, _ticker: (frame, frame),
    )
    db = _FakeTechnicalDb()
    coordinator = TechnicalScoringOverlapCoordinator(
        db,
        run_id=7,
        tickers=["AAA"],
        settings=Settings(_env_file=None, technical_worker_processes=1),
        should_cancel=lambda: cancelled["value"],
    )
    coordinator.on_ticker_ready(_ready_event("AAA"))
    cancelled["value"] = True

    with pytest.raises(technical_score_service.TechnicalScoringError, match="cancelled"):
        coordinator.finalize()

    assert not hasattr(db, "rows")


def test_overlap_matches_pure_sequential_and_persists_input_order(monkeypatch) -> None:
    frame = _synthetic_frame()
    monkeypatch.setattr(
        technical_score_service,
        "load_preferred_ohlcv_frames",
        lambda _db, _ticker: (frame, frame),
    )
    monkeypatch.setattr(
        technical_score_service,
        "get_settings",
        lambda: Settings(_env_file=None, technical_pure_boundary_enabled=True),
    )
    sequential = technical_score_service.score_run_technicals(
        _FakeTechnicalDb(),
        run_id=7,
        tickers=["BBB", "AAA"],
    )

    _install_immediate_overlap_executor(monkeypatch)
    coordinator = TechnicalScoringOverlapCoordinator(
        _FakeTechnicalDb(),
        run_id=7,
        tickers=["BBB", "AAA"],
        settings=Settings(_env_file=None, technical_worker_processes=1),
    )
    coordinator.on_ticker_ready(_ready_event("AAA"))
    coordinator.on_ticker_ready(_ready_event("BBB"))
    overlapped = coordinator.finalize()

    assert [row.ticker for row in overlapped] == ["BBB", "AAA"]
    assert coordinator.completed_during_fetch == 2
    assert [_technical_fingerprint(row) for row in overlapped] == [
        _technical_fingerprint(row) for row in sequential
    ]


class _FakeTechnicalDb:
    def execute(self, _statement):
        pass

    def add_all(self, rows):
        self.rows = rows

    def flush(self):
        pass


def _technical_fingerprint(row) -> dict:
    return {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
        if column.name not in {"id", "created_at"}
    }


def _ready_event(ticker: str) -> TickerReadyEvent:
    return TickerReadyEvent(
        ticker=ticker,
        statuses=("SUCCESS",),
        failed=False,
        completed_at=datetime.now(UTC),
    )


def _market_features_for_test(ticker: str, *, qqq_distribution: int) -> dict:
    return {
        "close": 120.0,
        "sma50": 110.0,
        "sma200": 100.0,
        "sma50_slope_pct": 2.0,
        "roc21": 3.0,
        "roc63": 8.0,
        "distribution_count": qqq_distribution if ticker.upper() == "QQQ" else 0,
        "donchian_20_breakout": False,
    }


def _install_immediate_overlap_executor(monkeypatch) -> None:
    class ImmediateFuture:
        def __init__(self, fn, *args):
            self._fn = fn
            self._args = args
            self._resolved = False
            self._value = None

        def result(self):
            if not self._resolved:
                self._value = self._fn(*self._args)
                self._resolved = True
            return self._value

        def cancel(self):
            return False

    class ImmediateExecutor:
        def __init__(self, max_workers):
            self.max_workers = max_workers

        def submit(self, fn, *args):
            return ImmediateFuture(fn, *args)

        def shutdown(self, wait=True, cancel_futures=False):
            pass

    monkeypatch.setattr(technical_score_service, "ProcessPoolExecutor", ImmediateExecutor)
    monkeypatch.setattr(
        technical_score_service,
        "wait",
        lambda futures, timeout=None, return_when=None: ({next(iter(futures))}, set()),
    )


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


def _declining_frame() -> pd.DataFrame:
    dates = pd.date_range("2020-01-01", periods=320, freq="D")
    close = np.linspace(180.0, 80.0, len(dates))
    return pd.DataFrame(
        {
            "date": dates,
            "open": close + 1.0,
            "high": close + 2.0,
            "low": close - 2.0,
            "close": close,
            "volume": np.linspace(120_000.0, 100_000.0, len(dates)),
        }
    )
