from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import MetaData, Table, case, insert, select, update
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.tables import BackgroundJob, WinnerProcessingRun
from app.services.background_queue import QueueClaimGroup, worker_queue_filter
from app.services.operational_metrics import operational_metrics
from app.services.redaction import redact_sensitive, redacted_token_metadata

ERROR_MESSAGE_MAX_LENGTH = 500
RETRY_DELAYS_SECONDS = (60, 180, 600)
TERMINAL_JOB_STATUSES = {"COMPLETED", "PARTIAL", "FAILED", "BLOCKED", "CANCELLED", "STALE"}
DEFAULT_LEASE_SECONDS = 900
LEASE_EVENT_MAX_COUNT = 50
logger = logging.getLogger(__name__)


class JobLeaseLost(RuntimeError):
    pass


class JobStatus:
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    STALE = "STALE"
    STALLED = "STALLED"
    RECOVERING = "RECOVERING"


ACTIVE_JOB_STATUSES = (JobStatus.QUEUED, JobStatus.RECOVERING, JobStatus.RUNNING)


def enqueue_job(
    db: Session,
    job_type: str,
    payload: dict[str, Any],
    related_run_id: int | None = None,
    priority: int = 100,
    max_retries: int = 3,
    run_after: datetime | None = None,
    request_key: str | None = None,
    workflow_key: str | None = None,
    coalesce: bool = True,
) -> BackgroundJob:
    if not _database_has_job_progress_columns(db):
        return _enqueue_pre_migration_job(
            db,
            job_type=job_type,
            payload=payload,
            related_run_id=related_run_id,
            priority=priority,
            max_retries=max_retries,
            run_after=run_after,
            request_key=request_key,
        )
    if workflow_key and request_key and coalesce:
        existing = workflow_stage_job(db, workflow_key, job_type, request_key)
        if existing is not None:
            existing._coalesced = True
            operational_metrics.increment("swinglens_jobs_coalesced_total", job_type=job_type)
            return existing
    if request_key and coalesce:
        existing = active_job_for_request_key(db, job_type, request_key)
        if existing is not None:
            existing._coalesced = True
            operational_metrics.increment("swinglens_jobs_coalesced_total", job_type=job_type)
            return existing

    if workflow_key is None and not _database_has_workflow_column(db):
        return _enqueue_pre_migration_job(
            db,
            job_type=job_type,
            payload=payload,
            related_run_id=related_run_id,
            priority=priority,
            max_retries=max_retries,
            run_after=run_after,
            request_key=request_key,
        )

    job_values: dict[str, Any] = {
        "job_type": job_type,
        "related_run_id": related_run_id,
        "request_key": request_key,
        "status": JobStatus.QUEUED,
        "priority": priority,
        "payload_json": payload,
        "max_retries": max_retries,
        "run_after": run_after or _utcnow(),
    }
    if workflow_key is not None:
        job_values["workflow_key"] = workflow_key
    job = BackgroundJob(**job_values)
    try:
        begin_nested = getattr(db, "begin_nested", None)
        if request_key and coalesce and callable(begin_nested):
            with begin_nested():
                db.add(job)
                db.flush()
        else:
            db.add(job)
            db.flush()
    except IntegrityError:
        existing = (
            workflow_stage_job(db, workflow_key, job_type, request_key)
            if workflow_key and request_key
            else active_job_for_request_key(db, job_type, request_key)
        )
        if existing is None:
            raise
        existing._coalesced = True
        operational_metrics.increment("swinglens_jobs_coalesced_total", job_type=job_type)
        return existing
    operational_metrics.increment("swinglens_jobs_enqueued_total", job_type=job_type)
    return job


def workflow_stage_job(
    db: Session,
    workflow_key: str | None,
    job_type: str,
    request_key: str | None,
) -> BackgroundJob | None:
    if not workflow_key or not request_key:
        return None
    local_job = _workflow_job_from_local_store(db, workflow_key, job_type, request_key)
    if local_job is not None:
        return local_job
    scalars = getattr(db, "scalars", None)
    if not callable(scalars):
        return None
    rows = scalars(
        select(BackgroundJob)
        .where(BackgroundJob.workflow_key == workflow_key)
        .where(BackgroundJob.job_type == job_type)
        .where(BackgroundJob.request_key == request_key)
        .order_by(BackgroundJob.created_at.asc(), BackgroundJob.id.asc())
        .limit(1)
    )
    values = rows.all() if hasattr(rows, "all") else list(rows)
    return next((row for row in values if isinstance(row, BackgroundJob)), None)


