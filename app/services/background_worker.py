from __future__ import annotations

import logging
import os
import socket
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from threading import Event, Thread
from typing import Any
from uuid import uuid4

import psutil
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.db import SessionLocal
from app.models.tables import BackgroundJob, PipelineRun
from app.observability.correlation import worker_job_scope
from app.observability.db_monitor import background_job_scope, job_phase
from app.services.background_job_service import (
    JobLeaseLost,
    JobStatus,
    claim_next_job,
    heartbeat_job,
    is_cancel_requested,
    mark_job_blocked,
    mark_job_cancelled,
    mark_job_completed,
    mark_job_deferred,
    mark_job_failed_or_retry,
    mark_job_partial,
    record_job_progress,
    recover_abandoned_jobs_for_worker,
    recover_stale_jobs,
)
from app.services.background_queue import (
    WorkerClaimState,
    build_worker_claim_groups,
    normalize_worker_queues,
)
from app.services.ceri.sec.processor_capability import (
    SEC_CAPABILITY_JOB_TYPES,
    evaluate_sec_processor_capability,
)
from app.services.ceri.sec.processor_lifecycle import (
    establish_worker_processor_identity,
    lifecycle_state,
)
from app.services.cleanup_service import execute_durable_evidence_retention
from app.services.domain_write_fence import fence_domain_commits
from app.services.operational_metrics import operational_metrics
from app.services.pipeline_prerequisites import PipelineBlockedError
from app.services.process_identity import process_started_at
from app.services.process_memory import (
    WorkerMemoryCritical,
    memory_status,
    process_memory_snapshot,
    runtime_memory_diagnostics,
    start_memory_tracing,
)
from app.services.runtime_mutation_authority import RecoveryAuthority, RuntimeMutationAuthority
from app.services.worker_registry import (
    heartbeat_worker,
    heartbeat_worker_control_loop,
    mark_worker_stopping,
    register_worker,
)
from app.settings import RuntimeMode, SecDocumentIncrementalMode, Settings, get_settings

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
    certification_session_id = None
    if settings.runtime_mode is RuntimeMode.CERTIFICATION:
        from app.services.certification_runtime import require_certification_session_id

        # Validate before worker registration or any heartbeat/control-plane write.
        certification_session_id = require_certification_session_id(settings)
    worker_id = (worker_id or settings.job_worker_id).strip()
    queue_names = normalize_worker_queues(queues)
    handlers = handlers or default_job_handlers()
    hostname = socket.gethostname()
    process_id = os.getpid()
    process_start = process_started_at(process_id)
    instance_id = uuid4().hex
    claim_state = WorkerClaimState()
    runtime_stop_event = stop_event or Event()
    if settings.worker_memory_tracemalloc_enabled:
        start_memory_tracing()
    db = session_factory()
    try:
        register_worker(
            db,
            worker_id=worker_id,
            queues=queue_names,
            heartbeat_timeout_seconds=settings.job_worker_heartbeat_timeout_seconds,
            hostname=hostname,
            process_id=process_id,
            instance_id=instance_id,
            process_start=process_start,
        )
        # Publish the canonical worker process identity before expensive provider/config
        # startup checks. The supervisor can now distinguish a slow-but-alive startup
        # from a missing worker and will not create a thundering herd of replacements.
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
            "instance_id": instance_id,
            "memory_warning_mb": settings.worker_memory_warning_mb,
            "memory_critical_mb": settings.worker_memory_critical_mb,
            "interval_seconds": settings.job_worker_heartbeat_interval_seconds,
            "session_factory": session_factory,
            "stop_event": runtime_stop_event,
        },
        name=f"{worker_id}-heartbeat",
        daemon=True,
    )
    heartbeat_thread.start()

    startup_db = session_factory()
    try:
        processor_authority = (
            RuntimeMutationAuthority.certification(
                "worker.sec_processor_identity",
                session_id=certification_session_id,
            )
            if certification_session_id is not None
            else RuntimeMutationAuthority.normal("worker.sec_processor_identity")
        )
        establish_worker_processor_identity(startup_db, authority=processor_authority)
        log_worker_startup_configuration(
            startup_db,
            settings=settings,
            worker_id=worker_id,
            instance_id=instance_id,
            process_start=process_start,
        )
        startup_db.commit()
    except Exception:
        startup_db.rollback()
        runtime_stop_event.set()
        heartbeat_thread.join(timeout=max(1.0, settings.job_worker_heartbeat_interval_seconds * 2))
        raise
    finally:
        startup_db.close()

    next_evidence_cleanup = 0.0

    try:
        while not runtime_stop_event.is_set():
            if (
                settings.runtime_mode is not RuntimeMode.CERTIFICATION
                and time.monotonic() >= next_evidence_cleanup
            ):
                cleanup_db = session_factory()
                try:
                    execute_durable_evidence_retention(cleanup_db, settings)
                    cleanup_db.commit()
                except Exception:
                    cleanup_db.rollback()
                    logger.exception("job.worker.durable_evidence_retention_failed")
                finally:
                    cleanup_db.close()
                next_evidence_cleanup = time.monotonic() + float(
                    settings.observability_evidence_cleanup_interval_seconds
                )
            ran_job = run_worker_once(
                worker_id=worker_id,
                worker_instance_id=instance_id,
                queues=queue_names,
                stale_after_seconds=settings.job_stale_after_seconds,
                heartbeat_timeout_seconds=settings.job_worker_heartbeat_timeout_seconds,
                fairness_enabled=settings.queue_fairness_enabled,
                max_consecutive_interactive=(settings.job_max_consecutive_interactive_claims),
                age_promotion_seconds=settings.job_age_promotion_seconds,
                claim_state=claim_state,
                session_factory=session_factory,
                handlers=handlers,
                schedule_winner_probability=(settings.winner_probability_auto_maturation_enabled),
                certification_mode=settings.runtime_mode is RuntimeMode.CERTIFICATION,
                certification_session_id=certification_session_id,
                sec_capability_required=(
                    settings.ceri_enabled or settings.ceri_provider_ingest_enabled
                ),
            )
            if stop_after_one:
                return
            if not ran_job:
                runtime_stop_event.wait(settings.job_poll_interval_seconds)
    finally:
        runtime_stop_event.set()
        heartbeat_thread.join(timeout=max(1.0, settings.job_worker_heartbeat_interval_seconds * 2))
        db = session_factory()
        try:
            mark_worker_stopping(
                db,
                worker_id,
                hostname=hostname,
                process_id=process_id,
                instance_id=instance_id,
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
    from app.services.certification_runtime import effective_runtime_configuration

    # Treat a missing/unreadable migration identity as a startup failure.  On
    # PostgreSQL, swallowing the query error would still leave this worker's
    # registration transaction aborted and make the later commit fail with a
    # misleading secondary exception.
    schema_revision = str(db.scalar(text("select version_num from alembic_version")))
    identity = current_deployment_identity(
        config_hash=None,
        calculation_version=None,
        database_schema_revision=schema_revision,
    )
    try:
        processor_state = lifecycle_state(db)
        processor_capability = evaluate_sec_processor_capability(db)
    except AttributeError:
        # Lightweight unit-test/session doubles may only implement the schema
        # revision scalar used above. Real database errors remain fail-closed.
        processor_state = None
        processor_capability = None
    return {
        **effective_runtime_configuration(settings),
        "ceri_enabled": settings.ceri_enabled,
        "ceri_batched_workflow_enabled": settings.ceri_batched_workflow_enabled,
        "ceri_provider_ingest_enabled": settings.ceri_provider_ingest_enabled,
        "sec_incremental_mode": settings.sec_document_incremental_mode.value,
        "sec_processor_signature": sec_guidance_processor_signature(),
        "sec_active_processor_signature": (
            processor_state.active_signature if processor_state is not None else None
        ),
        "sec_processor_compatible": (
            processor_state.deployed_is_active if processor_state is not None else None
        ),
        "sec_capability_state": (
            processor_capability.state.value if processor_capability is not None else None
        ),
        "sec_signature_algorithm_version": (
            processor_capability.algorithm_version if processor_capability is not None else None
        ),
        "sec_readiness_policy": settings.sec_readiness_policy.value,
        "sec_requests_per_second": settings.sec_requests_per_second,
        "database_schema_revision": schema_revision,
        "deployment_git_sha": identity.get("git_sha"),
        "deployment_git_dirty": identity.get("git_dirty"),
        "winner_probability_auto_maturation_enabled": (
            settings.winner_probability_auto_maturation_enabled
        ),
        "winner_probability_auto_cohort_refresh_enabled": (
            settings.winner_probability_auto_cohort_refresh_enabled
        ),
        "winner_cohort_refresh_v2_enabled": settings.winner_cohort_refresh_v2_enabled,
    }


def log_worker_startup_configuration(
    db: Session,
    *,
    settings: Settings,
    worker_id: str,
    instance_id: str | None = None,
    process_start: datetime | None = None,
) -> dict[str, Any]:
    summary = {
        **worker_startup_configuration(db, settings=settings),
        "worker_id": worker_id,
        "worker_instance_id": instance_id,
        "worker_process_id": os.getpid(),
        "worker_process_started_at": (process_start or datetime.now(UTC)).isoformat(),
        "worker_hostname": socket.gethostname(),
        "worker_started_at": datetime.now(UTC).isoformat(),
    }
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
    instance_id: str,
    memory_warning_mb: int,
    memory_critical_mb: int,
    interval_seconds: float,
    session_factory: sessionmaker[Session],
    stop_event: Event,
) -> None:
    process = psutil.Process(process_id)
    process.cpu_percent(None)
    while not stop_event.wait(interval_seconds):
        db = session_factory()
        try:
            snapshot = process_memory_snapshot(process_id)
            state = memory_status(
                snapshot,
                warning_mb=memory_warning_mb,
                critical_mb=memory_critical_mb,
            )
            cpu_percent = max(0.0, process.cpu_percent(None))
            heartbeat_worker(
                db,
                worker_id,
                hostname=hostname,
                process_id=process_id,
                instance_id=instance_id,
                rss_bytes=snapshot.rss_bytes,
                private_bytes=snapshot.private_bytes,
                cpu_percent=cpu_percent,
                memory_status=state,
            )
            operational_metrics.set_gauge("swinglens_worker_up", 1, worker_id=worker_id)
            operational_metrics.set_gauge(
                "swinglens_worker_heartbeat_age_seconds", 0, worker_id=worker_id
            )
            operational_metrics.set_gauge(
                "swinglens_worker_cpu_percent", cpu_percent, worker_id=worker_id
            )
            operational_metrics.set_gauge(
                "swinglens_worker_rss_bytes", snapshot.rss_bytes, worker_id=worker_id
            )
            if snapshot.private_bytes is not None:
                operational_metrics.set_gauge(
                    "swinglens_worker_private_bytes", snapshot.private_bytes, worker_id=worker_id
                )
            for candidate in ("NORMAL", "WARNING", "CRITICAL"):
                operational_metrics.set_gauge(
                    "swinglens_worker_memory_status",
                    float(state == candidate),
                    worker_id=worker_id,
                    status=candidate,
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
    worker_instance_id: str | None = None,
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
    certification_mode: bool = False,
    certification_session_id: str | None = None,
    sec_capability_required: bool = False,
) -> bool:
    if certification_mode:
        from app.services.certification_runtime import require_certification_session_id

        certification_session_id = require_certification_session_id(
            session_id=certification_session_id
        )
    handlers = handlers or default_job_handlers()
    db = session_factory()
    try:
        queue_names = normalize_worker_queues(queues)
        hostname = socket.gethostname()
        process_id = os.getpid()
        abandoned_count = (
            0
            if certification_mode
            else recover_abandoned_jobs_for_worker(
                db,
                authority=RecoveryAuthority.normal("worker.abandoned_recovery"),
                worker_id=worker_id,
                heartbeat_timeout_seconds=heartbeat_timeout_seconds,
            )
        )
        if abandoned_count:
            logger.info(
                "job.restarted_worker_recovered",
                extra={"count": abandoned_count, "worker_id": worker_id},
            )
        register_worker(
            db,
            worker_id=worker_id,
            queues=queue_names,
            heartbeat_timeout_seconds=heartbeat_timeout_seconds,
            hostname=hostname,
            process_id=process_id,
            instance_id=worker_instance_id,
        )
        recovered_count = (
            0
            if certification_mode
            else recover_stale_jobs(
                db,
                stale_after_seconds,
                authority=RecoveryAuthority.normal("worker.stale_recovery"),
            )
        )
        if recovered_count:
            logger.info("job.stale_recovered", extra={"count": recovered_count})
        heartbeat_worker_control_loop(db, worker_id, instance_id=worker_instance_id)
        db.commit()

        if schedule_winner_probability and not certification_mode:
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
        sec_capability = evaluate_sec_processor_capability(db) if sec_capability_required else None
        excluded_job_types = (
            SEC_CAPABILITY_JOB_TYPES
            if sec_capability is not None and not sec_capability.ready
            else ()
        )
        if sec_capability is not None and not sec_capability.ready:
            logger.warning(
                "job.worker.sec_capability_blocked",
                extra={"worker_id": worker_id, "sec_capability": sec_capability.to_dict()},
            )
        job = claim_next_job(
            db,
            worker_id,
            worker_instance_id=worker_instance_id,
            lease_seconds=stale_after_seconds,
            queues=queue_names,
            claim_groups=claim_groups,
            certification_only=certification_mode,
            certification_session_id=certification_session_id,
            excluded_job_types=excluded_job_types,
        )
        if job is None:
            db.commit()
            return False
        claim_state.record(job.job_type)
        logger.info(
            "job.claimed",
            extra={
                "job_id": job.id,
                "job_type": job.job_type,
                "worker_id": worker_id,
                "worker_instance_id": worker_instance_id,
            },
        )

        execution_token = job.execution_token
        job_id = job.id
        control_job = BackgroundJob(
            id=job_id,
            status=JobStatus.RUNNING,
            execution_token=execution_token,
            worker_id=worker_id,
        )
        # Publish the claim before opening an independent control-plane
        # transaction. Otherwise PostgreSQL correctly exposes the prior QUEUED
        # row to that session and the first heartbeat fences its own worker.
        db.commit()
        try:

            def heartbeat() -> None:
                from app.services.domain_write_fence import current_fenced_domain_session

                source_db = current_fenced_domain_session(job.id, execution_token)
                if source_db is not None and (
                    job.job_type == "FULL_PIPELINE"
                    or source_db is not db
                    or source_db.in_nested_transaction()
                ):
                    # A synchronous fenced transaction owns the same row lock.
                    # Renew there whether it is the main Session or a child;
                    # an independent update would wait on this process itself.
                    source_job = (
                        job if source_db is db else source_db.get(BackgroundJob, job.id)
                    )
                    heartbeat_job(
                        source_db,
                        source_job,
                        lease_seconds=stale_after_seconds,
                        execution_token=execution_token,
                    )
                    heartbeat_worker(
                        source_db,
                        worker_id,
                        hostname=hostname,
                        process_id=process_id,
                        instance_id=worker_instance_id,
                    )
                    return
                if job.job_type != "FULL_PIPELINE":
                    # Existing bounded handlers coordinate progress and domain
                    # fencing in their business Session. Preserve that contract;
                    # moving it to a second connection can wait on the handler's
                    # own job-row lock.
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
                        instance_id=worker_instance_id,
                    )
                    # Bounded handlers may open a child Session for their
                    # financial write. Release this control-row lock before
                    # that child acquires the durable ownership fence.
                    db.commit()
                    return
                # Job control is deliberately independent of the business
                # Session. Long read/calculation work cannot defer lease renewal.
                control_db = session_factory() if isinstance(db, Session) else db
                try:
                    heartbeat_job(
                        control_db,
                        control_job,
                        lease_seconds=stale_after_seconds,
                        execution_token=execution_token,
                    )
                    heartbeat_worker(
                        control_db,
                        worker_id,
                        hostname=hostname,
                        process_id=process_id,
                        instance_id=worker_instance_id,
                    )
                    control_db.commit()
                finally:
                    if control_db is not db:
                        control_db.close()

            def detached_control_progress(**progress: Any) -> bool:
                """Commit only lease/progress state on an independent connection."""
                control_db = session_factory()
                try:
                    heartbeat_job(
                        control_db,
                        control_job,
                        lease_seconds=stale_after_seconds,
                        execution_token=execution_token,
                    )
                    if progress:
                        record_job_progress(
                            control_db,
                            job_id=int(job_id),
                            execution_token=str(execution_token),
                            **progress,
                        )
                    requested = is_cancel_requested(control_db, int(job_id))
                    control_db.commit()
                    return requested
                except Exception:
                    control_db.rollback()
                    raise
                finally:
                    control_db.close()

            heartbeat()
            if job.job_type == "CERI_CHANGE_DETECTION":
                job._control_plane_progress = detached_control_progress
            result = execute_job(
                db,
                job,
                handlers,
                heartbeat=heartbeat,
                execution_token=execution_token,
            )
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
        except PipelineBlockedError as exc:
            db.rollback()
            mark_job_blocked(
                db,
                job,
                exc,
                reason_code=exc.reason_code,
                diagnostics=exc.diagnostics,
                execution_token=execution_token,
            )
            logger.warning(
                "job.blocked",
                extra={
                    "job_id": job.id,
                    "job_type": job.job_type,
                    "reason_code": exc.reason_code,
                },
            )
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
    execution_token: str | None = None,
) -> dict[str, Any] | None:
    handler = (handlers or {}).get(job.job_type)
    if handler is None:
        raise ValueError(f"Unsupported job type: {job.job_type}")
    if heartbeat is not None:
        job._heartbeat = heartbeat
    job._execution_token = execution_token if execution_token is not None else job.execution_token
    payload = job.payload_json or {}
    run_id = job.related_run_id or payload.get("run_id")
    workflow_key = job.workflow_key or payload.get("workflow_key")
    ticker = payload.get("ticker")
    company = payload.get("company")
    from app.services.configuration_delivery import (
        configuration_delivery_scope,
        durable_business_job,
        execution_configuration_reference,
        load_configuration_delivery,
    )

    delivery = None
    if isinstance(db, Session) and (
        durable_business_job(job.job_type) or job.job_type == "SEC_READINESS_REPAIR"
    ):
        expected = execution_configuration_reference(db, job)
        delivery = load_configuration_delivery(db, expected, expected_anchor=expected)
    try:
        with (
            configuration_delivery_scope(delivery),
            worker_job_scope(job),
            background_job_scope(
                job_id=job.id,
                job_type=job.job_type,
                run_id=run_id,
                worker_id=job.worker_id,
                workflow_key=workflow_key,
                attempt=int(job.retry_count or 0) + 1,
                ticker=str(ticker) if ticker else None,
                company=str(company) if company else None,
                root_correlation_id=getattr(job, "root_correlation_id", None),
                causation_id=getattr(job, "causation_id", None),
                job_status_getter=lambda: str(job.status) if job.status is not None else None,
            ),
        ):
            with (
                job_phase("job_handler"),
                fence_domain_commits(
                    job_id=job.id,
                    execution_token=execution_token,
                ),
            ):
                return handler(db, job)
    finally:
        if heartbeat is not None and hasattr(job, "_heartbeat"):
            delattr(job, "_heartbeat")
        if hasattr(job, "_control_plane_progress"):
            delattr(job, "_control_plane_progress")


