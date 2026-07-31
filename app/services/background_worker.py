from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from threading import Event
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.db import SessionLocal
from app.models.tables import BackgroundJob
from app.services.background_job_service import (
    JobLeaseLost,
    JobStatus,
    claim_next_job,
    heartbeat_job,
    mark_job_cancelled,
    mark_job_completed,
    mark_job_failed_or_retry,
    mark_job_partial,
    recover_stale_jobs,
)
from app.settings import Settings, get_settings

logger = logging.getLogger(__name__)

JobHandler = Callable[[Session, BackgroundJob], dict[str, Any] | None]


class CancelRequested(Exception):
    pass


def run_worker(
    *,
    settings: Settings | None = None,
    session_factory: sessionmaker[Session] = SessionLocal,
    handlers: Mapping[str, JobHandler] | None = None,
    stop_after_one: bool = False,
    stop_event: Event | None = None,
) -> None:
    settings = settings or get_settings()
    worker_id = settings.job_worker_id
    handlers = handlers or default_job_handlers()

    while stop_event is None or not stop_event.is_set():
        ran_job = run_worker_once(
            worker_id=worker_id,
            stale_after_seconds=settings.job_stale_after_seconds,
            session_factory=session_factory,
            handlers=handlers,
        )
        if stop_after_one:
            return
        if not ran_job:
            if stop_event is None:
                time.sleep(settings.job_poll_interval_seconds)
            else:
                stop_event.wait(settings.job_poll_interval_seconds)


def run_worker_once(
    *,
    worker_id: str,
    stale_after_seconds: int,
    session_factory: sessionmaker[Session],
    handlers: Mapping[str, JobHandler] | None = None,
) -> bool:
    handlers = handlers or default_job_handlers()
    db = session_factory()
    try:
        recovered_count = recover_stale_jobs(db, stale_after_seconds)
        if recovered_count:
            logger.info("job.stale_recovered", extra={"count": recovered_count})
        db.commit()

        job = claim_next_job(db, worker_id, lease_seconds=stale_after_seconds)
        if job is None:
            db.commit()
            return False
        logger.info("job.claimed", extra={"job_id": job.id, "job_type": job.job_type})

        execution_token = job.execution_token
        try:
            def heartbeat() -> None:
                heartbeat_job(
                    db,
                    job,
                    lease_seconds=stale_after_seconds,
                    execution_token=execution_token,
                )
                db.commit()

            heartbeat()
            result = execute_job(db, job, handlers, heartbeat=heartbeat)
            if job.status == JobStatus.PARTIAL:
                mark_job_partial(db, job, result, execution_token=execution_token)
            else:
                mark_job_completed(db, job, result, execution_token=execution_token)
            logger.info("job.completed", extra={"job_id": job.id, "job_type": job.job_type})
        except CancelRequested:
            mark_job_cancelled(db, job, execution_token=execution_token)
            logger.info("job.cancelled", extra={"job_id": job.id, "job_type": job.job_type})
        except JobLeaseLost:
            db.rollback()
            logger.warning("job.lease_lost", extra={"job_id": job.id, "job_type": job.job_type})
            return True
        except Exception as exc:
            mark_job_failed_or_retry(db, job, exc, execution_token=execution_token)
            logger.exception("job.failed", extra={"job_id": job.id, "job_type": job.job_type})
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def execute_job(
    db: Session,
    job: BackgroundJob,
    handlers: Mapping[str, JobHandler] | None = None,
    heartbeat: Callable[[], None] | None = None,
) -> dict[str, Any] | None:
    handler = (handlers or {}).get(job.job_type)
    if handler is None:
        raise ValueError(f"Unsupported job type: {job.job_type}")
    if heartbeat is not None:
        job._heartbeat = heartbeat
    try:
        return handler(db, job)
    finally:
        if heartbeat is not None and hasattr(job, "_heartbeat"):
            delattr(job, "_heartbeat")


def default_job_handlers() -> dict[str, JobHandler]:
    return {"FULL_PIPELINE": _execute_full_pipeline_job}


def _execute_full_pipeline_job(db: Session, job: BackgroundJob) -> dict[str, Any] | None:
    from app.services.background_job_service import is_cancel_requested
    from app.services.pipeline_executor import PipelineCancelled, execute_full_pipeline

    pipeline_run_id = job.payload_json.get("pipeline_run_id")
    if pipeline_run_id is None:
        raise ValueError("FULL_PIPELINE job payload is missing pipeline_run_id.")

    def should_cancel() -> bool:
        heartbeat = getattr(job, "_heartbeat", None)
        if callable(heartbeat):
            heartbeat()
        return is_cancel_requested(db, job.id)

    try:
        result = execute_full_pipeline(
            db=db,
            pipeline_run_id=int(pipeline_run_id),
            should_cancel=should_cancel,
        )
    except PipelineCancelled as exc:
        raise CancelRequested(str(exc)) from exc
    return result.__dict__