def _database_has_workflow_column(db: Session) -> bool:
    get_bind = getattr(db, "get_bind", None)
    if not callable(get_bind):
        return True


def _database_has_job_progress_columns(db: Session) -> bool:
    get_bind = getattr(db, "get_bind", None)
    if not callable(get_bind):
        return True
    bind = get_bind()
    try:
        names = {column["name"] for column in sa_inspect(bind).get_columns("background_jobs")}
        return "last_progress_at" in names
    except Exception:
        return True
    bind = get_bind()
    try:
        return any(
            column["name"] == "workflow_key"
            for column in sa_inspect(bind).get_columns("background_jobs")
        )
    except Exception:
        return True


def _enqueue_pre_migration_job(
    db: Session,
    *,
    job_type: str,
    payload: dict[str, Any],
    related_run_id: int | None,
    priority: int,
    max_retries: int,
    run_after: datetime | None,
    request_key: str | None,
) -> BackgroundJob:
    legacy_jobs = Table("background_jobs", MetaData(), autoload_with=db.get_bind())
    job_id = db.scalar(
        insert(legacy_jobs)
        .values(
            job_type=job_type,
            related_run_id=related_run_id,
            request_key=request_key,
            status=JobStatus.QUEUED,
            priority=priority,
            payload_json=payload,
            retry_count=0,
            max_retries=max_retries,
            requested_cancel=False,
            run_after=run_after or _utcnow(),
            operational_metadata_json={},
        )
        .returning(legacy_jobs.c.id)
    )
    job = BackgroundJob(
        id=job_id,
        job_type=job_type,
        related_run_id=related_run_id,
        request_key=request_key,
        status=JobStatus.QUEUED,
        priority=priority,
        payload_json=payload,
        retry_count=0,
        max_retries=max_retries,
        requested_cancel=False,
        run_after=run_after or _utcnow(),
        operational_metadata_json={},
    )
    operational_metrics.increment("swinglens_jobs_enqueued_total", job_type=job_type)
    return job


def active_job_for_request_key(
    db: Session,
    job_type: str,
    request_key: str | None,
) -> BackgroundJob | None:
    if not request_key:
        return None

    local_job = _active_job_from_local_store(db, job_type, request_key)
    if local_job is not None:
        return local_job

    scalars = getattr(db, "scalars", None)
    if not callable(scalars):
        return None

    result = scalars(
        select(BackgroundJob)
        .where(BackgroundJob.job_type == job_type)
        .where(BackgroundJob.request_key == request_key)
        .where(BackgroundJob.status.in_(ACTIVE_JOB_STATUSES))
        .order_by(BackgroundJob.created_at.asc(), BackgroundJob.id.asc())
        .limit(1)
    )
    rows = result.all() if hasattr(result, "all") else list(result)
    for row in rows:
        if isinstance(row, BackgroundJob):
            return row
    return None


def claim_next_job(
    db: Session,
    worker_id: str,
    worker_instance_id: str | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    queues: Iterable[str] | None = None,
    claim_groups: Iterable[QueueClaimGroup] | None = None,
) -> BackgroundJob | None:
    groups = (
        tuple(claim_groups)
        if claim_groups is not None
        else (QueueClaimGroup(tuple(queues)) if queues is not None else QueueClaimGroup(()),)
    )
    job_id = None
    for group in groups:
        job_id = _claim_ready_job_id(db, group)
        if job_id is not None:
            break
    if job_id is None:
        return None

    job = db.get(BackgroundJob, job_id)
    if job is None:
        return None

    now = _utcnow()
    execution_token = uuid4().hex
    job.status = JobStatus.RUNNING
    job.worker_id = worker_id
    job.worker_instance_id = worker_instance_id
    job.lease_owner = worker_id
    job.execution_token = execution_token
    job.locked_at = now
    job.heartbeat_at = now
    job.lease_expires_at = _lease_expiry(now, lease_seconds)
    job.started_at = job.started_at or now
    job.error_message = None
    job.last_progress_at = now
    job.progress_sequence = int(job.progress_sequence or 0) + 1
    job.stall_detected_at = None
    metadata = _with_attempt_started(
        _with_lease_event(
            job.operational_metadata_json,
            event_type="CLAIMED",
            occurred_at=now,
            worker_id=worker_id,
            execution_token=execution_token,
        ),
        job=job,
        started_at=now,
    )
    metadata["progress_watchdog"] = {
        "progress_sequence": job.progress_sequence,
        "unchanged_since": now.isoformat(),
        "observed_at": now.isoformat(),
    }
    job.operational_metadata_json = metadata
    db.flush()
    return job


