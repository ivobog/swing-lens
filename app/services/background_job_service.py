from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import BackgroundJob

ERROR_MESSAGE_MAX_LENGTH = 500
RETRY_DELAYS_SECONDS = (60, 180, 600)
TERMINAL_JOB_STATUSES = {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED", "STALE"}


class JobStatus:
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    STALE = "STALE"


def enqueue_job(
    db: Session,
    job_type: str,
    payload: dict[str, Any],
    related_run_id: int | None = None,
    priority: int = 100,
    max_retries: int = 3,
    run_after: datetime | None = None,
) -> BackgroundJob:
    job = BackgroundJob(
        job_type=job_type,
        related_run_id=related_run_id,
        status=JobStatus.QUEUED,
        priority=priority,
        payload_json=payload,
        max_retries=max_retries,
        run_after=run_after or _utcnow(),
    )
    db.add(job)
    db.flush()
    return job


def claim_next_job(db: Session, worker_id: str) -> BackgroundJob | None:
    job_id = db.scalar(
        select(BackgroundJob.id)
        .where(BackgroundJob.status == JobStatus.QUEUED)
        .where(BackgroundJob.run_after <= _utcnow())
        .order_by(
            BackgroundJob.priority.asc(),
            BackgroundJob.created_at.asc(),
        )
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job_id is None:
        return None

    job = db.get(BackgroundJob, job_id)
    if job is None:
        return None

    now = _utcnow()
    job.status = JobStatus.RUNNING
    job.worker_id = worker_id
    job.locked_at = now
    job.started_at = job.started_at or now
    job.error_message = None
    db.flush()
    return job


def mark_job_completed(
    db: Session,
    job: BackgroundJob,
    result: dict[str, Any] | None = None,
) -> None:
    _finish_job(job, JobStatus.COMPLETED, result=result)
    db.flush()


def mark_job_partial(
    db: Session,
    job: BackgroundJob,
    result: dict[str, Any] | None = None,
) -> None:
    _finish_job(job, JobStatus.PARTIAL, result=result)
    db.flush()


def mark_job_cancelled(
    db: Session,
    job: BackgroundJob,
    result: dict[str, Any] | None = None,
) -> None:
    _finish_job(job, JobStatus.CANCELLED, result=result)
    job.requested_cancel = True
    db.flush()


def mark_job_failed_or_retry(
    db: Session,
    job: BackgroundJob,
    error: str | Exception,
    retry_delay: Callable[[int], timedelta] | None = None,
) -> None:
    job.retry_count += 1
    job.error_message = _safe_error(error)
    job.locked_at = None
    job.worker_id = None

    if job.retry_count <= job.max_retries:
        job.status = JobStatus.QUEUED
        job.run_after = _utcnow() + (retry_delay or default_retry_delay)(job.retry_count)
    else:
        job.status = JobStatus.FAILED
        job.completed_at = _utcnow()

    db.flush()


def request_job_cancel(db: Session, job_id: int) -> BackgroundJob:
    job = db.get(BackgroundJob, job_id)
    if job is None:
        raise ValueError(f"Background job {job_id} was not found.")

    job.requested_cancel = True
    if job.status == JobStatus.QUEUED:
        now = _utcnow()
        job.status = JobStatus.CANCELLED
        job.completed_at = now
        job.locked_at = None
        job.worker_id = None

    db.flush()
    return job


def is_cancel_requested(db: Session, job_id: int) -> bool:
    requested_cancel = db.scalar(
        select(BackgroundJob.requested_cancel).where(BackgroundJob.id == job_id)
    )
    return bool(requested_cancel)


def recover_stale_jobs(db: Session, stale_after_seconds: int) -> int:
    cutoff = _utcnow() - timedelta(seconds=stale_after_seconds)
    stale_jobs = db.scalars(
        select(BackgroundJob)
        .where(BackgroundJob.status == JobStatus.RUNNING)
        .where(BackgroundJob.locked_at < cutoff)
    ).all()

    for job in stale_jobs:
        job.locked_at = None
        job.worker_id = None
        job.error_message = "Recovered after stale worker lock."
        if job.retry_count < job.max_retries:
            job.status = JobStatus.QUEUED
            job.run_after = _utcnow()
        else:
            job.status = JobStatus.STALE
            job.completed_at = _utcnow()

    db.flush()
    return len(stale_jobs)


def default_retry_delay(retry_count: int) -> timedelta:
    index = max(0, min(retry_count - 1, len(RETRY_DELAYS_SECONDS) - 1))
    return timedelta(seconds=RETRY_DELAYS_SECONDS[index])


def _finish_job(
    job: BackgroundJob,
    status: str,
    result: dict[str, Any] | None,
) -> None:
    job.status = status
    job.result_json = result
    job.error_message = None
    job.locked_at = None
    job.worker_id = None
    job.completed_at = _utcnow()


def _safe_error(error: str | Exception) -> str:
    return str(error).replace("\n", " ").strip()[:ERROR_MESSAGE_MAX_LENGTH]


def _utcnow() -> datetime:
    return datetime.now(UTC)
