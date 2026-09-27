from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app import worker_supervisor
from app.database_safety import configure_guarded_alembic
from app.models.tables import BackgroundJob, BackgroundWorker
from app.services.background_job_service import JobStatus
from app.services.certification_runtime import queue_isolation_status
from app.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
LIFECYCLE_FIELDS = (
    "status",
    "requested_cancel",
    "worker_id",
    "worker_instance_id",
    "lease_owner",
    "execution_token",
    "locked_at",
    "heartbeat_at",
    "lease_expires_at",
    "run_after",
    "started_at",
    "completed_at",
    "last_progress_at",
    "progress_sequence",
    "progress_stage",
    "progress_current_item",
    "progress_last_completed_item",
    "progress_processed",
    "progress_total",
    "checkpoint_version",
    "stall_detected_at",
    "recovery_count",
    "error_message",
    "retry_count",
    "max_retries",
    "operational_metadata_json",
)


class _ReplacementProcess:
    pid = 90002

    @staticmethod
    def poll() -> None:
        return None


def _upgrade(database_url: str) -> None:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    configure_guarded_alembic(config, database_url)
    command.upgrade(config, "head")


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        job_worker_heartbeat_timeout_seconds=30,
        worker_shutdown_grace_seconds=1,
    )


def _worker(now: datetime, *, worker_id: str, instance_id: str) -> BackgroundWorker:
    return BackgroundWorker(
        worker_id=worker_id,
        queues_json=["interactive", "broker", "background"],
        hostname="fixture-host",
        process_id=90001,
        instance_id=instance_id,
        process_started_at=now - timedelta(hours=1),
        generation=7,
        started_at=now - timedelta(hours=1),
        heartbeat_at=now - timedelta(minutes=10),
    )


def _running_job(
    now: datetime,
    *,
    worker_id: str,
    instance_id: str,
    request_key: str,
    payload: dict[str, object],
    requested_cancel: bool,
    root_correlation_id: str,
) -> BackgroundJob:
    return BackgroundJob(
        job_type="FULL_PIPELINE",
        status=JobStatus.RUNNING,
        request_key=request_key,
        payload_json=payload,
        requested_cancel=requested_cancel,
        retry_count=1,
        max_retries=3,
        worker_id=worker_id,
        worker_instance_id=instance_id,
        lease_owner=worker_id,
        execution_token=f"token-{request_key}",
        locked_at=now - timedelta(minutes=20),
        heartbeat_at=now - timedelta(minutes=10),
        lease_expires_at=now - timedelta(minutes=5),
        run_after=now - timedelta(minutes=20),
        started_at=now - timedelta(minutes=20),
        last_progress_at=now - timedelta(minutes=11),
        progress_sequence=4,
        progress_stage="CERI_PROVIDER_INGEST",
        progress_current_item="fixture-current",
        progress_last_completed_item="fixture-prior",
        progress_processed=4,
        progress_total=10,
        checkpoint_version="fixture-v1",
        recovery_count=2,
        error_message="fixture-preserved-error",
        operational_metadata_json={"fixture": request_key, "retry": {"attempt": 2}},
        root_correlation_id=root_correlation_id,
    )


def _snapshot(job: BackgroundJob) -> dict[str, object]:
    return {field: deepcopy(getattr(job, field)) for field in LIFECYCLE_FIELDS}


def _run_supervisor_cycle(
    monkeypatch: pytest.MonkeyPatch,
    sessions: sessionmaker[Session],
    *,
    worker_id: str,
    certification_session_id: str | None,
) -> None:
    monkeypatch.setattr(worker_supervisor, "SessionLocal", sessions)
    monkeypatch.setattr(worker_supervisor, "get_settings", _settings)
    monkeypatch.setattr(
        worker_supervisor,
        "_registered_worker_process_alive",
        lambda _worker: False,
    )
    monkeypatch.setattr(
        worker_supervisor,
        "_start_worker",
        lambda *_args: _ReplacementProcess(),
    )

    replacement = worker_supervisor._supervise_once(
        worker_id=worker_id,
        queues="interactive,broker,background",
        child=None,
        certification_session_id=certification_session_id,
    )
    assert replacement is not None
    assert replacement.process.pid == _ReplacementProcess.pid