def record_job_progress(
    db: Session,
    *,
    job_id: int,
    execution_token: str,
    stage: str,
    current_item: str | None = None,
    last_completed_item: str | None = None,
    processed: int | None = None,
    total: int | None = None,
    checkpoint_version: str = "job-progress-v1",
    only_if_advanced: bool = False,
) -> None:
    """Persist useful progress and fence the caller in the same transaction."""
    now = _utcnow()
    values: dict[str, Any] = {
        "last_progress_at": now,
        "progress_sequence": BackgroundJob.progress_sequence + 1,
        "progress_stage": stage,
        "progress_current_item": current_item,
        "checkpoint_version": checkpoint_version,
    }
    if last_completed_item is not None:
        values["progress_last_completed_item"] = last_completed_item
    if processed is not None:
        values["progress_processed"] = processed
    if total is not None:
        if processed is not None and total < processed:
            raise ValueError("progress total cannot be less than processed work")
        values["progress_total"] = case(
            (BackgroundJob.progress_total.is_(None), total),
            (BackgroundJob.progress_total < total, total),
            else_=BackgroundJob.progress_total,
        )
    statement = (
        update(BackgroundJob)
        .where(BackgroundJob.id == job_id)
        .where(BackgroundJob.status == JobStatus.RUNNING)
        .where(BackgroundJob.execution_token == execution_token)
    )
    if only_if_advanced and processed is not None:
        statement = statement.where(BackgroundJob.progress_processed < processed)
    result = db.execute(statement.values(**values))
    if result.rowcount != 1:
        if only_if_advanced:
            owner = db.execute(
                select(
                    BackgroundJob.status,
                    BackgroundJob.execution_token,
                    BackgroundJob.progress_processed,
                ).where(BackgroundJob.id == job_id)
            ).one_or_none()
            if (
                owner is not None
                and owner[0] == JobStatus.RUNNING
                and owner[1] == execution_token
                and int(owner[2] or 0) >= int(processed or 0)
            ):
                return
        raise JobLeaseLost(f"Background job {job_id} progress owner was fenced.")
    operational_metrics.increment("swinglens_job_progress_total", stage=stage)


