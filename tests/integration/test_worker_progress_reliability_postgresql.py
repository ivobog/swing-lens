from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.models.tables import BackgroundJob, BackgroundWorker, IBFetchItem, IBFetchRun
from app.services.background_job_service import (
    JobLeaseLost,
    claim_next_job,
    enqueue_job,
    fence_stalled_jobs,
    record_job_progress,
    requeue_stalled_jobs,
)
from app.services.ib_fetch_executor import execute_fetch_plan
from app.services.ib_fetch_plan_service import FetchAction, FetchPlan, FetchPlanItem
from app.services.process_memory import process_memory_snapshot
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
        assert requeue_stalled_jobs(verify, job_ids=[job_id], now=now) == 1
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
