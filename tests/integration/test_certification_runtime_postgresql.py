from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from alembic import command
from app.database_safety import configure_guarded_alembic
from app.models.tables import BackgroundJob
from app.observability.correlation import CausalityContext
from app.services.background_job_service import (
    JobStatus,
    claim_next_job,
    enqueue_job,
    mark_job_completed,
)
from app.services.ceri.sec.processor_capability import SEC_CAPABILITY_JOB_TYPES
from app.services.certification_runtime import (
    CertificationRuntimeViolation,
    queue_isolation_status,
)
from app.services.worker_registry import register_worker
from app.settings import ProcessRole, RuntimeMode, Settings

REPO_ROOT = Path(__file__).resolve().parents[2]


def _upgrade(database_url: str) -> None:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    configure_guarded_alembic(config, database_url)
    command.upgrade(config, "head")


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        runtime_mode=RuntimeMode.CERTIFICATION,
        process_role=ProcessRole.DURABLE_WORKER,
        use_durable_pipeline=True,
        durable_worker_process_enabled=True,
        winner_probability_auto_maturation_enabled=False,
        winner_probability_auto_cohort_refresh_enabled=False,
        market_data_prewarm_enabled=False,
    )


def test_due_unrelated_matrix_is_deferred_while_authorized_lineage_executes(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    now = datetime.now(UTC)
    due_types = (
        "WINNER_OUTCOME_MATURATION",
        "WINNER_COHORT_REFRESH",
        "WINNER_LATEST_RESCORE",
        "CERI_PROVIDER_INGEST",
        "MARKET_DATA_PREWARM",
        "STALE_CONTINUATION",
    )
    with Session(engine, expire_on_commit=False) as db:
        worker = register_worker(
            db,
            worker_id="certification-worker",
            queues=("interactive", "broker", "background"),
            heartbeat_timeout_seconds=30,
            now=now,
            instance_id="certification-instance",
        )
        for index, job_type in enumerate(due_types):
            db.add(
                BackgroundJob(
                    job_type=job_type,
                    status=JobStatus.QUEUED,
                    priority=index,
                    payload_json={},
                    max_retries=3,
                    retry_count=0,
                    run_after=now,
                )
            )
        db.add(
            BackgroundJob(
                job_type="WINNER_OUTCOME_MATURATION",
                status="RETRYING",
                priority=0,
                payload_json={},
                max_retries=3,
                retry_count=1,
                run_after=now,
            )
        )
        root = BackgroundJob(
            job_type="FULL_PIPELINE",
            status=JobStatus.QUEUED,
            priority=0,
            payload_json={
                "certification_authorized": True,
                "transition_preflight_plan_id": 3,
            },
            max_retries=3,
            retry_count=0,
            run_after=now,
            root_correlation_id="certification-root",
        )
        db.add(root)
        db.flush()
        root.root_job_id = root.id
        db.add(
            BackgroundJob(
                job_type="CERI_CAPTURE_RUN",
                status=JobStatus.QUEUED,
                priority=1,
                payload_json={},
                max_retries=3,
                retry_count=0,
                run_after=now,
                root_job_id=root.id,
                parent_job_id=root.id,
                root_correlation_id="certification-root",
            )
        )
        db.commit()

        isolation = queue_isolation_status(
            db,
            now=now,
            allow_authorized_lineage=True,
        )
        assert isolation.isolated is False
        assert isolation.unrelated_runnable_jobs == len(due_types) + 1

        claimed_root = claim_next_job(
            db,
            worker.worker_id,
            worker_instance_id="certification-instance",
            queues=("interactive", "broker", "background"),
            certification_only=True,
        )
        assert claimed_root is not None
        assert claimed_root.job_type == "FULL_PIPELINE"
        mark_job_completed(
            db,
            claimed_root,
            execution_token=claimed_root.execution_token,
        )
        db.commit()

        claimed_child = claim_next_job(
            db,
            worker.worker_id,
            worker_instance_id="certification-instance",
            queues=("interactive", "broker", "background"),
            certification_only=True,
        )
        assert claimed_child is not None
        assert claimed_child.job_type == "CERI_CAPTURE_RUN"
        db.rollback()

        remaining = list(
            db.scalars(select(BackgroundJob).where(BackgroundJob.job_type.in_(due_types)))
        )
        assert len(remaining) == len(due_types) + 1
        assert {job.status for job in remaining} == {JobStatus.QUEUED, "RETRYING"}
    engine.dispose()


def test_certification_enqueue_boundary_rejects_unrelated_and_allows_lineage(
    disposable_postgres_database: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    monkeypatch.setattr(
        "app.services.background_job_service.get_settings",
        _settings,
    )
    with Session(engine) as db:
        with pytest.raises(CertificationRuntimeViolation):
            enqueue_job(db, "WINNER_OUTCOME_MATURATION", {})

        root = enqueue_job(
            db,
            "FULL_PIPELINE",
            {
                "certification_authorized": True,
                "transition_preflight_plan_id": 3,
            },
            request_key="certification-root",
        )
        db.flush()
        child = enqueue_job(
            db,
            "CERI_PROVIDER_INGEST",
            {},
            causality=CausalityContext(
                root_correlation_id=root.root_correlation_id,
                causation_id="child-cause",
                background_job_id=root.id,
            ),
        )

        assert root.job_type == "FULL_PIPELINE"
        assert child.root_correlation_id == root.root_correlation_id
    engine.dispose()


def test_sec_capability_exclusion_claims_non_sec_but_never_sec(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    now = datetime.now(UTC)
    with Session(engine, expire_on_commit=False) as db:
        worker = register_worker(
            db,
            worker_id="sec-capability-worker",
            queues=("interactive", "broker", "background"),
            heartbeat_timeout_seconds=30,
            now=now,
            instance_id="sec-capability-instance",
        )
        sec_job = BackgroundJob(
            job_type="CERI_PROVIDER_INGEST",
            status=JobStatus.QUEUED,
            priority=0,
            payload_json={},
            max_retries=3,
            retry_count=0,
            run_after=now,
        )
        non_sec_job = BackgroundJob(
            job_type="WORKER_RECOVERY_PROBE",
            status=JobStatus.QUEUED,
            priority=1,
            payload_json={},
            max_retries=3,
            retry_count=0,
            run_after=now,
        )
        db.add_all((sec_job, non_sec_job))
        db.commit()

        claimed = claim_next_job(
            db,
            worker.worker_id,
            worker_instance_id="sec-capability-instance",
            queues=("interactive", "broker", "background"),
            excluded_job_types=SEC_CAPABILITY_JOB_TYPES,
        )

        assert claimed is not None
        assert claimed.id == non_sec_job.id
        assert claimed.job_type == "WORKER_RECOVERY_PROBE"
        assert db.get(BackgroundJob, sec_job.id).status == JobStatus.QUEUED
    engine.dispose()