def fence_stalled_jobs(
    db: Session,
    *,
    default_timeout_seconds: int,
    market_data_timeout_seconds: int,
    now: datetime | None = None,
    worker_id: str | None = None,
    worker_instance_id: str | None = None,
    worker_heartbeat_at: datetime | None = None,
    long_stage_timeout_seconds: int = 1800,
) -> list[int]:
    """Fence live-but-not-progressing executions without trusting lease freshness."""
    observed_at = now or _utcnow()
    query = (
        select(BackgroundJob)
        .where(BackgroundJob.status == JobStatus.RUNNING)
        .where(BackgroundJob.last_progress_at.is_not(None))
        .with_for_update(skip_locked=True)
    )
    if worker_id is not None:
        query = query.where(BackgroundJob.worker_id == worker_id)
    if worker_instance_id is not None:
        query = query.where(BackgroundJob.worker_instance_id == worker_instance_id)
    fenced: list[int] = []
    for job in db.scalars(query).all():
        if job.progress_stage == "FETCHING_MARKET_DATA":
            timeout = market_data_timeout_seconds
        elif job.progress_stage in {
            "SCORING_TECHNICALS",
            "CERI_PROVIDER_INGEST",
            "CERI_NORMALIZE",
            "CERI_FEATURE_REBUILD",
            "CAPTURING_CERI",
            "CAPTURING_SETUP_LIFECYCLE",
            "EVALUATING_SETUP_LIFECYCLE",
        }:
            timeout = long_stage_timeout_seconds
        else:
            timeout = default_timeout_seconds
        metadata = dict(job.operational_metadata_json or {})
        observation = dict(metadata.get("progress_watchdog") or {})
        current_sequence = int(job.progress_sequence or 0)
        has_observed_sequence = "progress_sequence" in observation
        observed_sequence = int(observation.get("progress_sequence", current_sequence))
        if has_observed_sequence and observed_sequence != current_sequence:
            unchanged_since = observed_at
            decision = "HEALTHY_PROGRESS_SEQUENCE_ADVANCED"
        else:
            raw_unchanged_since = observation.get("unchanged_since")
            try:
                unchanged_since = datetime.fromisoformat(str(raw_unchanged_since))
            except (TypeError, ValueError):
                unchanged_since = job.last_progress_at or observed_at
            decision = "HEALTHY_WITHIN_PROGRESS_TIMEOUT"
        if unchanged_since.tzinfo is None:
            unchanged_since = unchanged_since.replace(tzinfo=UTC)
        worker_age = _age_seconds(observed_at, worker_heartbeat_at)
        lease_age = _age_seconds(observed_at, job.heartbeat_at)
        progress_age = _age_seconds(observed_at, job.last_progress_at)
        observation = {
            "progress_sequence": current_sequence,
            "unchanged_since": unchanged_since.isoformat(),
            "observed_at": observed_at.isoformat(),
        }
        metadata["progress_watchdog"] = observation
        job.operational_metadata_json = metadata
        if unchanged_since >= observed_at - timedelta(seconds=timeout):
            decision_context = {
                    "job_id": job.id,
                    "run_id": job.related_run_id,
                    "job_type": job.job_type,
                    "worker_heartbeat_age_seconds": worker_age,
                    "job_lease_heartbeat_age_seconds": lease_age,
                    "job_progress_age_seconds": progress_age,
                    "progress_sequence": current_sequence,
                    "stage": job.progress_stage,
                    "stall_threshold_seconds": timeout,
                    "decision": decision,
                }
            logger.info(
                "job.watchdog.decision %s", decision_context, extra=decision_context
            )
            continue
        old_token = job.execution_token
        job.status = JobStatus.STALLED
        job.stall_detected_at = observed_at
        decision = "STALL_PROGRESS_SEQUENCE_FROZEN"
        job.error_message = (
            f"Useful progress sequence {current_sequence} remained frozen for {timeout}s "
            f"at {job.progress_stage or 'UNSPECIFIED'}; lease heartbeat age={lease_age}s"
        )
        job.worker_id = None
        job.worker_instance_id = None
        job.lease_owner = None
        job.execution_token = None
        job.locked_at = None
        job.heartbeat_at = None
        job.lease_expires_at = None
        job.operational_metadata_json = _with_lease_event(
            job.operational_metadata_json,
            event_type="PROGRESS_STALLED_FENCED",
            occurred_at=observed_at,
            worker_id=worker_id,
            execution_token=old_token,
        )
        fenced.append(job.id)
        decision_context = {
                "job_id": job.id,
                "run_id": job.related_run_id,
                "job_type": job.job_type,
                "worker_heartbeat_age_seconds": worker_age,
                "job_lease_heartbeat_age_seconds": lease_age,
                "job_progress_age_seconds": progress_age,
                "progress_sequence": current_sequence,
                "stage": job.progress_stage,
                "stall_threshold_seconds": timeout,
                "decision": decision,
            }
        logger.warning(
            "job.watchdog.decision %s", decision_context, extra=decision_context
        )
        operational_metrics.increment("swinglens_jobs_stalled_total", job_type=job.job_type)
    db.flush()
    return fenced


def requeue_stalled_jobs(
    db: Session,
    *,
    job_ids: Iterable[int] | None = None,
    now: datetime | None = None,
) -> int:
    observed_at = now or _utcnow()
    query = select(BackgroundJob).where(BackgroundJob.status == JobStatus.STALLED)
    if job_ids is not None:
        query = query.where(BackgroundJob.id.in_(tuple(job_ids)))
    recovered = 0
    for job in db.scalars(query.with_for_update(skip_locked=True)).all():
        job.status = JobStatus.RECOVERING
        job.recovery_count = int(job.recovery_count or 0) + 1
        job.operational_metadata_json = _with_lease_event(
            job.operational_metadata_json,
            event_type="PROGRESS_RECOVERY_QUEUED",
            occurred_at=observed_at,
            worker_id=None,
            execution_token=None,
        )
        job.run_after = observed_at
        recovered += 1
    db.flush()
    return recovered


