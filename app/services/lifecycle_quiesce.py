from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import BackgroundJob, BackgroundWorker
from app.services.background_job_service import JobStatus

SHUTDOWN_BLOCKING_JOB_STATES = (JobStatus.RUNNING, JobStatus.RECOVERING)


@dataclass(frozen=True)
class WorkerQuiesceIdentity:
    instance_id: str | None
    generation: int
    process_id: int | None
    process_started_at: datetime | None


class WorkerQuiesceIdentityConflict(RuntimeError):
    """The registration changed before the claim fence could be established."""


def worker_quiesce_identity(worker: BackgroundWorker) -> WorkerQuiesceIdentity:
    return WorkerQuiesceIdentity(
        instance_id=worker.instance_id,
        generation=int(worker.generation or 0),
        process_id=worker.process_id,
        process_started_at=worker.process_started_at,
    )


def request_worker_quiesce(
    db: Session,
    worker_id: str,
    *,
    now: datetime | None = None,
    expected_identity: WorkerQuiesceIdentity | None = None,
) -> BackgroundWorker | None:
    worker = db.scalar(
        select(BackgroundWorker)
        .where(BackgroundWorker.worker_id == worker_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        worker is not None
        and expected_identity is not None
        and worker_quiesce_identity(worker) != expected_identity
    ):
        raise WorkerQuiesceIdentityConflict(
            f"worker registration {worker_id!r} changed before quiescence"
        )
    if worker is None or worker.stopping_at is not None:
        return worker
    if worker.quiesce_requested_at is None:
        worker.quiesce_requested_at = now or datetime.now(UTC)
        worker.quiesced_at = None
    db.flush()
    return worker


def resume_worker_claims(db: Session, worker_id: str) -> bool:
    worker = db.scalar(
        select(BackgroundWorker).where(BackgroundWorker.worker_id == worker_id).with_for_update()
    )
    if worker is None:
        return False
    worker.quiesce_requested_at = None
    worker.quiesced_at = None
    db.flush()
    return True


def blocking_jobs(db: Session) -> list[BackgroundJob]:
    return list(
        db.scalars(
            select(BackgroundJob)
            .where(BackgroundJob.status.in_(SHUTDOWN_BLOCKING_JOB_STATES))
            .order_by(BackgroundJob.id)
        ).all()
    )
