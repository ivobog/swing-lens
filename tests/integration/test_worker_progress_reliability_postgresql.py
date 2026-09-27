from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import Session, sessionmaker
from test_queue_fairness_postgresql import _seed_pipeline

import app.services.ib_fetch_executor as fetch_executor
from app.models.tables import (
    BackgroundJob,
    BackgroundWorker,
    IBContract,
    IBFetchItem,
    IBFetchRun,
    PipelineRun,
)
from app.services.background_job_service import (
    JobLeaseLost,
    JobStatus,
    _acquisition_retry_manifest,
    claim_next_job,
    enqueue_job,
    fence_stalled_jobs,
    mark_job_failed_or_retry,
    record_job_progress,
    requeue_stalled_jobs,
)
from app.services.bar_cache_service import BarUpsertSummary
from app.services.ib_data_fetcher import HistoricalBar, IBHistoricalRequestError
from app.services.ib_fetch_executor import execute_fetch_plan
from app.services.ib_fetch_plan_service import (
    FetchAction,
    FetchPlan,
    FetchPlanItem,
    build_fetch_plan,
    fetch_plan_from_dict,
    fetch_plan_to_dict,
)
from app.services.process_memory import process_memory_snapshot
from app.services.runtime_mutation_authority import RecoveryAuthority
from app.services.scope_refresh_adoption import admit_frozen_operation, bind_semantic_authority
from app.services.us_market_calendar import latest_completed_us_trading_day
from app.services.work_scope_identity import AcquisitionRequirement, ScopeMember
from app.settings import Settings