def fence_jobs_for_worker(
    db: Session,
    *,
    worker_id: str,
    reason: str,
    worker_instance_id: str | None = None,
    now: datetime | None = None,
) -> list[int]:
    """Fence jobs after a supervisor has proven their owning process exited."""
    observed_at = now or _utcnow()
    fenced: list[int] = []
    jobs = db.scalars(
        select(BackgroundJob)
        .where(BackgroundJob.status == JobStatus.RUNNING)
        .where(BackgroundJob.worker_id == worker_id)
        .where(
            BackgroundJob.worker_instance_id == worker_instance_id
            if worker_instance_id is not None
            else BackgroundJob.worker_id == worker_id
        )
        .with_for_update(skip_locked=True)
    ).all()
    for job in jobs:
        old_token = job.execution_token
        job.status = JobStatus.STALLED
        job.stall_detected_at = observed_at
        job.error_message = _safe_error(reason)
        job.worker_id = None
        job.worker_instance_id = None
        job.lease_owner = None
        job.execution_token = None
        job.locked_at = None
        job.heartbeat_at = None
        job.lease_expires_at = None
        job.operational_metadata_json = _with_lease_event(
            job.operational_metadata_json,
            event_type="WORKER_PROCESS_FENCED",
            occurred_at=observed_at,
            worker_id=worker_id,
            execution_token=old_token,
        )
        fenced.append(job.id)
    db.flush()
    return fenced


def _claim_ready_job_id(db: Session, group: QueueClaimGroup) -> int | None:
    query = select(BackgroundJob.id).where(
        BackgroundJob.status.in_((JobStatus.QUEUED, JobStatus.RECOVERING))
    )
    query = query.where(BackgroundJob.run_after <= _utcnow())
    if group.queues:
        queue_filter = worker_queue_filter(group.queues)
        if queue_filter is not None:
            query = query.where(queue_filter)
    if group.created_before is not None:
        query = query.where(BackgroundJob.created_at <= group.created_before).order_by(
            BackgroundJob.created_at.asc(),
            BackgroundJob.priority.asc(),
        )
    else:
        query = query.order_by(
            BackgroundJob.priority.asc(),
            BackgroundJob.created_at.asc(),
        )
    return db.scalar(query.with_for_update(skip_locked=True).limit(1))


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


def mark_job_blocked(
    db: Session,
    job: BackgroundJob,
    error: Exception,
    *,
    reason_code: str,
    diagnostics: dict[str, Any] | None = None,
    execution_token: str | None = None,
) -> None:
    expected_token = _expected_execution_token(job, execution_token)
    now = _utcnow()
    result = {
        "status": JobStatus.BLOCKED,
        "reason_code": reason_code,
        "diagnostics": diagnostics or {},
    }
    metadata = _with_attempt_finished(
        job.operational_metadata_json,
        finished_at=now,
        status=JobStatus.BLOCKED,
    )
    metadata["blocked"] = {
        "reason_code": reason_code,
        "diagnostics": redact_sensitive(diagnostics or {}),
        "blocked_at": now.isoformat(),
    }
    _apply_running_job_update(
        db,
        job,
        expected_token,
        {
            "status": JobStatus.BLOCKED,
            "result_json": redact_sensitive(result),
            "error_message": _safe_error(error),
            "locked_at": None,
            "heartbeat_at": None,
            "lease_expires_at": None,
            "worker_id": None,
            "worker_instance_id": None,
            "lease_owner": None,
            "execution_token": None,
            "completed_at": now,
            "operational_metadata_json": metadata,
        },
    )
    operational_metrics.increment(
        "swinglens_jobs_finished_total",
        job_type=job.job_type,
        status=JobStatus.BLOCKED,
    )


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
        "worker_instance_id": None,
        "lease_owner": None,
        "execution_token": None,
        "operational_metadata_json": _with_attempt_finished(
            job.operational_metadata_json,
            finished_at=now,
            status="RETRYING" if retry_count <= job.max_retries else JobStatus.FAILED,
        ),
    }
    if retry_count <= job.max_retries:
        values["status"] = JobStatus.QUEUED
        values["run_after"] = now + (retry_delay or default_retry_delay)(retry_count)
    else:
        values["status"] = JobStatus.FAILED
        values["completed_at"] = now

    _apply_running_job_update(db, job, expected_token, values)
    metric_name = (
        "swinglens_jobs_retry_total"
        if values["status"] == JobStatus.QUEUED
        else "swinglens_jobs_failed_total"
    )
    operational_metrics.increment(
        metric_name,
        job_type=job.job_type,
        status=str(values["status"]),
    )


