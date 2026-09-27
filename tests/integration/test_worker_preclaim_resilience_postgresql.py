from __future__ import annotations

import os
import socket
from datetime import UTC, datetime

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app.models.tables import BackgroundJob, BackgroundWorker
from app.services.background_job_service import JobStatus
from app.services.background_worker import PreClaimInfrastructureError, run_worker_once
from app.services.supervisor_registry import (
    acquire_supervisor,
    retire_supervisor_registration,
)
from app.services.worker_registry import (
    heartbeat_worker,
    mark_worker_infrastructure_degraded,
    mark_worker_infrastructure_healthy,
    register_worker,
    retire_worker_registration,
)


def _upgrade(database_url: str) -> None:
    config = Config("alembic.ini")
    config.attributes["database_url"] = database_url
    command.upgrade(config, "head")


def test_postgresql_53200_before_claim_invalidates_connection_without_job_mutation(
    disposable_postgres_database: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    factory = sessionmaker(bind=engine)
    try:
        with Session(engine) as db:
            job = BackgroundJob(
                job_type="TEST_JOB",
                status=JobStatus.QUEUED,
                payload_json={"evidence": "must-remain-unchanged"},
                retry_count=2,
                max_retries=3,
            )
            db.add(job)
            db.commit()
            job_id = int(job.id)

        def injected_postgres_oom(db, *_args, **_kwargs):
            db.execute(
                text(
                    """
                    DO $$
                    BEGIN
                        RAISE EXCEPTION 'injected pre-claim OOM' USING ERRCODE = '53200';
                    END
                    $$
                    """
                )
            )

        monkeypatch.setattr(
            "app.services.background_worker.claim_next_job",
            injected_postgres_oom,
        )

        with pytest.raises(PreClaimInfrastructureError) as caught:
            run_worker_once(
                worker_id="worker-postgres-oom",
                worker_instance_id="instance-postgres-oom",
                stale_after_seconds=60,
                session_factory=factory,
                handlers={},
                certification_mode=True,
                certification_session_id="cert-postgres-oom",
            )

        assert caught.value.reason_code == "POSTGRES_OUT_OF_MEMORY"
        with Session(engine) as db:
            persisted = db.scalar(select(BackgroundJob).where(BackgroundJob.id == job_id))
            assert persisted is not None
            assert persisted.status == JobStatus.QUEUED
            assert persisted.retry_count == 2
            assert persisted.worker_id is None
            assert persisted.worker_instance_id is None
            assert persisted.lease_owner is None
            assert persisted.execution_token is None
            assert persisted.locked_at is None
            assert persisted.heartbeat_at is None
            assert persisted.lease_expires_at is None
            assert persisted.error_message is None
            assert persisted.payload_json == {"evidence": "must-remain-unchanged"}
    finally:
        engine.dispose()


def test_postgresql_worker_degraded_marker_survives_heartbeat_and_clears_on_recovery(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    hostname = socket.gethostname()
    process_id = os.getpid()
    observed_at = datetime.now(UTC)
    try:
        with Session(engine) as db:
            register_worker(
                db,
                worker_id="worker-degraded",
                queues=("interactive",),
                heartbeat_timeout_seconds=30,
                hostname=hostname,
                process_id=process_id,
                process_start=observed_at,
                instance_id="instance-degraded",
            )
            mark_worker_infrastructure_degraded(
                db,
                "worker-degraded",
                hostname=hostname,
                process_id=process_id,
                instance_id="instance-degraded",
                reason_code="POSTGRES_OUT_OF_MEMORY",
            )
            db.commit()

        with Session(engine) as db:
            heartbeat_worker(
                db,
                "worker-degraded",
                hostname=hostname,
                process_id=process_id,
                instance_id="instance-degraded",
                memory_status="NORMAL",
            )
            db.commit()
            worker = db.get(BackgroundWorker, "worker-degraded")
            assert worker is not None
            assert worker.telemetry_status == "INFRASTRUCTURE_DEGRADED:POSTGRES_OUT_OF_MEMORY"

            mark_worker_infrastructure_healthy(
                db,
                "worker-degraded",
                hostname=hostname,
                process_id=process_id,
                instance_id="instance-degraded",
            )
            db.commit()
            assert worker.telemetry_status == "OK"
    finally:
        engine.dispose()


def test_postgresql_dead_generation_retirement_preserves_jobs(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    observed_at = datetime.now(UTC)
    try:
        with Session(engine) as db:
            worker = register_worker(
                db,
                worker_id="worker-stale",
                queues=("interactive",),
                heartbeat_timeout_seconds=30,
                hostname="dead-host",
                process_id=8820,
                process_start=observed_at,
                instance_id="dead-worker-instance",
            )
            supervisor = acquire_supervisor(
                db,
                worker_id="worker-stale",
                instance_id="dead-supervisor-instance",
                process_id=15972,
                process_started_at=observed_at,
                heartbeat_timeout_seconds=30,
                hostname="dead-host",
            )
            assert supervisor is not None
            job = BackgroundJob(
                job_type="TEST_JOB",
                status=JobStatus.QUEUED,
                payload_json={"evidence": "unchanged"},
                retry_count=2,
            )
            db.add(job)
            db.commit()
            job_id = int(job.id)
            worker_generation = int(worker.generation)
            supervisor_generation = int(supervisor.generation)

        with Session(engine) as db:
            assert (
                retire_worker_registration(
                    db,
                    worker_id="worker-stale",
                    expected_instance_id="dead-worker-instance",
                    expected_generation=worker_generation,
                    expected_process_id=8820,
                )
                is not None
            )
            assert (
                retire_supervisor_registration(
                    db,
                    worker_id="worker-stale",
                    expected_instance_id="dead-supervisor-instance",
                    expected_generation=supervisor_generation,
                    expected_process_id=15972,
                )
                is not None
            )
            db.commit()

        with Session(engine) as db:
            persisted = db.get(BackgroundJob, job_id)
            assert persisted is not None
            assert persisted.status == JobStatus.QUEUED
            assert persisted.retry_count == 2
            assert persisted.worker_id is None
            assert persisted.execution_token is None
            assert persisted.error_message is None
            assert persisted.payload_json == {"evidence": "unchanged"}
    finally:
        engine.dispose()