def test_stalled_owner_is_fenced_and_late_checkpoint_rolls_back(
    disposable_postgres_database: str,
) -> None:
    _migrate(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(UTC)
    with sessions() as db:
        db.add_all(
            [
                BackgroundWorker(
                    worker_id=worker_id,
                    instance_id=f"{worker_id}-instance",
                    generation=1,
                    queues_json=["interactive", "broker", "background"],
                )
                for worker_id in ("worker-a", "worker-b")
            ]
        )
        _seed_pipeline(db, 117)
        job = enqueue_job(db, "FULL_PIPELINE", {"pipeline_run_id": 117})
        fetch_run = IBFetchRun(
            requested_tickers=["LATE"],
            symbols_including_benchmarks=["LATE"],
            status="RUNNING",
        )
        db.add(fetch_run)
        db.commit()
        job_id = job.id
        fetch_run_id = fetch_run.id
    with sessions() as db:
        job = claim_next_job(db, "worker-a", lease_seconds=900)
        assert job is not None
        old_token = str(job.execution_token)
        db.commit()
    with engine.begin() as connection:
        connection.execute(
            update(BackgroundJob)
            .where(BackgroundJob.id == job_id)
            .values(
                heartbeat_at=now,
                last_progress_at=now - timedelta(minutes=10),
                progress_stage="FETCHING_MARKET_DATA",
                operational_metadata_json={
                    "progress_watchdog": {
                        "progress_sequence": 1,
                        "unchanged_since": (now - timedelta(minutes=10)).isoformat(),
                    }
                },
            )
        )
    with sessions() as watchdog:
        assert fence_stalled_jobs(
            watchdog,
            authority=RecoveryAuthority.normal("test.progress.watchdog"),
            default_timeout_seconds=60,
            market_data_timeout_seconds=120,
            now=now,
            worker_id="worker-a",
        ) == [job_id]
        watchdog.commit()

    with sessions() as late_worker:
        late_worker.add(
            IBFetchItem(
                fetch_run_id=fetch_run_id,
                ticker="LATE",
                what_to_show="TRADES",
                action="TOP_UP_RECENT",
                bar_size="1 day",
                status="SUCCESS",
                execution_token=old_token,
            )
        )
        with pytest.raises(JobLeaseLost):
            record_job_progress(
                late_worker,
                job_id=job_id,
                execution_token=old_token,
                stage="FETCHING_MARKET_DATA",
                current_item="LATE",
                processed=301,
                total=634,
            )
        late_worker.rollback()
    with sessions() as verify:
        assert verify.scalar(select(func.count()).select_from(IBFetchItem)) == 0
        assert (
            requeue_stalled_jobs(
                verify,
                authority=RecoveryAuthority.normal("test.progress.requeue"),
                job_ids=[job_id],
                now=now,
            )
            == 1
        )
        verify.commit()
    with sessions() as replacement:
        job = claim_next_job(replacement, "worker-b", lease_seconds=900)
        assert job is not None
        assert job.execution_token != old_token
        assert job.recovery_count == 1
        replacement.commit()
    engine.dispose()


def test_six_hundred_item_fetch_keeps_session_and_memory_bounded(
    disposable_postgres_database: str,
) -> None:
    _migrate(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    samples: list[tuple[int, int, int]] = []
    items = [_skip_item(f"T{index:04d}") for index in range(600)]
    plan = FetchPlan(
        run_id=None,
        requested_tickers=[item.ticker for item in items],
        symbols_including_benchmarks=[item.ticker for item in items],
        items=items,
        estimated_request_count=0,
        estimated_full_backfills=0,
        estimated_top_ups=0,
        estimated_refreshes=0,
        estimated_skips=600,
        warnings=[],
    )
    with sessions() as db:
        baseline = process_memory_snapshot().private_bytes or process_memory_snapshot().rss_bytes

        def sample(item_db: Session, index: int, _total: int, _ticker: str) -> None:
            if index % 25 == 0:
                snapshot = process_memory_snapshot()
                samples.append(
                    (
                        index,
                        snapshot.private_bytes or snapshot.rss_bytes,
                        len(item_db.identity_map),
                    )
                )

        fetch_run = execute_fetch_plan(
            db,
            plan,
            ib_client_factory=FakeIB,
            settings=Settings(_env_file=None),
            memory_probe=sample,
        )
        assert fetch_run.status == "COMPLETED"
        assert fetch_run.skipped_count == 600
        assert max(identity_size for _, _, identity_size in samples) <= 1
        growth = max(value for _, value, _ in samples) - baseline
        assert growth < 128 * 1024 * 1024
        assert samples[-1][1] - samples[len(samples) // 2][1] < 32 * 1024 * 1024
    engine.dispose()


def test_durable_fetch_retry_reuses_success_and_only_retries_unresolved_item(
    disposable_postgres_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _migrate(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    today = latest_completed_us_trading_day()
    calls = {"A": 0, "B": 0}

    def fetch(_ib, contract, feed, **_kwargs):
        calls[contract.symbol] += 1
        if contract.symbol == "B" and calls["B"] == 1:
            raise IBHistoricalRequestError(
                code=162,
                provider_message="Historical market data service is currently unavailable",
                request_id=4,
            )
        return [
            HistoricalBar(
                ticker=contract.symbol,
                bar_date=today,
                timeframe="1 day",
                open=10,
                high=11,
                low=9,
                close=10,
                volume=100,
                source="IB",
                what_to_show=feed,
                adjustment_type=None,
            )
        ]

    monkeypatch.setattr(
        fetch_executor,
        "resolve_us_stock_contract",
        lambda _db, ticker, _ib: SimpleNamespace(
            contract=SimpleNamespace(symbol=ticker), error_message=None
        ),
    )
    monkeypatch.setattr(fetch_executor, "fetch_daily_bars", fetch)
    monkeypatch.setattr(
        fetch_executor, "cache_bars", lambda *_args, **_kwargs: BarUpsertSummary(inserted=1)
    )
    monkeypatch.setattr(
        fetch_executor.MarketDataObligationService,
        "record_fetch_results",
        lambda *_args, **_kwargs: None,
    )

    class NoWait:
        def wait_before_request(self):
            return True

        def backoff_after_error(self, *_args):
            return True

    items = [
        FetchPlanItem(
            ticker=ticker,
            contract_status="RESOLVED",
            what_to_show="TRADES",
            action=FetchAction.TOP_UP_RECENT,
            duration="10 D",
            bar_size="1 day",
            current_bar_count=1,
            first_bar_date=today,
            latest_bar_date=today,
            required_bars=252,
            reason="test",
            estimated_request_count=1,
            freshness_threshold_date=today,
        )
        for ticker in ("A", "B")
    ]
    plan = FetchPlan(
        run_id=None,
        requested_tickers=["A", "B"],
        symbols_including_benchmarks=["A", "B"],
        items=items,
        estimated_request_count=2,
        estimated_full_backfills=0,
        estimated_top_ups=2,
        estimated_refreshes=0,
        estimated_skips=0,
        warnings=[],
    )
    with Session(engine) as db:
        first = execute_fetch_plan(
            db,
            plan,
            ib_client_factory=FakeIB,
            rate_limiter=NoWait(),
            settings=Settings(_env_file=None, ib_max_retries=1),
        )
        assert first.status == "PARTIAL"
        assert (first.success_count, first.failure_count) == (1, 1)
        fetch_run_id = first.id
    with Session(engine) as db:
        second = execute_fetch_plan(
            db,
            plan,
            fetch_run_id=fetch_run_id,
            ib_client_factory=FakeIB,
            rate_limiter=NoWait(),
            settings=Settings(_env_file=None, ib_max_retries=1),
        )
        assert second.id == fetch_run_id
        assert second.status == "COMPLETED"
        assert (second.success_count, second.failure_count) == (2, 0)
        assert len(second.items) == 2
    assert calls == {"A": 1, "B": 2}
    engine.dispose()


def test_disposable_postgres_frozen_contract_survives_cache_change(
    disposable_postgres_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _migrate(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        db.add(
            IBContract(
                ticker="DOCN",
                ib_conid=111,
                symbol="DOCN",
                sec_type="STK",
                exchange="SMART",
                primary_exchange="NYSE",
                currency="USD",
                local_symbol="DOCN",
                trading_class="DOCN",
                resolution_status="RESOLVED",
                last_resolved_at=datetime.now(UTC),
            )
        )
        db.commit()
        plan = build_fetch_plan(db, ["DOCN"], include_benchmarks=False)
        plan = fetch_plan_from_dict(fetch_plan_to_dict(plan))
        assert {item.contract_identity["conId"] for item in plan.items} == {111}
        cached = db.scalar(select(IBContract).where(IBContract.ticker == "DOCN"))
        cached.ib_conid = 222
        db.commit()

    requested_conids = []
    monkeypatch.setattr(
        fetch_executor,
        "resolve_us_stock_contract",
        lambda *_args: (_ for _ in ()).throw(AssertionError("cache C2 lookup forbidden")),
    )
    monkeypatch.setattr(
        fetch_executor,
        "fetch_daily_bars",
        lambda _ib, contract, *_args, **_kwargs: requested_conids.append(contract.conId) or [],
    )
    monkeypatch.setattr(
        fetch_executor.MarketDataObligationService,
        "record_fetch_results",
        lambda *_args, **_kwargs: None,
    )

    class NoWait:
        def wait_before_request(self):
            return True

        def backoff_after_error(self, *_args):
            return True

    with Session(engine) as db:
        fetch_run = execute_fetch_plan(
            db,
            plan,
            ib_client_factory=FakeIB,
            rate_limiter=NoWait(),
            settings=Settings(_env_file=None, ib_max_retries=1),
        )
        assert fetch_run.failure_count == 2
    assert requested_conids == [111, 111]
    engine.dispose()


def test_204_item_deterministic_checkpoint_does_not_replay_198_successes(
    disposable_postgres_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _migrate(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    today = latest_completed_us_trading_day()
    items = [
        FetchPlanItem(
            ticker=f"S{index:03d}",
            contract_status="RESOLVED",
            what_to_show=feed,
            action=FetchAction.TOP_UP_RECENT,
            duration="10 D",
            bar_size="1 day",
            current_bar_count=1,
            first_bar_date=today,
            latest_bar_date=today,
            required_bars=252,
            reason="reviewed request",
            estimated_request_count=1,
            freshness_threshold_date=today,
        )
        for index in range(102)
        for feed in ("ADJUSTED_LAST", "TRADES")
    ]
    plan = FetchPlan(
        run_id=None,
        requested_tickers=[f"S{index:03d}" for index in range(100)],
        symbols_including_benchmarks=[f"S{index:03d}" for index in range(102)],
        items=items,
        estimated_request_count=204,
        estimated_full_backfills=0,
        estimated_top_ups=204,
        estimated_refreshes=0,
        estimated_skips=0,
        warnings=[],
    )
    with Session(engine) as db:
        fetch_run = IBFetchRun(
            requested_tickers=plan.requested_tickers,
            symbols_including_benchmarks=plan.symbols_including_benchmarks,
            planned_request_count=204,
            success_count=198,
            failure_count=6,
            status="PARTIAL",
        )
        fetch_run.items.extend(
            IBFetchItem(
                ticker=item.ticker,
                what_to_show=item.what_to_show,
                action=item.action.value,
                bar_size=item.bar_size,
                status="FAILED" if index >= 198 else "SUCCESS",
                attempt_count=1,
                decision_metadata_json=(
                    {"retryable": False, "failure_classification": "CONTRACT_NOT_FOUND"}
                    if index >= 198
                    else {"retryable": False}
                ),
            )
            for index, item in enumerate(items)
        )
        db.add(fetch_run)
        db.commit()
        fetch_run_id = fetch_run.id

    monkeypatch.setattr(
        fetch_executor,
        "resolve_us_stock_contract",
        lambda *_args: (_ for _ in ()).throw(AssertionError("no item may be replayed")),
    )
    monkeypatch.setattr(
        fetch_executor.MarketDataObligationService,
        "record_fetch_results",
        lambda *_args, **_kwargs: None,
    )
    with Session(engine) as db:
        resumed = execute_fetch_plan(
            db,
            plan,
            fetch_run_id=fetch_run_id,
            ib_client_factory=FakeIB,
            settings=Settings(_env_file=None, ib_max_retries=3),
        )
        assert resumed.id == fetch_run_id
        assert resumed.status == "PARTIAL"
        assert (resumed.success_count, resumed.failure_count) == (198, 6)
        assert len(resumed.items) == 204
        assert {item.attempt_count for item in resumed.items} == {1}
    engine.dispose()


def test_mixed_transient_and_deterministic_items_retry_only_transient(
    disposable_postgres_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _migrate(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    today = latest_completed_us_trading_day()
    reasons = {
        "TIMEOUT": (True, "PROVIDER_TIMEOUT"),
        "INVALID": (False, "CONTRACT_NOT_FOUND"),
        "MISMATCH": (False, "ACQUISITION_PLAN_REQUEST_SCOPE_MISMATCH"),
    }
    items = [
        FetchPlanItem(
            ticker=ticker,
            contract_status="RESOLVED",
            what_to_show="TRADES",
            action=FetchAction.TOP_UP_RECENT,
            duration="10 D",
            bar_size="1 day",
            current_bar_count=1,
            first_bar_date=today,
            latest_bar_date=today,
            required_bars=252,
            reason="reviewed request",
            estimated_request_count=1,
            freshness_threshold_date=today,
        )
        for ticker in reasons
    ]
    plan = FetchPlan(
        run_id=None,
        requested_tickers=list(reasons),
        symbols_including_benchmarks=list(reasons),
        items=items,
        estimated_request_count=3,
        estimated_full_backfills=0,
        estimated_top_ups=3,
        estimated_refreshes=0,
        estimated_skips=0,
        warnings=[],
    )
    with Session(engine) as db:
        fetch_run = IBFetchRun(
            requested_tickers=list(reasons),
            symbols_including_benchmarks=list(reasons),
            planned_request_count=3,
            failure_count=3,
            status="PARTIAL",
        )
        fetch_run.items.extend(
            IBFetchItem(
                ticker=ticker,
                what_to_show="TRADES",
                action="TOP_UP_RECENT",
                bar_size="1 day",
                status="FAILED",
                attempt_count=1,
                decision_metadata_json={
                    "retryable": retryable,
                    "failure_classification": reason,
                },
            )
            for ticker, (retryable, reason) in reasons.items()
        )
        db.add(fetch_run)
        db.commit()
        fetch_run_id = fetch_run.id

    provider_calls = []
    monkeypatch.setattr(
        fetch_executor,
        "resolve_us_stock_contract",
        lambda _db, ticker, _ib: SimpleNamespace(
            contract=SimpleNamespace(symbol=ticker, conId=1), error_message=None
        ),
    )

    def fetch(_ib, contract, *_args, **_kwargs):
        provider_calls.append(contract.symbol)
        return [
            HistoricalBar(
                ticker=contract.symbol,
                bar_date=today,
                timeframe="1 day",
                open=10,
                high=11,
                low=9,
                close=10,
                volume=100,
                source="IB",
                what_to_show="TRADES",
                adjustment_type=None,
            )
        ]

    monkeypatch.setattr(fetch_executor, "fetch_daily_bars", fetch)
    monkeypatch.setattr(
        fetch_executor,
        "cache_bars",
        lambda *_args, **_kwargs: BarUpsertSummary(unchanged=1),
    )
    monkeypatch.setattr(
        fetch_executor.MarketDataObligationService,
        "record_fetch_results",
        lambda *_args, **_kwargs: None,
    )

    class NoWait:
        def wait_before_request(self):
            return True

        def backoff_after_error(self, *_args):
            return True

    with Session(engine) as db:
        resumed = execute_fetch_plan(
            db,
            plan,
            fetch_run_id=fetch_run_id,
            ib_client_factory=FakeIB,
            rate_limiter=NoWait(),
            settings=Settings(_env_file=None, ib_max_retries=3),
        )
        assert resumed.id == fetch_run_id
        assert (resumed.success_count, resumed.failure_count) == (1, 2)
        assert {
            item.ticker: (item.status, item.decision_metadata_json.get("failure_classification"))
            for item in resumed.items
        } == {
            "TIMEOUT": ("SUCCESS", None),
            "INVALID": ("FAILED", "CONTRACT_NOT_FOUND"),
            "MISMATCH": ("FAILED", "ACQUISITION_PLAN_REQUEST_SCOPE_MISMATCH"),
        }
    assert provider_calls == ["TIMEOUT"]
    engine.dispose()


def test_no_progress_same_plan_retry_stops_after_identical_failure_set(
    disposable_postgres_database: str,
) -> None:
    _migrate(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        _seed_pipeline(db, 501)
        db.add(
            BackgroundWorker(
                worker_id="retry-worker",
                instance_id="retry-instance",
                generation=1,
                queues_json=["interactive", "broker", "background"],
            )
        )
        root_authority = admit_frozen_operation(
            db,
            operation_kind="full-pipeline-run",
            subject_kind="ticker",
            members=(ScopeMember("TICKER", "A"), ScopeMember("TICKER", "B")),
            cycle_key="retry-root:501",
            business_cutoff=datetime.now(UTC),
            provider_source_class="PIPELINE_INPUTS",
            request_type="FULL_PIPELINE",
            requirements=(AcquisitionRequirement("FETCHING_MARKET_DATA"),),
            policy_identity="retry-root-policy",
            scope_definition={"upload_run_id": 501},
        )
        bind_semantic_authority(db.get(PipelineRun, 501), root_authority)
        authority = admit_frozen_operation(
            db,
            operation_kind="ib-price-acquisition",
            subject_kind="ticker",
            members=(ScopeMember("TICKER", "A"), ScopeMember("TICKER", "B")),
            cycle_key="retry-test:501",
            business_cutoff=datetime.now(UTC),
            provider_source_class="INTERACTIVE_BROKERS",
            request_type="HISTORICAL_BARS",
            requirements=(AcquisitionRequirement("A:TRADES"), AcquisitionRequirement("B:TRADES")),
            policy_identity="retry-test-policy",
            scope_definition={"upload_run_id": 501},
            parent_scope_id=root_authority.scope_id,
        )
        job = enqueue_job(
            db,
            "FULL_PIPELINE",
            {"pipeline_run_id": 501},
            related_run_id=501,
            max_retries=3,
        )
        pipeline = db.get(PipelineRun, 501)
        pipeline.result_json = {
            "background_job_id": job.id,
            "ib_fetch_authority": authority.as_dict(),
        }
        fetch_run = IBFetchRun(
            run_id=501,
            requested_tickers=["A", "B"],
            symbols_including_benchmarks=["A", "B"],
            status="PARTIAL",
            scope_id=authority.scope_id,
            refresh_cycle_id=authority.refresh_cycle_id,
            acquisition_plan_id=authority.acquisition_plan_id,
        )
        fetch_run.items.extend(
            [
                IBFetchItem(
                    ticker="A",
                    what_to_show="TRADES",
                    action="TOP_UP_RECENT",
                    bar_size="1 day",
                    status="SUCCESS",
                    attempt_count=1,
                ),
                IBFetchItem(
                    ticker="B",
                    what_to_show="TRADES",
                    action="TOP_UP_RECENT",
                    bar_size="1 day",
                    status="FAILED",
                    attempt_count=1,
                    decision_metadata_json={
                        "provider_error_category": "HISTORICAL_TRANSIENT",
                        "provider_error_code": 162,
                        "retryable": True,
                    },
                ),
            ]
        )
        db.add(fetch_run)
        db.commit()
        job_id = job.id
    with Session(engine) as db:
        job = claim_next_job(db, "retry-worker", lease_seconds=900)
        assert job.id == job_id
        job.progress_stage = "FETCHING_MARKET_DATA"
        db.flush()
        assert _acquisition_retry_manifest(db, job) is not None
        mark_job_failed_or_retry(
            db, job, TimeoutError("provider timeout"), execution_token=job.execution_token
        )
        db.commit()
        assert job.status == JobStatus.QUEUED
        assert job.operational_metadata_json["acquisition_retry_manifest"]["success_count"] == 1
        job.run_after = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    with Session(engine) as db:
        job = claim_next_job(db, "retry-worker", lease_seconds=900)
        assert job.id == job_id
        job.progress_stage = "FETCHING_MARKET_DATA"
        db.flush()
        mark_job_failed_or_retry(
            db, job, TimeoutError("provider timeout"), execution_token=job.execution_token
        )
        db.commit()
        assert job.status == JobStatus.FAILED
        assert job.retry_count == 2
        assert job.operational_metadata_json["failure_classification"]["code"] == (
            "ACQUISITION_NO_PROGRESS_RETRY_GUARD"
        )
        assert db.get(PipelineRun, 501).status == "BLOCKED"
        assert "B/TRADES:HISTORICAL_TRANSIENT" in db.get(PipelineRun, 501).error_message
    engine.dispose()


def test_progress_totals_and_labels_reset_at_stage_boundary(
    disposable_postgres_database: str,
) -> None:
    _migrate(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as setup:
        setup.add(
            BackgroundWorker(
                worker_id="stage-reset-worker",
                instance_id="stage-reset-instance",
                generation=1,
                queues_json=["interactive", "background"],
            )
        )
        _seed_pipeline(setup, 148)
        job = enqueue_job(setup, "FULL_PIPELINE", {"pipeline_run_id": 148})
        setup.commit()
        job_id = int(job.id)

    with sessions() as db:
        job = claim_next_job(db, "stage-reset-worker", lease_seconds=900)
        assert job is not None
        token = str(job.execution_token)
        record_job_progress(
            db,
            job_id=job_id,
            execution_token=token,
            stage="FETCHING_MARKET_DATA",
            current_item=None,
            last_completed_item="SPY:TRADES",
            processed=374,
            total=374,
        )
        db.commit()

        # This is the pipeline-step transition callback: the next stage total
        # is not known yet, so every prior stage-local field must be cleared.
        record_job_progress(
            db,
            job_id=job_id,
            execution_token=token,
            stage="CAPTURING_WINNER_PREDICTIONS",
            current_item=None,
        )
        db.commit()
        db.refresh(job)
        assert job.progress_stage == "CAPTURING_WINNER_PREDICTIONS"
        assert job.progress_processed == 0
        assert job.progress_total is None
        assert job.progress_current_item is None
        assert job.progress_last_completed_item is None

        record_job_progress(
            db,
            job_id=job_id,
            execution_token=token,
            stage="CAPTURING_WINNER_PREDICTIONS",
            current_item="AAA",
            processed=0,
            total=185,
        )
        record_job_progress(
            db,
            job_id=job_id,
            execution_token=token,
            stage="CAPTURING_WINNER_PREDICTIONS",
            current_item=None,
            last_completed_item="ZZZ",
            processed=185,
            total=185,
        )
        # Same-stage stale progress cannot regress the counters.
        record_job_progress(
            db,
            job_id=job_id,
            execution_token=token,
            stage="CAPTURING_WINNER_PREDICTIONS",
            current_item="OLD",
            processed=100,
            total=185,
        )
        db.commit()
        db.refresh(job)
        assert job.progress_processed == 185
        assert job.progress_total == 185
        assert job.progress_last_completed_item == "ZZZ"
    engine.dispose()


class FakeIB:
    def __init__(self) -> None:
        self.connected = False

    def connect(self, *_args, **_kwargs) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def isConnected(self) -> bool:  # noqa: N802
        return self.connected


def _skip_item(ticker: str) -> FetchPlanItem:
    return FetchPlanItem(
        ticker=ticker,
        contract_status="RESOLVED",
        what_to_show="TRADES",
        action=FetchAction.SKIP,
        duration=None,
        bar_size="1 day",
        current_bar_count=300,
        first_bar_date=date(2025, 1, 1),
        latest_bar_date=date(2026, 8, 21),
        required_bars=252,
        reason="already current",
        estimated_request_count=0,
    )


def _migrate(database_url: str) -> None:
    environment = dict(os.environ)
    environment["DATABASE_URL"] = database_url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=os.getcwd(),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