def mark_job_deferred(
    db: Session,
    job: BackgroundJob,
    *,
    delay: timedelta,
    reason: str,
    execution_token: str | None = None,
) -> None:
    expected_token = _expected_execution_token(job, execution_token)
    now = _utcnow()
    values: dict[str, Any] = {
        "status": JobStatus.QUEUED,
        "run_after": now + delay,
        "error_message": _safe_error(reason),
        "locked_at": None,
        "heartbeat_at": None,
        "lease_expires_at": None,
        "worker_id": None,
        "worker_instance_id": None,
        "lease_owner": None,
        "execution_token": None,
        "operational_metadata_json": _with_attempt_finished(
            job.operational_metadata_json,
            finished_at=now,
            status="DEFERRED",
        ),
    }
    _apply_running_job_update(db, job, expected_token, values)
    operational_metrics.increment(
        "swinglens_jobs_deferred_total",
        job_type=job.job_type,
    )


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
        job.worker_instance_id = None
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

    eligible = [
        job
        for job in stale_jobs
        if job.status == JobStatus.RUNNING
        and job.lease_expires_at is not None
        and job.lease_expires_at < now
    ]
    return _recover_jobs(db, eligible, now=now)


def recover_abandoned_jobs_for_worker(
    db: Session,
    *,
    worker_id: str,
    heartbeat_timeout_seconds: int,
) -> int:
    """Requeue work abandoned when the same durable worker identity restarts."""
    now = _utcnow()
    heartbeat_cutoff = now - timedelta(seconds=heartbeat_timeout_seconds)
    abandoned_jobs = db.scalars(
        select(BackgroundJob)
        .where(BackgroundJob.status == JobStatus.RUNNING)
        .where(BackgroundJob.worker_id == worker_id)
        .where(BackgroundJob.heartbeat_at.is_not(None))
        .where(BackgroundJob.heartbeat_at < heartbeat_cutoff)
    ).all()
    eligible = [
        job
        for job in abandoned_jobs
        if job.status == JobStatus.RUNNING
        and job.worker_id == worker_id
        and job.heartbeat_at is not None
        and job.heartbeat_at < heartbeat_cutoff
    ]
    return _recover_jobs(db, eligible, now=now)


