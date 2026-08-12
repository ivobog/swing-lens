from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from time import perf_counter

from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import Session

from app.models.tables import BackgroundJob
from app.services.background_job_service import (
    JobStatus,
    claim_next_job,
    enqueue_job,
    mark_job_completed,
)
from app.services.background_queue import (
    BACKGROUND,
    BROKER,
    INTERACTIVE,
    WorkerClaimState,
    build_worker_claim_groups,
)

ALL_QUEUES = (INTERACTIVE, BROKER, BACKGROUND)


def test_interactive_claim_preempts_large_background_backlog(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        for index in range(200):
            enqueue_job(
                db,
                "CERI_FEATURE_BATCH",
                {"batch": index},
                priority=1,
                coalesce=False,
            )
        interactive = enqueue_job(
            db,
            "FULL_PIPELINE",
            {"pipeline_run_id": 1},
            priority=999,
        )
        db.commit()
        interactive_id = interactive.id

    with Session(engine) as db:
        started = perf_counter()
        claimed = _fair_claim(db, WorkerClaimState(), worker_id="worker-a")
        elapsed = perf_counter() - started
        assert claimed is not None
        assert claimed.id == interactive_id
        assert elapsed < 5
        db.rollback()
    engine.dispose()


def test_aged_background_claim_preempts_new_broker_work(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    now = datetime.now(UTC)
    with Session(engine) as db:
        background = BackgroundJob(
            job_type="CERI_FEATURE_BATCH",
            status=JobStatus.QUEUED,
            priority=999,
            payload_json={},
            max_retries=3,
            run_after=now - timedelta(hours=2),
            created_at=now - timedelta(hours=2),
        )
        broker = BackgroundJob(
            job_type="IB_HISTOGRAM_FETCH",
            status=JobStatus.QUEUED,
            priority=1,
            payload_json={},
            max_retries=3,
            run_after=now,
            created_at=now,
        )
        db.add_all([background, broker])
        db.commit()
        background_id = background.id

    with Session(engine) as db:
        claimed = _fair_claim(db, WorkerClaimState(), worker_id="worker-a")
        assert claimed is not None
        assert claimed.id == background_id
        db.rollback()
    engine.dispose()


def test_continuous_interactive_claims_eventually_yield_to_background(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        for run_id in range(1, 7):
            enqueue_job(db, "FULL_PIPELINE", {"pipeline_run_id": run_id})
        enqueue_job(db, "CERI_FEATURE_BATCH", {"batch": 1})
        db.commit()

    state = WorkerClaimState()
    claimed_types = []
    with Session(engine, expire_on_commit=False) as db:
        for _ in range(5):
            claimed = _fair_claim(db, state, worker_id="worker-a")
            assert claimed is not None
            claimed_types.append(claimed.job_type)
            mark_job_completed(
                db,
                claimed,
                {"ok": True},
                execution_token=claimed.execution_token,
            )
            db.commit()

    assert claimed_types == [
        "FULL_PIPELINE",
        "FULL_PIPELINE",
        "FULL_PIPELINE",
        "FULL_PIPELINE",
        "CERI_FEATURE_BATCH",
    ]
    engine.dispose()


def test_skip_locked_prevents_two_workers_claiming_same_job(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        enqueue_job(db, "FULL_PIPELINE", {"pipeline_run_id": 1})
        enqueue_job(db, "FULL_PIPELINE", {"pipeline_run_id": 2})
        db.commit()

    def claim(worker_id: str) -> int:
        with Session(engine) as db:
            job = _fair_claim(db, WorkerClaimState(), worker_id=worker_id)
            assert job is not None
            claimed_id = job.id
            db.commit()
            return claimed_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        claimed_ids = list(executor.map(claim, ("worker-a", "worker-b")))

    assert len(set(claimed_ids)) == 2
    assert "idx_background_jobs_queue_claim" in {
        index["name"] for index in inspect(engine).get_indexes("background_jobs")
    }
    engine.dispose()


def test_request_key_coalescing_is_preserved_with_queue_fairness(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    request_key = "queue-fairness:full-pipeline:1"
    with Session(engine) as db:
        first = enqueue_job(
            db,
            "FULL_PIPELINE",
            {"pipeline_run_id": 1},
            request_key=request_key,
        )
        db.commit()
        first_id = first.id

    with Session(engine) as db:
        second = enqueue_job(
            db,
            "FULL_PIPELINE",
            {"pipeline_run_id": 1},
            request_key=request_key,
        )
        db.commit()

        assert second.id == first_id
        assert getattr(second, "_coalesced", False) is True
        assert db.scalar(
            select(func.count(BackgroundJob.id)).where(
                BackgroundJob.job_type == "FULL_PIPELINE",
                BackgroundJob.request_key == request_key,
                BackgroundJob.status.in_((JobStatus.QUEUED, JobStatus.RUNNING)),
            )
        ) == 1
    engine.dispose()


def _fair_claim(
    db: Session,
    state: WorkerClaimState,
    *,
    worker_id: str,
) -> BackgroundJob | None:
    groups = build_worker_claim_groups(
        ALL_QUEUES,
        fairness_enabled=True,
        claim_state=state,
        max_consecutive_interactive=4,
        age_promotion_seconds=300,
    )
    job = claim_next_job(
        db,
        worker_id,
        queues=ALL_QUEUES,
        claim_groups=groups,
    )
    if job is not None:
        state.record(job.job_type)
    return job


def _upgrade(database_url: str) -> None:
    env = {**os.environ, "DATABASE_URL": database_url}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
    )