def test_certification_supervisor_reconciles_each_owned_job_independently(
    disposable_postgres_database: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(UTC)
    worker_id = "certification-fixture-worker"
    instance_id = "certification-fixture-instance"
    current_session = "cert-session-current"
    prior_session = "cert-session-prior"

    with sessions() as db:
        db.add(_worker(now, worker_id=worker_id, instance_id=instance_id))
        authorized = _running_job(
            now,
            worker_id=worker_id,
            instance_id=instance_id,
            request_key="authorized-recover",
            payload={
                "certification_authorized": True,
                "transition_preflight_plan_id": 1001,
                "certification_session_id": current_session,
            },
            requested_cancel=False,
            root_correlation_id="authorized-recover-root",
        )
        authorized_cancel = _running_job(
            now,
            worker_id=worker_id,
            instance_id=instance_id,
            request_key="authorized-cancel",
            payload={
                "certification_authorized": True,
                "transition_preflight_plan_id": 1002,
                "certification_session_id": current_session,
            },
            requested_cancel=True,
            root_correlation_id="authorized-cancel-root",
        )
        unrelated = _running_job(
            now,
            worker_id=worker_id,
            instance_id=instance_id,
            request_key="unrelated-prior-session",
            payload={
                "certification_authorized": True,
                "transition_preflight_plan_id": 901,
                "certification_session_id": prior_session,
            },
            requested_cancel=False,
            root_correlation_id="unrelated-prior-root",
        )
        unrelated_cancel = _running_job(
            now,
            worker_id=worker_id,
            instance_id=instance_id,
            request_key="unrelated-prior-session-cancel",
            payload={
                "certification_authorized": True,
                "transition_preflight_plan_id": 902,
                "certification_session_id": prior_session,
            },
            requested_cancel=True,
            root_correlation_id="unrelated-prior-cancel-root",
        )
        db.add_all([authorized, authorized_cancel, unrelated, unrelated_cancel])
        db.commit()
        identities = {
            "authorized": authorized.id,
            "authorized_cancel": authorized_cancel.id,
            "unrelated": unrelated.id,
            "unrelated_cancel": unrelated_cancel.id,
        }
        before_unrelated = _snapshot(unrelated)
        before_unrelated_cancel = _snapshot(unrelated_cancel)

    _run_supervisor_cycle(
        monkeypatch,
        sessions,
        worker_id=worker_id,
        certification_session_id=current_session,
    )

    with sessions() as db:
        rows = {
            name: db.get(BackgroundJob, job_id) for name, job_id in identities.items()
        }
        assert all(row is not None for row in rows.values())
        assert _snapshot(rows["unrelated"]) == before_unrelated
        assert _snapshot(rows["unrelated_cancel"]) == before_unrelated_cancel
        assert rows["authorized"].status == JobStatus.RECOVERING
        assert rows["authorized"].recovery_count == 3
        assert rows["authorized_cancel"].status == JobStatus.CANCELLED
        assert rows["authorized_cancel"].requested_cancel is True
        assert rows["authorized_cancel"].recovery_count == 2
        assert rows["authorized_cancel"].completed_at is not None

        registration = db.get(BackgroundWorker, worker_id)
        assert registration is not None
        assert registration.stopping_at is not None

        isolation = queue_isolation_status(
            db,
            now=datetime.now(UTC),
            allow_authorized_lineage=True,
            certification_session_id=current_session,
        )
        assert isolation.isolated is True
        assert isolation.unrelated_runnable_jobs == 0
    engine.dispose()


def test_normal_supervisor_recovers_non_cancelled_and_cancels_requested_job(
    disposable_postgres_database: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(UTC)
    worker_id = "normal-fixture-worker"
    instance_id = "normal-fixture-instance"

    with sessions() as db:
        db.add(_worker(now, worker_id=worker_id, instance_id=instance_id))
        recoverable = _running_job(
            now,
            worker_id=worker_id,
            instance_id=instance_id,
            request_key="normal-recover",
            payload={},
            requested_cancel=False,
            root_correlation_id="normal-recover-root",
        )
        cancelled = _running_job(
            now,
            worker_id=worker_id,
            instance_id=instance_id,
            request_key="normal-cancel",
            payload={},
            requested_cancel=True,
            root_correlation_id="normal-cancel-root",
        )
        db.add_all([recoverable, cancelled])
        db.commit()
        recoverable_id = recoverable.id
        cancelled_id = cancelled.id

    _run_supervisor_cycle(
        monkeypatch,
        sessions,
        worker_id=worker_id,
        certification_session_id=None,
    )

    with sessions() as db:
        recoverable = db.get(BackgroundJob, recoverable_id)
        cancelled = db.get(BackgroundJob, cancelled_id)
        assert recoverable is not None and recoverable.status == JobStatus.RECOVERING
        assert recoverable.recovery_count == 3
        assert cancelled is not None and cancelled.status == JobStatus.CANCELLED
        assert cancelled.requested_cancel is True
        assert cancelled.recovery_count == 2
        assert cancelled.completed_at is not None
        registration = db.get(BackgroundWorker, worker_id)
        assert registration is not None and registration.stopping_at is not None
    engine.dispose()


def test_expired_unrelated_running_jobs_have_explicit_non_blocking_readiness_semantics(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    now = datetime.now(UTC)
    with Session(engine) as db:
        for requested_cancel in (False, True):
            db.add(
                _running_job(
                    now,
                    worker_id="historical-worker",
                    instance_id="historical-instance",
                    request_key=f"historical-{requested_cancel}",
                    payload={"certification_session_id": "prior-session"},
                    requested_cancel=requested_cancel,
                    root_correlation_id=f"historical-{requested_cancel}-root",
                )
            )
        db.commit()

        isolation = queue_isolation_status(
            db,
            now=now,
            allow_authorized_lineage=True,
            certification_session_id="cert-session-current",
        )
        assert isolation.isolated is True
        assert isolation.unrelated_runnable_jobs == 0

        job = db.scalar(select(BackgroundJob).where(BackgroundJob.requested_cancel.is_(False)))
        assert job is not None
        job.lease_expires_at = now + timedelta(minutes=5)
        db.commit()

        isolation = queue_isolation_status(
            db,
            now=now,
            allow_authorized_lineage=True,
            certification_session_id="cert-session-current",
        )
        assert isolation.isolated is False
        assert isolation.unrelated_runnable_jobs == 1

        job.lease_expires_at = now - timedelta(minutes=5)
        recovering_cancel = _running_job(
            now,
            worker_id="historical-worker",
            instance_id="historical-instance",
            request_key="historical-recovering-cancel",
            payload={"certification_session_id": "prior-session"},
            requested_cancel=True,
            root_correlation_id="historical-recovering-cancel-root",
        )
        recovering_cancel.status = JobStatus.RECOVERING
        recovering_cancel.worker_id = None
        recovering_cancel.worker_instance_id = None
        recovering_cancel.lease_owner = None
        recovering_cancel.execution_token = None
        recovering_cancel.locked_at = None
        recovering_cancel.heartbeat_at = None
        recovering_cancel.lease_expires_at = None
        db.add(recovering_cancel)
        db.commit()

        isolation = queue_isolation_status(
            db,
            now=now,
            allow_authorized_lineage=True,
            certification_session_id="cert-session-current",
        )
        assert isolation.isolated is False
        assert isolation.unrelated_runnable_jobs == 1
    engine.dispose()