def _recover_jobs(db: Session, jobs: Iterable[BackgroundJob], *, now: datetime) -> int:
    recovered_count = 0
    for job in jobs:
        old_worker_id = job.worker_id
        old_execution_token = job.execution_token
        job.locked_at = None
        job.heartbeat_at = None
        job.lease_expires_at = None
        job.worker_id = None
        job.worker_instance_id = None
        job.lease_owner = None
        job.execution_token = None
        job.error_message = "Recovered after stale worker lock."
        if job.retry_count < job.max_retries:
            job.status = JobStatus.QUEUED
            job.run_after = now
        else:
            job.status = JobStatus.STALE
            job.completed_at = now
        job.operational_metadata_json = _with_attempt_finished(
            _with_lease_event(
                job.operational_metadata_json,
                event_type="RECOVERED",
                occurred_at=now,
                worker_id=old_worker_id,
                execution_token=old_execution_token,
            ),
            finished_at=now,
            status="STALE_RECOVERED",
        )
        if isinstance(db, Session):
            db.execute(
                update(WinnerProcessingRun)
                .where(WinnerProcessingRun.background_job_id == job.id)
                .where(WinnerProcessingRun.status == JobStatus.RUNNING)
                .values(
                    status="LOST",
                    completed_at=now,
                    terminal_reason_code="LEASE_EXPIRED_SUPERSEDED",
                    error_message="Background job lease expired; execution was superseded.",
                )
            )
        recovered_count += 1

    if recovered_count:
        operational_metrics.increment("swinglens_jobs_stale_recovered_total", value=recovered_count)

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
        "result_json": redact_sensitive(result),
        "error_message": None,
        "locked_at": None,
        "heartbeat_at": None,
        "lease_expires_at": None,
        "worker_id": None,
        "worker_instance_id": None,
        "lease_owner": None,
        "execution_token": None,
        "completed_at": now,
        "operational_metadata_json": _with_attempt_finished(
            job.operational_metadata_json,
            finished_at=now,
            status=status,
        ),
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
    operational_metrics.increment(
        "swinglens_jobs_finished_total",
        job_type=job.job_type,
        status=status,
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


def _active_job_from_local_store(
    db: Session,
    job_type: str,
    request_key: str,
) -> BackgroundJob | None:
    for attr_name in ("background_jobs", "jobs", "stale_jobs"):
        rows = getattr(db, attr_name, None)
        if rows is None:
            continue
        candidates = rows.values() if isinstance(rows, dict) else rows
        for row in candidates:
            if not isinstance(row, BackgroundJob):
                continue
            if row.job_type != job_type or row.request_key != request_key:
                continue
            if row.status in ACTIVE_JOB_STATUSES:
                return row
    return None


def _workflow_job_from_local_store(
    db: Session,
    workflow_key: str,
    job_type: str,
    request_key: str,
) -> BackgroundJob | None:
    for attr_name in ("background_jobs", "jobs", "stale_jobs"):
        rows = getattr(db, attr_name, None)
        if rows is None:
            continue
        candidates = rows.values() if isinstance(rows, dict) else rows
        for row in candidates:
            if not isinstance(row, BackgroundJob):
                continue
            if (
                row.workflow_key == workflow_key
                and row.job_type == job_type
                and row.request_key == request_key
            ):
                return row
    return None


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
            **redacted_token_metadata(execution_token),
        }
    )
    updated["lease_events"] = events[-LEASE_EVENT_MAX_COUNT:]
    return updated


def _with_attempt_started(
    metadata: dict[str, Any] | None,
    *,
    job: BackgroundJob,
    started_at: datetime,
) -> dict[str, Any]:
    updated = dict(metadata or {})
    attempt_count = int(updated.get("attempt_count") or 0) + 1
    updated["attempt_count"] = attempt_count
    updated["current_attempt"] = {
        "attempt_number": attempt_count,
        "started_at": started_at.isoformat(),
        "queue_delay_ms": _duration_ms(job.run_after or job.created_at, started_at),
        "original_queue_delay_ms": _duration_ms(job.created_at, started_at),
        "retry_count_at_start": int(job.retry_count or 0),
    }
    return updated


def _with_attempt_finished(
    metadata: dict[str, Any] | None,
    *,
    finished_at: datetime,
    status: str,
) -> dict[str, Any]:
    updated = dict(metadata or {})
    attempt = dict(updated.pop("current_attempt", {}) or {})
    started_at = _parse_datetime(attempt.get("started_at"))
    attempt.update(
        {
            "finished_at": finished_at.isoformat(),
            "execution_duration_ms": _duration_ms(started_at, finished_at),
            "status": status,
        }
    )
    updated["last_attempt"] = attempt
    return updated


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _duration_ms(started_at: datetime | None, completed_at: datetime | None) -> float | None:
    if started_at is None or completed_at is None:
        return None
    if started_at.tzinfo is None and completed_at.tzinfo is not None:
        started_at = started_at.replace(tzinfo=completed_at.tzinfo)
    if completed_at.tzinfo is None and started_at.tzinfo is not None:
        completed_at = completed_at.replace(tzinfo=started_at.tzinfo)
    return round(max(0.0, (completed_at - started_at).total_seconds() * 1000), 3)


def _safe_error(error: str | Exception) -> str:
    return str(redact_sensitive(str(error))).replace("\n", " ").strip()[:ERROR_MESSAGE_MAX_LENGTH]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _age_seconds(observed_at: datetime, value: datetime | None) -> int | None:
    if value is None:
        return None
    comparable = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return max(0, int((observed_at - comparable).total_seconds()))
