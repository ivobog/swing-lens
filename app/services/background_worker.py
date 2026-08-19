from __future__ import annotations

import logging
import os
import socket
from collections.abc import Callable, Iterable, Mapping
from datetime import timedelta
from threading import Event, Thread
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.db import SessionLocal
from app.models.tables import BackgroundJob
from app.observability.db_monitor import background_job_scope
from app.services.background_job_service import (
    JobLeaseLost,
    JobStatus,
    claim_next_job,
    heartbeat_job,
    mark_job_cancelled,
    mark_job_completed,
    mark_job_deferred,
    mark_job_failed_or_retry,
    mark_job_partial,
    recover_stale_jobs,
)
from app.services.background_queue import (
    WorkerClaimState,
    build_worker_claim_groups,
    normalize_worker_queues,
)
from app.services.worker_registry import (
    heartbeat_worker,
    mark_worker_stopping,
    register_worker,
)
from app.settings import SecDocumentIncrementalMode, Settings, get_settings

logger = logging.getLogger(__name__)

JobHandler = Callable[[Session, BackgroundJob], dict[str, Any] | None]


class CancelRequested(Exception):
    pass


class JobDeferred(Exception):
    def __init__(self, reason: str, *, delay_seconds: int = 5) -> None:
        super().__init__(reason)
        self.reason = reason
        self.delay_seconds = max(1, int(delay_seconds))


def run_worker(
    *,
    settings: Settings | None = None,
    session_factory: sessionmaker[Session] = SessionLocal,
    handlers: Mapping[str, JobHandler] | None = None,
    worker_id: str | None = None,
    queues: Iterable[str] | None = None,
    stop_after_one: bool = False,
    stop_event: Event | None = None,
) -> None:
    settings = settings or get_settings()
    worker_id = (worker_id or settings.job_worker_id).strip()
    queue_names = normalize_worker_queues(queues)
    handlers = handlers or default_job_handlers()
    hostname = socket.gethostname()
    process_id = os.getpid()
    claim_state = WorkerClaimState()
    runtime_stop_event = stop_event or Event()
    db = session_factory()
    try:
        register_worker(
            db,
            worker_id=worker_id,
            queues=queue_names,
            heartbeat_timeout_seconds=settings.job_worker_heartbeat_timeout_seconds,
            hostname=hostname,
            process_id=process_id,
        )
        log_worker_startup_configuration(db, settings=settings, worker_id=worker_id)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    heartbeat_thread = Thread(
        target=_worker_heartbeat_loop,
        kwargs={
            "worker_id": worker_id,
            "hostname": hostname,
            "process_id": process_id,
            "interval_seconds": settings.job_worker_heartbeat_interval_seconds,
            "session_factory": session_factory,
            "stop_event": runtime_stop_event,
        },
        name=f"{worker_id}-heartbeat",
        daemon=True,
    )
    heartbeat_thread.start()

    try:
        while not runtime_stop_event.is_set():
            ran_job = run_worker_once(
                worker_id=worker_id,
                queues=queue_names,
                stale_after_seconds=settings.job_stale_after_seconds,
                heartbeat_timeout_seconds=settings.job_worker_heartbeat_timeout_seconds,
                fairness_enabled=settings.queue_fairness_enabled,
                max_consecutive_interactive=(
                    settings.job_max_consecutive_interactive_claims
                ),
                age_promotion_seconds=settings.job_age_promotion_seconds,
                claim_state=claim_state,
                session_factory=session_factory,
                handlers=handlers,
                schedule_winner_probability=(
                    settings.winner_probability_auto_maturation_enabled
                ),
            )
            if stop_after_one:
                return
            if not ran_job:
                runtime_stop_event.wait(settings.job_poll_interval_seconds)
    finally:
        runtime_stop_event.set()
        heartbeat_thread.join(
            timeout=max(1.0, settings.job_worker_heartbeat_interval_seconds * 2)
        )
        db = session_factory()
        try:
            mark_worker_stopping(
                db,
                worker_id,
                hostname=hostname,
                process_id=process_id,
            )
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("job.worker.stop_heartbeat_failed", extra={"worker_id": worker_id})
        finally:
            db.close()


