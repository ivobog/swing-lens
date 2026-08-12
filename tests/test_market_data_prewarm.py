from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from app.models.tables import BackgroundJob
from app.services.background_job_service import JobStatus
from app.services.ib_fetch_plan_service import FetchAction
from app.services.market_data_prewarm_service import (
    MARKET_DATA_PREWARM,
    MarketDataPrewarmCancelled,
    MarketDataPrewarmRequest,
    _prepare_preempted_job_for_resume,
    enqueue_market_data_prewarm,
    execute_market_data_prewarm,
    execute_market_data_prewarm_job,
    resolve_prewarm_universe,
)
from app.settings import Settings


def test_explicit_prewarm_universe_is_normalized_and_capped() -> None:
    request = MarketDataPrewarmRequest(
        universe_source="explicit",
        tickers=(" msft", "AAPL", "MSFT", "NVDA"),
        freshness_date=date(2026, 8, 5),
    )
    universe = resolve_prewarm_universe(
        db=object(),
        request=request,
        settings=Settings(_env_file=None, market_data_prewarm_max_tickers=2),
    )

    assert universe.tickers == ("MSFT", "AAPL")
    assert universe.source == "EXPLICIT"
    assert universe.freshness_date == date(2026, 8, 5)
    assert len(universe.fingerprint) == 64


def test_same_frozen_universe_coalesces_active_prewarm_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = BackgroundJob(
        id=7,
        job_type=MARKET_DATA_PREWARM,
        request_key="existing",
        status=JobStatus.QUEUED,
    )
    calls: list[dict[str, object]] = []

    def fake_enqueue(db, job_type, payload, **kwargs):
        calls.append({"job_type": job_type, "payload": payload, **kwargs})
        first.request_key = kwargs["request_key"]
        return first

    monkeypatch.setattr(
        "app.services.market_data_prewarm_service.enqueue_job",
        fake_enqueue,
    )
    settings = Settings(_env_file=None, market_data_prewarm_enabled=True)
    request = MarketDataPrewarmRequest(
        universe_source="TICKERS",
        tickers=("AAPL", "MSFT"),
        freshness_date=date(2026, 8, 5),
    )

    first_job, first_universe = enqueue_market_data_prewarm(object(), request, settings)
    second_job, second_universe = enqueue_market_data_prewarm(object(), request, settings)

    assert first_job is second_job is first
    assert first_universe.fingerprint == second_universe.fingerprint
    assert calls[0]["request_key"] == first_universe.request_key
    assert calls[1]["request_key"] == second_universe.request_key
    assert calls[0]["priority"] == 200


def test_prewarm_rejects_disabled_flag() -> None:
    request = MarketDataPrewarmRequest(universe_source="TICKERS", tickers=("AAPL",))

    with pytest.raises(ValueError, match="disabled"):
        enqueue_market_data_prewarm(
            object(),
            request,
            Settings(_env_file=None, market_data_prewarm_enabled=False),
        )


