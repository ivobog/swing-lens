from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.tables import BackgroundJob

ERROR_MESSAGE_MAX_LENGTH = 500
RETRY_DELAYS_SECONDS = (60, 180, 600)
TERMINAL_JOB_STATUSES = {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED", "STALE"}
DEFAULT_LEASE_SECONDS = 900
LEASE_EVENT_MAX_COUNT = 50


class JobLeaseLost(RuntimeError):
    pass


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


def claim_next_job(
    db: Session,
    worker_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> BackgroundJob | None:
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
    execution_token = uuid4().hex
    job.status = JobStatus.RUNNING
    job.worker_id = worker_id
    job.lease_owner = worker_id
    job.execution_token = execution_token
    job.locked_at = now
    job.heartbeat_at = now
    job.lease_expires_at = _lease_expiry(now, lease_seconds)
    job.started_at = job.started_at or now
    job.error_message = None
    job.operational_metadata_json = _with_lease_event(
        job.operational_metadata_json,
        event_type="CLAIMED",
        occurred_at=now,
        worker_id=worker_id,
        execution_token=execution_token,
    )
    db.flush()
    return job


def mark_job_completed(
    db: Session,
    job: BackgroundJob,
    result: dict[str, Any] | None = None,
    execution_token: str | None = None,
) -> None:
    _finish_job(db, job, JobStatus.COMPLETED, result=result, execution_token=execution_token)


def mark_job_partial(
    db: Session,
    job: BackgroundJob,
    result: dict[str, Any] | None = None,
    execution_token: str | None = None,
) -> None:
    _finish_job(db, job, JobStatus.PARTIAL, result=result, execution_token=execution_token)


def mark_job_cancelled(
    db: Session,
    job: BackgroundJob,
    result: dict[str, Any] | None = None,
    execution_token: str | None = None,
) -> None:
    _finish_job(db, job, JobStatus.CANCELLED, result=result, execution_token=execution_token)
    job.requested_cancel = True
    db.flush()


def mark_job_failed_or_retry(
    db: Session,
    job: BackgroundJob,
    error: str | Exception,
    retry_delay: Callable[[int], timedelta] | None = None,
    execution_token: str | None = None,
) -> None:
    expected_token = _expected_execution_token(job, execution_token)
    now = _utcnow()
    retry_count = job.retry_count + 1
    values: dict[str, Any] = {
        "retry_count": retry_count,
        "error_message": _safe_error(error),
        "locked_at": None,
        "heartbeat_at": None,
        "lease_expires_at": None,
        "worker_id": None,
        "lease_owner": None,
        "execution_token": None,
    }
    if retry_count <= job.max_retries:
        values["status"] = JobStatus.QUEUED
        values["run_after"] = now + (retry_delay or default_retry_delay)(retry_count)
    else:
        values["status"] = JobStatus.FAILED
        values["completed_at"] = now

    _apply_running_job_update(db, job, expected_token, values)


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
        job.heartbeat_at = None
        job.lease_expires_at = None
        job.worker_id = None
        job.lease_owner = None
        job.execution_token = None

    db.flush()
    return job


def is_cancel_requested(db: Session, job_id: int) -> bool:
    requested_cancel = db.scalar(
        select(BackgroundJob.requested_cancel).where(BackgroundJob.id == job_id)
    )
    return bool(requested_cancel)


def heartbeat_job(
    db: Session,
    job: BackgroundJob,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    execution_token: str | None = None,
) -> BackgroundJob:
    expected_token = _expected_execution_token(job, execution_token)

    now = _utcnow()
    values = {
        "heartbeat_at": now,
        "lease_expires_at": _lease_expiry(now, lease_seconds),
    }
    _apply_running_job_update(db, job, expected_token, values)
    return job


def recover_stale_jobs(db: Session, stale_after_seconds: int) -> int:
    now = _utcnow()
    stale_jobs = db.scalars(
        select(BackgroundJob)
        .where(BackgroundJob.status == JobStatus.RUNNING)
        .where(BackgroundJob.lease_expires_at.is_not(None))
        .where(BackgroundJob.lease_expires_at < now)
    ).all()

    recovered_count = 0
    for job in stale_jobs:
        if job.status != JobStatus.RUNNING:
            continue
        if job.lease_expires_at is None or job.lease_expires_at >= now:
            continue
        old_worker_id = job.worker_id
        old_execution_token = job.execution_token
        job.locked_at = None
        job.heartbeat_at = None
        job.lease_expires_at = None
        job.worker_id = None
        job.lease_owner = None
        job.execution_token = None
        job.error_message = "Recovered after stale worker lock."
        if job.retry_count < job.max_retries:
            job.status = JobStatus.QUEUED
            job.run_after = now
        else:
            job.status = JobStatus.STALE
            job.completed_at = now
        job.operational_metadata_json = _with_lease_event(
            job.operational_metadata_json,
            event_type="RECOVERED",
            occurred_at=now,
            worker_id=old_worker_id,
            execution_token=old_execution_token,
        )
        recovered_count += 1

    db.flush()
    return recovered_count


def default_retry_delay(retry_count: int) -> timedelta:
    index = max(0, min(retry_count - 1, len(RETRY_DELAYS_SECONDS) - 1))
    return timedelta(seconds=RETRY_DELAYS_SECONDS[index])


def _finish_job(
    db: Session,
    job: BackgroundJob,
    status: str,
    result: dict[str, Any] | None,
    execution_token: str | None,
) -> None:
    now = _utcnow()
    expected_token = _expected_execution_token(job, execution_token)
    values = {
        "status": status,
        "result_json": result,
        "error_message": None,
        "locked_at": None,
        "heartbeat_at": None,
        "lease_expires_at": None,
        "worker_id": None,
        "lease_owner": None,
        "execution_token": None,
        "completed_at": now,
    }
    if status == JobStatus.CANCELLED:
        values["requested_cancel"] = True
    _apply_running_job_update(
        db,
        job,
        expected_token,
        values,
        allowed_current_statuses={JobStatus.RUNNING, JobStatus.PARTIAL},
    )


def _apply_running_job_update(
    db: Session,
    job: BackgroundJob,
    execution_token: str | None,
    values: dict[str, Any],
    allowed_current_statuses: set[str] | None = None,
) -> None:
    allowed_current_statuses = allowed_current_statuses or {JobStatus.RUNNING}
    if execution_token is not None and job.execution_token != execution_token:
        _record_local_lease_loss(job, execution_token)
        raise JobLeaseLost(f"Background job {job.id} lease is no longer held.")
    if job.status not in allowed_current_statuses:
        _record_local_lease_loss(job, execution_token)
        raise JobLeaseLost(f"Background job {job.id} lease is no longer active.")

    execute = getattr(db, "execute", None)
    if execution_token is not None and callable(execute):
        result = execute(
            update(BackgroundJob)
            .where(BackgroundJob.id == job.id)
            .where(BackgroundJob.status.in_(allowed_current_statuses))
            .where(BackgroundJob.execution_token == execution_token)
            .values(**values)
        )
        if result.rowcount != 1:
            _record_local_lease_loss(job, execution_token)
            raise JobLeaseLost(f"Background job {job.id} lease is no longer held.")

    for key, value in values.items():
        setattr(job, key, value)
    db.flush()


def _expected_execution_token(
    job: BackgroundJob,
    execution_token: str | None,
) -> str | None:
    return execution_token if execution_token is not None else job.execution_token


def _lease_expiry(now: datetime, lease_seconds: int) -> datetime:
    return now + timedelta(seconds=lease_seconds)


def _record_local_lease_loss(job: BackgroundJob, execution_token: str | None) -> None:
    job.operational_metadata_json = _with_lease_event(
        job.operational_metadata_json,
        event_type="LEASE_LOST",
        occurred_at=_utcnow(),
        worker_id=job.worker_id,
        execution_token=execution_token,
    )


def _with_lease_event(
    metadata: dict[str, Any] | None,
    *,
    event_type: str,
    occurred_at: datetime,
    worker_id: str | None,
    execution_token: str | None,
) -> dict[str, Any]:
    updated = dict(metadata or {})
    events = list(updated.get("lease_events") or [])
    events.append(
        {
            "event_type": event_type,
            "occurred_at": occurred_at.isoformat(),
            "worker_id": worker_id,
            "execution_token": execution_token,
        }
    )
    updated["lease_events"] = events[-LEASE_EVENT_MAX_COUNT:]
    return updated


def _safe_error(error: str | Exception) -> str:
    return str(error).replace("\n", " ").strip()[:ERROR_MESSAGE_MAX_LENGTH]


def _utcnow() -> datetime:
    return datetime.now(UTC)