def worker_startup_configuration(db: Session, *, settings: Settings) -> dict[str, Any]:
    from app.services.ceri.deployment_identity import current_deployment_identity
    from app.services.ceri.sec.processor_signature import sec_guidance_processor_signature

    # Treat a missing/unreadable migration identity as a startup failure.  On
    # PostgreSQL, swallowing the query error would still leave this worker's
    # registration transaction aborted and make the later commit fail with a
    # misleading secondary exception.
    schema_revision = str(db.scalar(text("select version_num from alembic_version")))
    identity = current_deployment_identity(
        config_hash=None,
        calculation_version=None,
    )
    return {
        "ceri_enabled": settings.ceri_enabled,
        "ceri_batched_workflow_enabled": settings.ceri_batched_workflow_enabled,
        "ceri_provider_ingest_enabled": settings.ceri_provider_ingest_enabled,
        "sec_incremental_mode": settings.sec_document_incremental_mode.value,
        "sec_processor_signature": sec_guidance_processor_signature(),
        "sec_readiness_policy": settings.sec_readiness_policy.value,
        "sec_requests_per_second": settings.sec_requests_per_second,
        "database_schema_revision": schema_revision,
        "deployment_git_sha": identity.get("git_sha"),
        "deployment_git_dirty": identity.get("git_dirty"),
    }


def log_worker_startup_configuration(
    db: Session,
    *,
    settings: Settings,
    worker_id: str,
) -> dict[str, Any]:
    summary = worker_startup_configuration(db, settings=settings)
    logger.info(
        "job.worker.startup_configuration %s",
        summary,
        extra={"worker_id": worker_id, "runtime_configuration": summary},
    )
    if (
        settings.ceri_provider_ingest_enabled
        and settings.sec_document_incremental_mode is SecDocumentIncrementalMode.OFF
    ):
        logger.critical(
            "SEC guidance is using the legacy repeated-download path because "
            "CERI provider ingestion is enabled while SEC document incremental mode is OFF.",
            extra={"worker_id": worker_id, "runtime_configuration": summary},
        )
    return summary


def _worker_heartbeat_loop(
    *,
    worker_id: str,
    hostname: str,
    process_id: int,
    interval_seconds: float,
    session_factory: sessionmaker[Session],
    stop_event: Event,
) -> None:
    while not stop_event.wait(interval_seconds):
        db = session_factory()
        try:
            heartbeat_worker(
                db,
                worker_id,
                hostname=hostname,
                process_id=process_id,
            )
            db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "job.worker.heartbeat_failed",
                extra={"worker_id": worker_id},
            )
        finally:
            db.close()


