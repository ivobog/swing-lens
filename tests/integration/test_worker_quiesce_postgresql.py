from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session

from alembic import command
from app.database_safety import configure_guarded_alembic
from app.models.tables import BackgroundJob, BackgroundWorker
from app.services.background_job_service import JobStatus, claim_next_job
from app.services.lifecycle_quiesce import (
    WorkerQuiesceIdentityConflict,
    blocking_jobs,
    request_worker_quiesce,
    resume_worker_claims,
    worker_quiesce_identity,
)
from app.services.worker_registry import register_worker
from scripts.ops import lifecycle_probe

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKER_ID = "quiesce-concurrency-worker"


def _upgrade(database_url: str) -> None:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    configure_guarded_alembic(config, database_url)
    command.upgrade(config, "head")


def _seed_worker(db: Session) -> str:
    now = datetime.now(UTC)
    worker = register_worker(
        db,
        worker_id=WORKER_ID,
        queues=("background",),
        heartbeat_timeout_seconds=30,
        hostname="quiesce-test",
        process_id=101,
        process_start=now,
        now=now,
        instance_id="quiesce-instance",
    )
    db.commit()
    return str(worker.instance_id)


def _add_job(db: Session, status: JobStatus = JobStatus.QUEUED) -> BackgroundJob:
    now = datetime.now(UTC)
    job = BackgroundJob(
        job_type="LIFECYCLE_FENCE_TEST",
        status=status,
        priority=100,
        payload_json={},
        operational_metadata_json={},
        run_after=now,
        created_at=now,
    )
    db.add(job)
    db.flush()
    return job


def _reset(engine) -> None:
    with Session(engine) as db:
        db.execute(delete(BackgroundJob))
        resume_worker_claims(db, WORKER_ID)
        db.commit()


def _commit_fence(engine) -> None:
    with Session(engine) as db:
        request_worker_quiesce(db, WORKER_ID)
        db.commit()


def test_postgresql_claim_fence_total_order_cases_a_through_e(
    disposable_postgres_database: str,
    monkeypatch,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)

    # Existing fail-safe: an absent registration and absent worker process is a
    # fail-closed claim state, provided there are no active jobs.
    monkeypatch.setattr(
        lifecycle_probe,
        "_settings",
        lambda: SimpleNamespace(
            database_url=disposable_postgres_database,
            database_connect_timeout_seconds=5,
            job_worker_id=WORKER_ID,
        ),
    )
    monkeypatch.setattr(lifecycle_probe, "_role_processes", lambda: [])
    absent_report = lifecycle_probe._quiesce_report()
    assert absent_report["claimFenceKind"] == "NO_REGISTERED_CLAIMANT"
    assert absent_report["claimFenceEstablished"] is True
    assert absent_report["activeCount"] == 0
    assert absent_report["safeToStop"] is True

    with Session(engine, expire_on_commit=False) as db:
        instance_id = _seed_worker(db)

    # A controller snapshot from an older generation cannot fence a replacement.
    with Session(engine) as observer:
        observed = observer.get(BackgroundWorker, WORKER_ID)
        assert observed is not None
        stale_identity = worker_quiesce_identity(observed)
        with Session(engine) as replacement:
            current = replacement.get(BackgroundWorker, WORKER_ID)
            assert current is not None
            current.generation += 1
            replacement.commit()
        with pytest.raises(WorkerQuiesceIdentityConflict):
            request_worker_quiesce(
                observer,
                WORKER_ID,
                expected_identity=stale_identity,
            )
        observer.rollback()

    # Case A: a claim holding the registration lock commits first. The fence
    # waits, then the post-fence read sees the committed RUNNING blocker.
    with Session(engine) as claim_db:
        _add_job(claim_db)
        claim_db.commit()
        claimed = claim_next_job(
            claim_db,
            worker_id=WORKER_ID,
            worker_instance_id=instance_id,
            queues=("background",),
        )
        assert claimed is not None
        fence_finished = Event()

        def fenced_after_claim() -> None:
            _commit_fence(engine)
            fence_finished.set()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(fenced_after_claim)
            assert not fence_finished.wait(0.25)
            claim_db.commit()
            future.result(timeout=10)
        with Session(engine) as verify:
            assert [row.id for row in blocking_jobs(verify)] == [claimed.id]

    # Case B: once quiescence commits, a later claim returns no work.
    _reset(engine)
    with Session(engine) as db:
        queued = _add_job(db)
        db.commit()
        queued_id = queued.id
    _commit_fence(engine)
    with Session(engine) as db:
        assert (
            claim_next_job(
                db,
                worker_id=WORKER_ID,
                worker_instance_id=instance_id,
                queues=("background",),
            )
            is None
        )
        assert db.get(BackgroundJob, queued_id).status == JobStatus.QUEUED

    # Case C: zero active jobs remains stable before worker acknowledgement.
    with Session(engine) as db:
        worker = db.get(BackgroundWorker, WORKER_ID)
        assert worker is not None
        assert worker.quiesce_requested_at is not None
        assert worker.quiesced_at is None
        assert blocking_jobs(db) == []
        assert (
            db.scalar(
                select(func.count())
                .select_from(BackgroundJob)
                .where(BackgroundJob.status.in_((JobStatus.RUNNING, JobStatus.RECOVERING)))
            )
            == 0
        )

    # Case D: repeated independent claim loops cannot cross a committed fence.
    def attempt_claim() -> int | None:
        with Session(engine) as db:
            job = claim_next_job(
                db,
                worker_id=WORKER_ID,
                worker_instance_id=instance_id,
                queues=("background",),
            )
            db.commit()
            return job.id if job is not None else None

    with ThreadPoolExecutor(max_workers=6) as pool:
        assert list(pool.map(lambda _: attempt_claim(), range(24))) == [None] * 24
    with Session(engine) as db:
        assert db.get(BackgroundJob, queued_id).status == JobStatus.QUEUED
        assert blocking_jobs(db) == []

    # Case E: RUNNING and RECOVERING rows remain blockers after the fence.
    _reset(engine)
    with Session(engine) as db:
        running = _add_job(db, JobStatus.RUNNING)
        recovering = _add_job(db, JobStatus.RECOVERING)
        db.commit()
        blocking_ids = {running.id, recovering.id}
    _commit_fence(engine)
    with Session(engine) as db:
        assert {row.id for row in blocking_jobs(db)} == blocking_ids
    active_report = lifecycle_probe._quiesce_report()
    assert active_report["activeCount"] == 2
    assert active_report["safeToStop"] is False
    assert active_report["reasonCode"] == "ACTIVE_LEASE_BLOCKS_STOP"

    engine.dispose()
