from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database_safety import run_guarded_alembic_upgrade
from app.models.tables import BackgroundJob, PipelineRun, UploadRun
from app.services.background_job_service import JobStatus, recover_stale_jobs
from app.services.pipeline_execution_authority import claimable_pipeline_job_ids
from app.services.pipeline_invariant_service import (
    InvariantScope,
    inspect_pipeline_invariants,
    invariant_counts,
)
from app.services.readiness_service import (
    ReadinessService,
    _historical_non_authoritative_job_counts,
    _unhealthy_job_counts,
)
from app.services.runtime_mutation_authority import RecoveryAuthority
from app.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("authority_state", "pipeline_status"),
    (
        (None, "WAITING_FOR_CERI_COMPLETION"),
        ("REVOKED", "WAITING_FOR_CERI_COMPLETION"),
        ("QUARANTINED", "WAITING_FOR_CERI_COMPLETION"),
        ("RETIRED", "WAITING_FOR_CERI_COMPLETION"),
        ("ACTIVE", "COMPLETED"),
    ),
)
def test_non_executable_authority_is_historical_not_operational(
    disposable_postgres_database: str,
    tmp_path: Path,
    authority_state: str | None,
    pipeline_status: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    now = datetime.now(UTC)
    pipeline_id, upload_run_id = _seed_pipeline_jobs(
        engine,
        authority_state=authority_state,
        pipeline_status=pipeline_status,
        now=now,
        queued_count=1,
    )

    with Session(engine) as db:
        assert claimable_pipeline_job_ids(db, upload_run_id=upload_run_id) == ()
        assert _unhealthy_job_counts(db, now) == (0, 0, 0)
        assert _historical_non_authoritative_job_counts(db, now) == (2, 1, 1)
        assert (
            recover_stale_jobs(
                db,
                0,
                authority=RecoveryAuthority.normal("readiness-authority-regression"),
            )
            == 0
        )
        expired = next(
            finding
            for finding in inspect_pipeline_invariants(db, now=now)
            if finding.code == "EXPIRED_WORKER_LEASE"
            and finding.pipeline_id == pipeline_id
        )
        assert expired.scope is InvariantScope.HISTORICAL_FORENSIC
        assert expired.operational_actionable is False
        assert invariant_counts((expired,))[expired.disposition.value] == 0

    service = _service(engine, tmp_path, now)
    assert service._jobs_check().status == "ok"
    assert service._queue_pressure_check().status == "ok"
    historical = service._historical_jobs_check()
    assert historical.status == "ok"
    assert historical.message == "non_authoritative=2;queued=1;stale_running=1"
    engine.dispose()


def test_active_authority_keeps_real_stale_and_old_queue_failures(
    disposable_postgres_database: str,
    tmp_path: Path,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    now = datetime.now(UTC)
    pipeline_id, upload_run_id = _seed_pipeline_jobs(
        engine,
        authority_state="ACTIVE",
        pipeline_status="WAITING_FOR_CERI_COMPLETION",
        now=now,
        queued_count=1,
    )

    with Session(engine) as db:
        assert len(claimable_pipeline_job_ids(db, upload_run_id=upload_run_id)) == 2
        assert _unhealthy_job_counts(db, now) == (1, 0, 0)
        assert _historical_non_authoritative_job_counts(db, now) == (0, 0, 0)
        expired = next(
            finding
            for finding in inspect_pipeline_invariants(db, now=now)
            if finding.code == "EXPIRED_WORKER_LEASE"
            and finding.pipeline_id == pipeline_id
        )
        assert expired.scope is InvariantScope.OPERATIONAL_ACTIONABLE
        assert expired.operational_actionable is True
        assert invariant_counts((expired,))[expired.disposition.value] == 1

    service = _service(engine, tmp_path, now)
    assert service._jobs_check().message == "stale_running_jobs:1"
    assert service._queue_pressure_check().message.startswith("queue_oldest_critical:")
    engine.dispose()


def test_run9_shaped_rows_are_preserved_but_not_actionable(
    disposable_postgres_database: str,
    tmp_path: Path,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    now = datetime.now(UTC)
    _pipeline_id, upload_run_id = _seed_pipeline_jobs(
        engine,
        authority_state=None,
        pipeline_status="WAITING_FOR_CERI_COMPLETION",
        now=now,
        queued_count=4,
    )

    with Session(engine) as db:
        before = tuple(
            (job.id, job.status, job.payload_json, job.lease_expires_at)
            for job in db.query(BackgroundJob).order_by(BackgroundJob.id)
        )
        assert claimable_pipeline_job_ids(db, upload_run_id=upload_run_id) == ()
        assert _unhealthy_job_counts(db, now) == (0, 0, 0)
        assert _historical_non_authoritative_job_counts(db, now) == (5, 4, 1)

    service = _service(engine, tmp_path, now)
    assert service._jobs_check().status == "ok"
    assert service._queue_pressure_check().status == "ok"
    assert service._historical_jobs_check().message == (
        "non_authoritative=5;queued=4;stale_running=1"
    )

    with Session(engine) as db:
        after = tuple(
            (job.id, job.status, job.payload_json, job.lease_expires_at)
            for job in db.query(BackgroundJob).order_by(BackgroundJob.id)
        )
    assert after == before
    engine.dispose()


def _seed_pipeline_jobs(
    engine,
    *,
    authority_state: str | None,
    pipeline_status: str,
    now: datetime,
    queued_count: int,
) -> tuple[int, int]:
    old = now - timedelta(days=2)
    with Session(engine) as db:
        upload = UploadRun(filename="run9-shape.csv", row_count=5, status="COMPLETED")
        db.add(upload)
        db.flush()
        pipeline = PipelineRun(
            upload_run_id=upload.id,
            status=pipeline_status,
            execution_authority_state="ACTIVE",
        )
        db.add(pipeline)
        db.flush()
        pipeline.execution_authority_state = authority_state
        db.add(
            BackgroundJob(
                job_type="CERI_PROVIDER_INGEST_BATCH",
                related_run_id=upload.id,
                status=JobStatus.RUNNING,
                payload_json={"pipeline_run_id": pipeline.id},
                worker_id="historical-worker",
                lease_owner="historical-worker",
                lease_expires_at=old,
                heartbeat_at=old,
                run_after=old,
                created_at=old,
            )
        )
        for index in range(queued_count):
            db.add(
                BackgroundJob(
                    job_type="CERI_FEATURE_BATCH",
                    related_run_id=upload.id,
                    request_key=f"historical-{index}",
                    status=JobStatus.QUEUED,
                    payload_json={"pipeline_run_id": pipeline.id},
                    run_after=old,
                    created_at=old,
                )
            )
        db.commit()
        return pipeline.id, upload.id


def _service(engine, tmp_path: Path, now: datetime) -> ReadinessService:
    settings = Settings(
        _env_file=None,
        database_url=str(engine.url),
        upload_dir=tmp_path / "uploads",
        export_dir=tmp_path / "exports",
        cache_dir=tmp_path / "cache",
        db_monitor_log_dir=tmp_path / "logs",
    )
    return ReadinessService(engine=engine, settings=settings, now=now)


def _upgrade(database_url: str) -> None:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    run_guarded_alembic_upgrade(config, database_url, "head")
