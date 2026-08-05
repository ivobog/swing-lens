from datetime import date
from types import SimpleNamespace

import pytest

from app.models.tables import BackgroundJob
from app.services.background_job_service import JobStatus
from app.services.market_data_prewarm_service import (
    MARKET_DATA_PREWARM,
    MarketDataPrewarmCancelled,
    MarketDataPrewarmRequest,
    enqueue_market_data_prewarm,
    execute_market_data_prewarm,
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
    assert calls[0]["request_key"] == first_universe.fingerprint
    assert calls[1]["request_key"] == second_universe.fingerprint


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