def default_job_handlers() -> dict[str, JobHandler]:
    from app.services.ceri.job_handlers import implemented_ceri_job_handlers
    from app.services.ceri.sec.readiness_repair import SEC_READINESS_REPAIR_JOB_TYPE
    from app.services.ib_fetch_job_service import IB_FETCH_JOB_TYPE, execute_durable_fetch_job
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
        "WORKER_RECOVERY_PROBE": _execute_worker_recovery_probe,
        IB_FETCH_JOB_TYPE: execute_durable_fetch_job,
        SEC_READINESS_REPAIR_JOB_TYPE: _execute_sec_readiness_repair_job,
        MARKET_DATA_PREWARM: execute_market_data_prewarm_job,
        **implemented_ib_intelligence_job_handlers(),
        **implemented_ceri_job_handlers(),
        **implemented_setup_lifecycle_job_handlers(),
        **implemented_winner_job_handlers(),
    }


def _execute_worker_recovery_probe(db: Session, job: BackgroundJob) -> dict[str, Any]:
    """Internal release-gate job for checkpoint/reclaim process recovery tests."""
    from app.services.background_job_service import is_cancel_requested

    payload = job.payload_json or {}
    total = max(1, int(payload.get("total_checkpoints") or 10))
    delay_seconds = max(0.0, float(payload.get("checkpoint_delay_seconds") or 0.1))
    metadata = dict(job.operational_metadata_json or {})
    checkpoint = dict(metadata.get("worker_recovery_probe") or {})
    completed = min(total, int(checkpoint.get("completed") or 0))
    execution_token = str(job.execution_token or "")
    for index in range(completed + 1, total + 1):
        if is_cancel_requested(db, job.id):
            raise CancelRequested("Worker recovery probe cancelled.")
        metadata = dict(job.operational_metadata_json or {})
        metadata["worker_recovery_probe"] = {
            "completed": index,
            "total": total,
            "checkpoint_id": f"worker-recovery-probe:{index}",
            "updated_at": datetime.now(UTC).isoformat(),
        }
        job.operational_metadata_json = metadata
        record_job_progress(
            db,
            job_id=job.id,
            execution_token=execution_token,
            stage="WORKER_RECOVERY_PROBE",
            current_item=str(index),
            last_completed_item=str(index),
            processed=index,
            total=total,
            checkpoint_version=f"worker-recovery-probe:{index}",
            only_if_advanced=True,
        )
        heartbeat = getattr(job, "_heartbeat", None)
        if callable(heartbeat):
            heartbeat()
        time.sleep(delay_seconds)
    return {"completed": total, "resumed_from": completed}