def run_worker_once(
    *,
    worker_id: str,
    queues: Iterable[str] | None = None,
    stale_after_seconds: int,
    heartbeat_timeout_seconds: int = 30,
    fairness_enabled: bool = False,
    max_consecutive_interactive: int = 4,
    age_promotion_seconds: int = 300,
    claim_state: WorkerClaimState | None = None,
    session_factory: sessionmaker[Session],
    handlers: Mapping[str, JobHandler] | None = None,
    schedule_winner_probability: bool = False,
) -> bool:
    handlers = handlers or default_job_handlers()
    db = session_factory()
    try:
        queue_names = normalize_worker_queues(queues)
        hostname = socket.gethostname()
        process_id = os.getpid()
        register_worker(
            db,
            worker_id=worker_id,
            queues=queue_names,
            heartbeat_timeout_seconds=heartbeat_timeout_seconds,
            hostname=hostname,
            process_id=process_id,
        )
        recovered_count = recover_stale_jobs(db, stale_after_seconds)
        if recovered_count:
            logger.info("job.stale_recovered", extra={"count": recovered_count})
        db.commit()

        if schedule_winner_probability:
            from app.services.winner_probability.scheduler import (
                schedule_primary_h5_maturation,
            )

            schedule_primary_h5_maturation(db)
            db.commit()

        claim_state = claim_state or WorkerClaimState()
        claim_groups = build_worker_claim_groups(
            queue_names,
            fairness_enabled=fairness_enabled,
            claim_state=claim_state,
            max_consecutive_interactive=max_consecutive_interactive,
            age_promotion_seconds=age_promotion_seconds,
        )
        job = claim_next_job(
            db,
            worker_id,
            lease_seconds=stale_after_seconds,
            queues=queue_names,
            claim_groups=claim_groups,
        )
        if job is None:
            db.commit()
            return False
        claim_state.record(job.job_type)
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
                heartbeat_worker(
                    db,
                    worker_id,
                    hostname=hostname,
                    process_id=process_id,
                )
                db.commit()

            heartbeat()
            result = execute_job(db, job, handlers, heartbeat=heartbeat)
            if job.status == JobStatus.PARTIAL:
                mark_job_partial(db, job, result, execution_token=execution_token)
            else:
                mark_job_completed(db, job, result, execution_token=execution_token)
            logger.info("job.completed", extra={"job_id": job.id, "job_type": job.job_type})
        except JobDeferred as exc:
            mark_job_deferred(
                db,
                job,
                delay=timedelta(seconds=exc.delay_seconds),
                reason=exc.reason,
                execution_token=execution_token,
            )
            logger.info(
                "job.deferred",
                extra={"job_id": job.id, "job_type": job.job_type},
            )
        except CancelRequested:
            mark_job_cancelled(db, job, execution_token=execution_token)
            logger.info("job.cancelled", extra={"job_id": job.id, "job_type": job.job_type})
        except JobLeaseLost:
            db.rollback()
            logger.warning("job.lease_lost", extra={"job_id": job.id, "job_type": job.job_type})
            return True
        except Exception as exc:
            db.rollback()
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
    payload = job.payload_json or {}
    run_id = job.related_run_id or payload.get("run_id")
    workflow_key = job.workflow_key or payload.get("workflow_key")
    ticker = payload.get("ticker")
    company = payload.get("company")
    try:
        with background_job_scope(
            job_id=job.id,
            job_type=job.job_type,
            run_id=run_id,
            worker_id=job.worker_id,
            workflow_key=workflow_key,
            ticker=str(ticker) if ticker else None,
            company=str(company) if company else None,
        ):
            return handler(db, job)
    finally:
        if heartbeat is not None and hasattr(job, "_heartbeat"):
            delattr(job, "_heartbeat")


def default_job_handlers() -> dict[str, JobHandler]:
    from app.services.ceri.job_handlers import implemented_ceri_job_handlers
    from app.services.ib_market_intelligence.job_handlers import (
        implemented_ib_intelligence_job_handlers,
    )
    from app.services.market_data_prewarm_service import (
        MARKET_DATA_PREWARM,
        execute_market_data_prewarm_job,
    )
    from app.services.setup_lifecycle.job_handlers import implemented_setup_lifecycle_job_handlers
    from app.services.winner_probability.job_handlers import implemented_winner_job_handlers

    return {
        "FULL_PIPELINE": _execute_full_pipeline_job,
        MARKET_DATA_PREWARM: execute_market_data_prewarm_job,
        **implemented_ib_intelligence_job_handlers(),
        **implemented_ceri_job_handlers(),
        **implemented_setup_lifecycle_job_handlers(),
        **implemented_winner_job_handlers(),
    }


def _execute_full_pipeline_job(db: Session, job: BackgroundJob) -> dict[str, Any] | None:
    from app.services.background_job_service import is_cancel_requested
    from app.services.pipeline_executor import PipelineCancelled, execute_full_pipeline

    pipeline_run_id = job.payload_json.get("pipeline_run_id")
    if pipeline_run_id is None:
        raise ValueError("FULL_PIPELINE job payload is missing pipeline_run_id.")

    def lease_guard() -> None:
        heartbeat = getattr(job, "_heartbeat", None)
        if callable(heartbeat):
            heartbeat()

    def should_cancel() -> bool:
        lease_guard()
        return is_cancel_requested(db, job.id)

    try:
        result = execute_full_pipeline(
            db=db,
            pipeline_run_id=int(pipeline_run_id),
            should_cancel=should_cancel,
            lease_guard=lease_guard,
        )
    except PipelineCancelled as exc:
        raise CancelRequested(str(exc)) from exc
    return result.__dict__