def test_prewarm_persists_fetch_failures_and_coverage_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = BackgroundJob(
        id=9,
        job_type=MARKET_DATA_PREWARM,
        request_key="fingerprint",
        status=JobStatus.RUNNING,
        payload_json={
            "universe_source": "TICKERS",
            "tickers": ["AAPL", "MSFT"],
            "include_benchmarks": False,
            "freshness_date": "2026-08-05",
            "requested_by": "test",
            "universe_fingerprint": "fingerprint",
        },
    )
    heartbeats = 0

    def heartbeat() -> None:
        nonlocal heartbeats
        heartbeats += 1

    job._heartbeat = heartbeat
    monkeypatch.setattr(
        "app.services.market_data_prewarm_service.is_cancel_requested",
        lambda *_args: False,
    )
    monkeypatch.setattr(
        "app.services.market_data_prewarm_service.build_fetch_plan",
        lambda **kwargs: SimpleNamespace(estimated_request_count=2, tickers=kwargs["tickers"]),
    )
    monkeypatch.setattr(
        "app.services.market_data_prewarm_service.execute_fetch_plan",
        lambda **kwargs: SimpleNamespace(
            id=44,
            status="PARTIAL",
            planned_request_count=2,
            executed_request_count=2,
            success_count=1,
            failure_count=1,
            skipped_count=0,
            items=[
                SimpleNamespace(
                    ticker="AAPL",
                    what_to_show="TRADES",
                    status="FAILED",
                    error_message="temporary failure",
                )
            ],
        ),
    )
    monkeypatch.setattr(
        "app.services.market_data_prewarm_service.summarize_ohlcv_coverage",
        lambda *_args, **_kwargs: SimpleNamespace(
            ready_count=1,
            total_tickers=2,
            stale_count=0,
            missing_count=1,
            insufficient_count=0,
            missing_volume_count=0,
            failed_contract_count=0,
        ),
    )

    result = execute_market_data_prewarm(
        object(),
        job,
        settings=Settings(_env_file=None),
    )

    assert result["status"] == "PARTIAL"
    assert result["coverage_ratio"] == 0.5
    assert result["failure_count"] == 1
    assert result["failures"][0]["ticker"] == "AAPL"
    assert job.status == JobStatus.PARTIAL
    assert heartbeats == 1


def test_prewarm_honors_cancellation_before_building_a_fetch_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = BackgroundJob(
        id=10,
        job_type=MARKET_DATA_PREWARM,
        status=JobStatus.RUNNING,
        payload_json={"tickers": ["AAPL"], "include_benchmarks": False},
    )
    monkeypatch.setattr(
        "app.services.market_data_prewarm_service.is_cancel_requested",
        lambda *_args: True,
    )

    with pytest.raises(MarketDataPrewarmCancelled, match="cancellation"):
        execute_market_data_prewarm(object(), job, settings=Settings(_env_file=None))


def test_request_key_changes_with_session_and_configuration() -> None:
    base = Settings(_env_file=None)
    request = MarketDataPrewarmRequest(
        universe_source="TICKERS",
        tickers=("MSFT", "AAPL"),
        freshness_date=date(2026, 8, 10),
    )
    first = resolve_prewarm_universe(object(), request, settings=base)
    reordered = resolve_prewarm_universe(
        object(),
        MarketDataPrewarmRequest(
            universe_source="TICKERS",
            tickers=("AAPL", "MSFT"),
            freshness_date=date(2026, 8, 10),
        ),
        settings=base,
    )
    next_session = resolve_prewarm_universe(
        object(),
        MarketDataPrewarmRequest(
            universe_source="TICKERS",
            tickers=("MSFT", "AAPL"),
            freshness_date=date(2026, 8, 11),
        ),
        settings=base,
    )
    changed_config = resolve_prewarm_universe(
        object(),
        request,
        settings=Settings(
            _env_file=None,
            market_data_prewarm_config_version="market-data-prewarm-v3",
        ),
    )

    assert first.request_key == reordered.request_key
    assert first.request_key != next_session.request_key
    assert first.request_key != changed_config.request_key
    assert first.bar_size == "1 day"
    assert first.data_types == ("ADJUSTED_LAST", "TRADES")


def test_preempted_prewarm_is_prepared_for_deferred_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.background_worker import JobDeferred

    job = BackgroundJob(
        id=14,
        job_type=MARKET_DATA_PREWARM,
        status=JobStatus.RUNNING,
        requested_cancel=True,
        payload_json={
            "tickers": ["AAPL"],
            "foreground_preemption": {
                "pipeline_run_id": 22,
                "requested_at": "2026-08-12T14:00:00+00:00",
                "deadline_at": "2026-08-12T14:00:45+00:00",
            },
        },
    )
    db = SimpleNamespace(flush=lambda: None)
    monkeypatch.setattr(
        "app.services.market_data_prewarm_service.execute_market_data_prewarm",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(MarketDataPrewarmCancelled("preempted")),
    )

    with pytest.raises(JobDeferred, match="preserved"):
        execute_market_data_prewarm_job(db, job)

    assert job.requested_cancel is False
    assert "foreground_preemption" not in job.payload_json
    assert job.payload_json["preemption_count"] == 1
    assert job.payload_json["preemption_history"][0]["pipeline_run_id"] == 22