def _execute_full_pipeline_job(db: Session, job: BackgroundJob) -> dict[str, Any] | None:
    from app.services.background_job_service import is_cancel_requested
    from app.services.market_calculation_context_service import (
        validate_pipeline_job_market_context,
    )
    from app.services.pipeline_executor import (
        PipelineCancelled,
        PipelineExecutionDependencies,
        execute_full_pipeline,
    )

    pipeline_run_id = job.payload_json.get("pipeline_run_id")
    if pipeline_run_id is None:
        raise ValueError("FULL_PIPELINE job payload is missing pipeline_run_id.")
    from app.services.scope_refresh_adoption import validate_same_authority

    pipeline = db.get(PipelineRun, int(pipeline_run_id))
    if pipeline is None:
        raise ValueError(f"Pipeline run {pipeline_run_id} was not found.")
    validate_same_authority(pipeline, job)
    market_cutoff = validate_pipeline_job_market_context(
        db,
        pipeline_run_id=int(pipeline_run_id),
        payload=job.payload_json or {},
    )

    execution_token = str(job.execution_token or "")
    control_job = BackgroundJob(
        id=job.id,
        status=JobStatus.RUNNING,
        execution_token=execution_token,
        worker_id=job.worker_id,
    )
    settings = get_settings()
    control_factory = (
        sessionmaker(bind=db.get_bind(), expire_on_commit=False)
        if isinstance(db, Session)
        else None
    )
    stage_boundary_pending = False

    def lease_guard() -> None:
        nonlocal stage_boundary_pending
        if stage_boundary_pending:
            heartbeat_job(
                db,
                control_job,
                lease_seconds=settings.job_stale_after_seconds,
                execution_token=execution_token,
            )
            stage_boundary_pending = False
            return
        heartbeat = getattr(job, "_heartbeat", None)
        if callable(heartbeat):
            heartbeat()

    def fenced_control_session() -> Session | None:
        from app.services.domain_write_fence import current_fenced_domain_session

        source_db = current_fenced_domain_session(job.id, execution_token)
        # Unlike the outer heartbeat, the fallback here is an independent
        # Session. Even when the fenced Session is the pipeline's main Session,
        # it must be preferred or the fallback would wait on that same row lock.
        return source_db

    def should_cancel() -> bool:
        if (
            getattr(pipeline, "current_step", None) != "SCORING_TECHNICALS"
            or control_factory is None
        ):
            lease_guard()
            return is_cancel_requested(db, job.id)
        source_db = fenced_control_session()
        if source_db is not None:
            # The active domain fence already prevents reclaim. A read-only
            # cancellation poll must not acquire a new job-row write lock that
            # could outlive this callback and block the next child writer.
            return is_cancel_requested(source_db, job.id)
        with control_factory() as control_db:
            heartbeat_job(
                control_db,
                control_job,
                lease_seconds=settings.job_stale_after_seconds,
                execution_token=execution_token,
            )
            requested = is_cancel_requested(control_db, job.id)
            control_db.commit()
            return requested

    def progress_callback(progress_db: Session, **progress: Any) -> None:
        nonlocal stage_boundary_pending
        stage = progress.get("stage")
        if (
            progress_db is db
            and progress.get("checkpoint_version") is None
            and progress.get("processed") is None
            and progress.get("total") is None
        ):
            # _pipeline_step calls lease_guard and commits immediately after
            # this stage-boundary update. Keep both writes in that transaction
            # so the guard cannot wait on the row just updated here.
            record_job_progress(
                progress_db,
                job_id=job.id,
                execution_token=execution_token,
                **progress,
            )
            stage_boundary_pending = True
            return
        if stage != "SCORING_TECHNICALS" or control_factory is None:
            # Preserve the established same-session contract for bounded
            # pipeline/downstream stages. _pipeline_step commits these updates
            # immediately. Only long Technical work needs detached control.
            record_job_progress(
                progress_db,
                job_id=job.id,
                execution_token=execution_token,
                **progress,
            )
            return
        source_db = fenced_control_session()
        if source_db is not None:
            source_job = source_db.get(BackgroundJob, job.id)
            heartbeat_job(
                source_db,
                source_job,
                lease_seconds=settings.job_stale_after_seconds,
                execution_token=execution_token,
            )
            record_job_progress(
                source_db,
                job_id=job.id,
                execution_token=execution_token,
                **progress,
            )
            return
        with control_factory() as control_db:
            heartbeat_job(
                control_db,
                control_job,
                lease_seconds=settings.job_stale_after_seconds,
                execution_token=execution_token,
            )
            record_job_progress(
                control_db,
                job_id=job.id,
                execution_token=execution_token,
                **progress,
            )
            control_db.commit()

    def memory_probe(
        item_db: Session,
        item_index: int,
        total_items: int,
        ticker: str,
    ) -> None:
        if item_index % settings.worker_memory_profile_interval_items:
            return
        diagnostics = runtime_memory_diagnostics(
            item_db,
            top_allocation_count=settings.worker_memory_top_allocations,
        )
        state = memory_status(
            process_memory_snapshot(),
            warning_mb=settings.worker_memory_warning_mb,
            critical_mb=settings.worker_memory_critical_mb,
        )
        diagnostics.update(
            item_index=item_index,
            total_items=total_items,
            ticker=ticker,
            memory_status=state,
        )
        logger.info("job.memory_checkpoint %s", diagnostics, extra={"job_id": job.id})
        operational_metrics.set_gauge(
            "swinglens_worker_memory_bytes",
            float(diagnostics["private_bytes"] or diagnostics["rss_bytes"]),
            worker_id=str(job.worker_id or "unknown"),
        )
        if state == "CRITICAL":
            raise WorkerMemoryCritical(
                f"Worker memory reached the critical budget after {ticker}; checkpoint preserved."
            )

    try:
        with job_phase("pipeline_execution"):
            result = execute_full_pipeline(
                db=db,
                pipeline_run_id=int(pipeline_run_id),
                should_cancel=should_cancel,
                lease_guard=lease_guard,
                resume_from_step=job.payload_json.get("resume_from_step"),
                progress_callback=progress_callback,
                memory_probe=memory_probe,
                execution_token=execution_token,
                dependencies=PipelineExecutionDependencies(market_cutoff=market_cutoff),
            )
    except PipelineCancelled as exc:
        raise CancelRequested(str(exc)) from exc
    return result.__dict__


def _execute_sec_readiness_repair_job(
    db: Session,
    job: BackgroundJob,
) -> dict[str, Any] | None:
    from app.services.background_job_service import is_cancel_requested
    from app.services.ceri.sec.readiness_repair import (
        SecReadinessRepairCancelled,
        execute_sec_readiness_repair,
    )

    def should_cancel() -> bool:
        heartbeat = getattr(job, "_heartbeat", None)
        if callable(heartbeat):
            heartbeat()
        return is_cancel_requested(db, job.id)

    try:
        with job_phase("sec_readiness_repair"):
            return execute_sec_readiness_repair(
                db,
                job,
                should_cancel=should_cancel,
            )
    except SecReadinessRepairCancelled as exc:
        raise CancelRequested(str(exc)) from exc