def test_preemption_history_records_bounded_stop_latency() -> None:
    job = BackgroundJob(
        id=15,
        job_type=MARKET_DATA_PREWARM,
        requested_cancel=True,
        payload_json={
            "foreground_preemption": {
                "pipeline_run_id": 23,
                "requested_at": "2026-08-12T14:00:00+00:00",
                "deadline_at": "2026-08-12T14:00:45+00:00",
            }
        },
    )
    db = SimpleNamespace(flush=lambda: None)

    _prepare_preempted_job_for_resume(
        db,
        job,
        now=datetime(2026, 8, 12, 14, 0, 12, tzinfo=UTC),
    )

    assert job.payload_json["preemption_history"][0]["stop_latency_seconds"] == 12.0


def test_prewarm_reports_current_fetched_and_request_timing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = BackgroundJob(
        id=16,
        job_type=MARKET_DATA_PREWARM,
        request_key="session-key",
        status=JobStatus.RUNNING,
        payload_json={
            "universe_source": "TICKERS",
            "tickers": ["AAPL", "MSFT"],
            "include_benchmarks": False,
            "freshness_date": "2026-08-11",
            "effective_session": "2026-08-11",
        },
    )
    plan_items = [
        SimpleNamespace(ticker="AAPL", action=FetchAction.SKIP),
        SimpleNamespace(ticker="AAPL", action=FetchAction.SKIP),
        SimpleNamespace(ticker="MSFT", action=FetchAction.TOP_UP_RECENT),
        SimpleNamespace(ticker="MSFT", action=FetchAction.TOP_UP_RECENT),
    ]
    monkeypatch.setattr(
        "app.services.market_data_prewarm_service.is_cancel_requested",
        lambda *_args: False,
    )
    monkeypatch.setattr(
        "app.services.market_data_prewarm_service.build_fetch_plan",
        lambda **_kwargs: SimpleNamespace(items=plan_items),
    )
    fetch_run = SimpleNamespace(
        id=47,
        status="COMPLETED",
        planned_request_count=2,
        decision_counts_json={"requested": 2, "reused": 2},
        executed_request_count=2,
        success_count=2,
        failure_count=0,
        skipped_count=2,
        items=[
            SimpleNamespace(
                ticker="AAPL",
                what_to_show="ADJUSTED_LAST",
                status="SUCCESS",
                attempt_count=1,
                fetched=0,
                error_message=None,
            ),
            SimpleNamespace(
                ticker="MSFT",
                what_to_show="ADJUSTED_LAST",
                status="SUCCESS",
                attempt_count=1,
                fetched=8,
                error_message=None,
            )
        ],
        _performance={"ib_pacing_wait_ms": 3000.0, "ib_network_ms": 450.0},
    )
    monkeypatch.setattr(
        "app.services.market_data_prewarm_service.execute_fetch_plan",
        lambda **_kwargs: fetch_run,
    )
    monkeypatch.setattr(
        "app.services.market_data_prewarm_service.summarize_ohlcv_coverage",
        lambda *_args, **_kwargs: SimpleNamespace(
            ready_count=2,
            total_tickers=2,
            stale_count=0,
            missing_count=0,
            insufficient_count=0,
            missing_volume_count=0,
            failed_contract_count=0,
            items=[
                SimpleNamespace(ticker="AAPL", status="ready"),
                SimpleNamespace(ticker="MSFT", status="ready"),
            ],
        ),
    )

    result = execute_market_data_prewarm(
        object(),
        job,
        settings=Settings(_env_file=None),
    )

    assert result["already_current_tickers"] == ["AAPL"]
    assert result["stale_or_missing_tickers"] == ["MSFT"]
    assert result["fetched_tickers"] == ["MSFT"]
    assert result["coverage_ready_tickers"] == ["AAPL", "MSFT"]
    assert result["requests_made"] == 2
    assert result["ib_pacing_wait_ms"] == 3000.0
