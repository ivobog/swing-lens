from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import BackgroundJob, PipelineRun, PipelineStep
from app.services.background_job_service import fence_job_execution
from app.services.ceri.config import load_ceri_config
from app.services.ceri.enums import CeriDataset
from app.services.ceri.orchestration import CeriIngestionRequest, CeriIngestionService
from app.services.ceri.provider_registry import CeriProviderRegistry
from app.services.ceri.sec.client import SecClientConfig, SecEdgarClient
from app.services.ceri.sec.identity_repair import resolve_and_persist_sec_identity
from app.services.ceri.sec.processor_lifecycle import require_deployed_processor_active
from app.services.ceri.sec.provider import SecCeriProvider
from app.services.ceri.sec.readiness_diagnostics import (
    SecTickerReadinessCategory,
    diagnose_sec_readiness,
)
from app.services.configuration_delivery import pipeline_helper_configuration
from app.services.pipeline_prerequisites import PipelineBlockedError
from app.services.pipeline_service import PipelineStatus, PipelineStepStatus
from app.settings import SecDocumentIncrementalMode, Settings, get_settings

SEC_READINESS_REPAIR_JOB_TYPE = "SEC_READINESS_REPAIR"
SEC_READINESS_REPAIR_PRIORITY = 70
SEC_READINESS_REPAIR_MAX_RETRIES = 5

IDENTITY_REPAIR_CATEGORIES = {
    SecTickerReadinessCategory.UNRESOLVED_MAPPING,
    SecTickerReadinessCategory.CIK_MISSING,
}
BOOTSTRAP_REPAIR_CATEGORIES = {
    SecTickerReadinessCategory.SYNC_STATE_MISSING,
    SecTickerReadinessCategory.SIGNATURE_MISMATCH,
}


class SecReadinessRepairCancelled(RuntimeError):
    pass


class SecReadinessRepairUnresolved(PipelineBlockedError):
    reason_code = "SEC_IDENTITY_UNRESOLVED"


@dataclass
class SecRepairTelemetry:
    documents_discovered: int = 0
    documents_downloaded: int = 0
    documents_cache_reused: int = 0
    documents_skipped: int = 0
    documents_would_skip: int = 0
    sec_requests: int = 0
    filing_downloads: int = 0
    bytes_downloaded: int = 0
    repaired_tickers: set[str] = field(default_factory=set)
    _persisted_sec_requests: int = field(init=False, repr=False)
    _persisted_filing_downloads: int = field(init=False, repr=False)
    _persisted_bytes_downloaded: int = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._persisted_sec_requests = self.sec_requests
        self._persisted_filing_downloads = self.filing_downloads
        self._persisted_bytes_downloaded = self.bytes_downloaded

    @classmethod
    def from_pipeline(cls, pipeline: PipelineRun) -> SecRepairTelemetry:
        stored = dict(((pipeline.result_json or {}).get("sec_repair") or {}).get("telemetry") or {})
        return cls(
            documents_discovered=int(stored.get("documents_discovered") or 0),
            documents_downloaded=int(stored.get("documents_downloaded") or 0),
            documents_cache_reused=int(stored.get("documents_cache_reused") or 0),
            documents_skipped=int(stored.get("documents_skipped") or 0),
            documents_would_skip=int(stored.get("documents_would_skip") or 0),
            sec_requests=int(stored.get("sec_requests") or 0),
            filing_downloads=int(stored.get("filing_downloads") or 0),
            bytes_downloaded=int(stored.get("bytes_downloaded") or 0),
        )

    def add_ingestion(self, ticker: str, result: Any) -> None:
        self.documents_discovered += int(result.documents_discovered or 0)
        self.documents_downloaded += int(result.documents_downloaded or 0)
        self.documents_cache_reused += int(result.documents_cache_reused or 0)
        self.documents_skipped += int(result.documents_skipped or 0)
        self.documents_would_skip += int(result.documents_would_skip or 0)
        if result.run_evidence_status == "READY" and result.status == "COMPLETED":
            self.repaired_tickers.add(ticker)

    def update_client(self, stats: Any, *, baseline: Any) -> None:
        self.sec_requests = self._persisted_sec_requests + max(
            0, int(stats.requests) - int(baseline.requests)
        )
        self.filing_downloads = self._persisted_filing_downloads + max(
            0,
            int(stats.filing_document_requests) - int(baseline.filing_document_requests),
        )
        self.bytes_downloaded = self._persisted_bytes_downloaded + max(
            0, int(stats.bytes_downloaded) - int(baseline.bytes_downloaded)
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "documents_discovered": self.documents_discovered,
            "documents_downloaded": self.documents_downloaded,
            "documents_cache_reused": self.documents_cache_reused,
            "documents_skipped": self.documents_skipped,
            "documents_would_skip": self.documents_would_skip,
            "sec_requests": self.sec_requests,
            "filing_downloads": self.filing_downloads,
            "bytes_downloaded": self.bytes_downloaded,
        }


def schedule_sec_readiness_repair(
    db: Session,
    *,
    pipeline: PipelineRun,
    diagnostics: dict[str, Any],
    resume_from_step: str | None = None,
) -> BackgroundJob:
    from app.services.pipeline_dependency_service import (
        enqueue_sec_readiness_dependency,
        prepare_sec_readiness_dependency,
    )

    target_step = resume_from_step or pipeline.current_step or "VALIDATING_RUN"
    dependency = prepare_sec_readiness_dependency(
        db,
        pipeline=pipeline,
        diagnostics=diagnostics,
        resume_from_step=target_step,
    )
    # Transaction A publishes the wait contract before any asynchronous child
    # exists. A crash after this commit is safely repairable from dependency
    # identity; no parent transaction remains open while the child is queued.
    db.commit()
    job = enqueue_sec_readiness_dependency(db, dependency_id=dependency.id)
    # Transaction B publishes child lineage and returns control to the worker.
    db.commit()
    if job is None:
        from app.services.pipeline_executor import PipelineCancelled

        raise PipelineCancelled("Pipeline cancellation won before SEC child enqueue.")
    return job


@pipeline_helper_configuration
def execute_sec_readiness_repair(
    db: Session,
    job: BackgroundJob,
    *,
    settings: Settings | None = None,
    provider: SecCeriProvider | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    pipeline_id = int((job.payload_json or {}).get("pipeline_run_id") or 0)
    pipeline = db.get(PipelineRun, pipeline_id)
    if pipeline is None:
        raise ValueError(f"Pipeline run {pipeline_id} was not found.")
    from app.services.scope_refresh_adoption import (
        retained_scope_members,
        validate_same_authority,
    )

    authority = validate_same_authority(pipeline, job)
    tickers = [
        member.subject_id
        for member in retained_scope_members(db, authority.scope_id)
        if member.subject_type == "TICKER"
    ]
    lifecycle = require_deployed_processor_active(db)
    signature = str(lifecycle.active_signature or lifecycle.deployed_signature)
    if (job.payload_json or {}).get("processor_signature") != signature:
        raise ValueError("SEC_REPAIR_PROCESSOR_SIGNATURE_MISMATCH")
    from app.services.pipeline_service import existing_sec_repair_continuation

    target = str((job.payload_json or {}).get("resume_from_step") or "VALIDATING_RUN")
    existing = existing_sec_repair_continuation(
        db, pipeline, processor_signature=signature, resume_from_step=target
    )
    if existing is not None:
        repair = (pipeline.result_json or {}).get("sec_repair") or {}
        return {
            "status": "COMPLETED",
            "pipeline_id": pipeline.id,
            "run_id": pipeline.upload_run_id,
            "resume_job_id": existing.id,
            "readiness": {"counts": repair.get("counts") or {}},
            "telemetry": repair.get("telemetry") or {},
        }
    config = load_ceri_config()
    provider = provider or SecCeriProvider(
        client=SecEdgarClient(
            SecClientConfig(
                user_agent=settings.sec_user_agent,
                requests_per_second=settings.sec_requests_per_second,
                timeout_seconds=settings.sec_http_timeout_seconds,
            )
        ),
        guidance_lookback_days=settings.sec_guidance_lookback_days,
        guidance_max_documents_per_ticker=settings.sec_guidance_max_documents_per_ticker,
    )
    telemetry = SecRepairTelemetry.from_pipeline(pipeline)
    client_stats_at_start = provider.client.stats()
    unresolved: dict[str, str] = {}
    transient_failures: list[str] = []
    last_completed_ticker: str | None = None

    _update_progress(
        db,
        pipeline,
        job,
        stage="RESOLVING_IDENTIFIERS",
        readiness=diagnose_sec_readiness(
            db, tickers=tickers, processor_signature=signature
        ).as_dict(),
        telemetry=telemetry,
        unresolved=unresolved,
        started=True,
    )

    initial = diagnose_sec_readiness(db, tickers=tickers, processor_signature=signature)
    current_readiness = initial
    identity_targets = [
        item for item in initial.tickers if item.category in IDENTITY_REPAIR_CATEGORIES
    ]
    for item in identity_targets:
        _guard_cancel(db, pipeline, job, should_cancel)
        _update_progress(
            db,
            pipeline,
            job,
            stage="RESOLVING_IDENTIFIERS",
            readiness=current_readiness.as_dict(),
            telemetry=telemetry,
            unresolved=unresolved,
            current_ticker=item.ticker,
            last_completed_ticker=last_completed_ticker,
        )
        result = resolve_and_persist_sec_identity(db, provider=provider, ticker=item.ticker)
        if not result.resolved:
            unresolved[item.ticker] = result.reason or "SEC identity could not be resolved."
        db.commit()
        current = diagnose_sec_readiness(db, tickers=tickers, processor_signature=signature)
        current_readiness = current
        _update_progress(
            db,
            pipeline,
            job,
            stage="RESOLVING_IDENTIFIERS",
            readiness=current.as_dict(),
            telemetry=telemetry,
            unresolved=unresolved,
            last_completed_ticker=item.ticker,
        )
        last_completed_ticker = item.ticker

    after_identity = diagnose_sec_readiness(db, tickers=tickers, processor_signature=signature)
    for item in after_identity.tickers:
        if item.category in {
            SecTickerReadinessCategory.INVALID_TICKER,
            SecTickerReadinessCategory.UNRESOLVED_MAPPING,
            SecTickerReadinessCategory.CIK_MISSING,
            SecTickerReadinessCategory.OTHER_BLOCKING_REASON,
        }:
            unresolved.setdefault(item.ticker, item.reason or item.category.value)

    repair_settings = settings.model_copy(
        update={"sec_document_incremental_mode": SecDocumentIncrementalMode.SHADOW}
    )
    registry = CeriProviderRegistry(providers={"sec": provider}, config=config)
    ingestion = CeriIngestionService(
        config=config,
        registry=registry,
        settings=repair_settings,
    )
    bootstrap_targets = [
        item.ticker
        for item in after_identity.tickers
        if item.category in BOOTSTRAP_REPAIR_CATEGORIES
    ]
    current_readiness = after_identity
    for ticker in bootstrap_targets:
        _guard_cancel(db, pipeline, job, should_cancel)
        _update_progress(
            db,
            pipeline,
            job,
            stage="PREPARING_SEC_EVIDENCE",
            readiness=current_readiness.as_dict(),
            telemetry=telemetry,
            unresolved=unresolved,
            current_ticker=ticker,
            last_completed_ticker=last_completed_ticker,
        )
        result = ingestion.ingest(
            db,
            CeriIngestionRequest(
                provider="sec",
                dataset=CeriDataset.GUIDANCE,
                ticker=ticker,
                request_key=(
                    f"sec-readiness-repair:{pipeline.id}:{signature}:{ticker}:"
                    f"attempt:{int(job.retry_count or 0)}"
                ),
                scope={
                    "ticker": ticker,
                    "repair": True,
                    "pipeline_id": pipeline.id,
                    "run_id": pipeline.upload_run_id,
                    "worker_id": job.worker_id,
                },
            ),
            should_cancel=should_cancel,
        )
        telemetry.add_ingestion(ticker, result)
        telemetry.update_client(
            provider.client.stats(),
            baseline=client_stats_at_start,
        )
        if result.status != "COMPLETED" or result.run_evidence_status != "READY":
            transient_failures.append(ticker)
        db.commit()
        current = diagnose_sec_readiness(db, tickers=tickers, processor_signature=signature)
        current_readiness = current
        _update_progress(
            db,
            pipeline,
            job,
            stage="PREPARING_SEC_EVIDENCE",
            readiness=current.as_dict(),
            telemetry=telemetry,
            unresolved=unresolved,
            last_completed_ticker=ticker,
        )
        last_completed_ticker = ticker

    client_stats = provider.client.stats()
    telemetry.update_client(client_stats, baseline=client_stats_at_start)
    final = diagnose_sec_readiness(db, tickers=tickers, processor_signature=signature)
    _update_progress(
        db,
        pipeline,
        job,
        stage="RECHECKING_READINESS",
        readiness=final.as_dict(),
        telemetry=telemetry,
        unresolved=unresolved,
        last_completed_ticker=last_completed_ticker,
    )

    if final.complete:
        _guard_cancel(db, pipeline, job, should_cancel)
        fence_job_execution(db, job)
        _update_progress(
            db,
            pipeline,
            job,
            stage="COMPLETED",
            readiness=final.as_dict(),
            telemetry=telemetry,
            unresolved={},
            last_completed_ticker=last_completed_ticker,
        )
        return {
            "status": "COMPLETED",
            "pipeline_id": pipeline.id,
            "run_id": pipeline.upload_run_id,
            "pipeline_dependency_id": job.pipeline_dependency_id,
            "readiness": final.as_dict(include_tickers=False),
            "telemetry": telemetry.as_dict(),
        }

    remaining = {
        item.ticker: unresolved.get(item.ticker) or item.reason or item.category.value
        for item in final.tickers
        if not item.accepted
    }
    identity_remaining = {
        ticker: reason for ticker, reason in remaining.items() if ticker in unresolved
    }
    if identity_remaining:
        message = _unresolved_message(identity_remaining)
        _mark_pipeline_unresolved(
            db,
            pipeline,
            job,
            message=message,
            diagnostics={
                "processor_signature": signature,
                "readiness": final.as_dict(),
                "unresolved_tickers": identity_remaining,
                "telemetry": telemetry.as_dict(),
            },
        )
        raise SecReadinessRepairUnresolved(
            message,
            diagnostics={
                "readiness": final.as_dict(),
                "unresolved_tickers": identity_remaining,
                "telemetry": telemetry.as_dict(),
            },
        )

    root_message = (
        "SEC preparation remains incomplete for "
        f"{len(remaining)} tickers after bounded processing: " + ", ".join(sorted(remaining)[:25])
    )
    _update_progress(
        db,
        pipeline,
        job,
        stage="RETRYING_TRANSIENT_FAILURES",
        readiness=final.as_dict(),
        telemetry=telemetry,
        unresolved=unresolved,
        last_completed_ticker=last_completed_ticker,
        extra={
            "last_error_code": "SEC_REPAIR_INCOMPLETE_TRANSIENT",
            "last_error_detail": root_message,
            "remaining_tickers": [
                {"ticker": ticker, "reason": reason} for ticker, reason in sorted(remaining.items())
            ],
            "next_retry_attempt": int(job.retry_count or 0) + 1,
        },
    )
    if int(job.retry_count or 0) >= int(job.max_retries or 0):
        _mark_pipeline_unresolved(
            db,
            pipeline,
            job,
            message=root_message,
            diagnostics={
                "processor_signature": signature,
                "readiness": final.as_dict(),
                "transient_failures": transient_failures,
                "telemetry": telemetry.as_dict(),
            },
            reason_code="SEC_REPAIR_RETRIES_EXHAUSTED",
        )
    raise RuntimeError(root_message)


def _update_progress(
    db: Session,
    pipeline: PipelineRun,
    job: BackgroundJob,
    *,
    stage: str,
    readiness: dict[str, Any],
    telemetry: SecRepairTelemetry,
    unresolved: dict[str, str],
    started: bool = False,
    current_ticker: str | None = None,
    last_completed_ticker: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    now = _utcnow().isoformat()
    fence_job_execution(db, job)
    existing = dict((job.operational_metadata_json or {}).get("sec_repair") or {})
    ready = int(readiness.get("ready_tickers") or 0)
    initial_ready = int(
        existing.get(
            "initial_ready_tickers",
            ready if started else existing.get("ready_tickers", ready),
        )
    )
    total = int(readiness.get("requested_tickers") or 0)
    progress = {
        **existing,
        "pipeline_id": pipeline.id,
        "run_id": pipeline.upload_run_id,
        "repair_job_id": job.id,
        "repair_stage": stage,
        "total_tickers": total,
        "ready_tickers": ready,
        "initial_ready_tickers": initial_ready,
        "repairable_tickers": max(0, total - initial_ready),
        "repaired_tickers": max(0, ready - initial_ready),
        "unresolved_tickers": [
            {"ticker": ticker, "reason": reason} for ticker, reason in sorted(unresolved.items())
        ],
        "current_processor_signature": readiness.get("processor_signature"),
        "started_at": existing.get("started_at") or (now if started else None),
        "updated_at": now,
        "last_error_code": None,
        "last_error_detail": None,
        "counts": readiness.get("counts") or {},
        "telemetry": telemetry.as_dict(),
        **(extra or {}),
    }
    job.operational_metadata_json = {
        **(job.operational_metadata_json or {}),
        "sec_repair": progress,
    }
    db.flush()
    db.commit()
    control_progress = getattr(job, "_control_plane_progress", None)
    if callable(control_progress):
        if control_progress(
            stage=stage,
            current_item=current_ticker,
            last_completed_item=last_completed_ticker,
            processed=ready,
            total=total,
            checkpoint_version=f"sec-readiness-repair:{stage.lower()}",
        ):
            raise SecReadinessRepairCancelled("SEC readiness repair cancellation requested.")
    else:
        heartbeat = getattr(job, "_heartbeat", None)
        if callable(heartbeat):
            heartbeat()


def sec_repair_failure_view(pipeline, job, failure):
    code = failure["code"]
    ready = (pipeline.result_json or {}).get("sec_repair") or {}
    completed = ready.get("total_tickers") is not None and ready.get("ready_tickers") == ready.get(
        "total_tickers"
    )
    message = (
        "Automatic SEC preparation completed, but pipeline continuation failed."
        if completed
        else "Automatic SEC preparation failed."
    )
    return {
        "status": PipelineStatus.BLOCKED if not failure["retryable"] else PipelineStatus.FAILED,
        "message": message,
        "detail": f"{message} Pipeline {pipeline.id}, job {job.id}: {code}.",
        "code": code,
        "stage": "CONTINUATION_FAILED" if completed else "FAILED",
    }


def mark_sec_repair_failure(db, job, error, failure):
    """Publish terminal helper failures only for the pipeline awaiting this job."""
    if getattr(job, "pipeline_dependency_id", None) is not None:
        # New dependency jobs are reconciled by the pipeline orchestrator only
        # after the worker has committed the child's terminal control state.
        return
    pipeline = db.scalar(
        select(PipelineRun)
        .where(
            PipelineRun.result_json["repair_job_id"].astext == str(job.id),
            PipelineRun.status == PipelineStatus.PREPARING,
        )
        .with_for_update()
    )
    if pipeline is None or (pipeline.result_json or {}).get("repair_job_id") != job.id:
        return
    if pipeline.status != PipelineStatus.PREPARING:
        return
    ready = (pipeline.result_json or {}).get("sec_repair") or {}
    view = sec_repair_failure_view(pipeline, job, failure)
    from app.services.pipeline_state_machine import transition_pipeline, transition_pipeline_step

    transition_pipeline(
        db,
        pipeline,
        view["status"],
        actor="dependency",
        message=view["message"],
        error_message=view["detail"],
    )
    pipeline.result_json = {
        **(pipeline.result_json or {}),
        "blocked_reason": view["code"],
        "sec_repair": {
            **ready,
            "repair_stage": view["stage"],
            "last_error_code": view["code"],
            "last_error_detail": view["detail"],
            "updated_at": _utcnow().isoformat(),
        },
    }
    target = pipeline.current_step or "VALIDATING_RUN"
    step = db.scalar(
        select(PipelineStep).where(
            PipelineStep.pipeline_run_id == pipeline.id, PipelineStep.step_name == target
        )
    )
    if step is not None:
        transition_pipeline_step(
            db,
            step,
            pipeline.status,
            completed_at=pipeline.completed_at,
            message=view["message"],
            error_message=view["detail"],
        )
    db.flush()


def _mark_pipeline_unresolved(
    db: Session,
    pipeline: PipelineRun,
    job: BackgroundJob,
    *,
    message: str,
    diagnostics: dict[str, Any],
    reason_code: str = "SEC_IDENTITY_UNRESOLVED",
) -> None:
    fence_job_execution(db, job)
    repair = dict((job.operational_metadata_json or {}).get("sec_repair") or {})
    repair.update(
        repair_stage="BLOCKED",
        updated_at=_utcnow().isoformat(),
        last_error_code=reason_code,
        last_error_detail=message,
    )
    job.operational_metadata_json = {
        **(job.operational_metadata_json or {}),
        "sec_repair": repair,
        "dependency_failure": {
            "reason_code": reason_code,
            "message": message,
            "diagnostics": diagnostics,
        },
    }
    db.flush()
    db.commit()


def _guard_cancel(
    db: Session,
    pipeline: PipelineRun,
    job: BackgroundJob,
    should_cancel: Callable[[], bool] | None,
) -> None:
    heartbeat = getattr(job, "_heartbeat", None)
    if callable(heartbeat):
        heartbeat()
    if callable(should_cancel) and should_cancel():
        raise SecReadinessRepairCancelled("SEC readiness repair cancelled")


def _validation_step(db: Session, pipeline_id: int) -> PipelineStep | None:
    return db.scalar(
        select(PipelineStep).where(
            PipelineStep.pipeline_run_id == pipeline_id,
            PipelineStep.step_name == "VALIDATING_RUN",
        )
    )


def _reset_pipeline_step(
    db: Session,
    pipeline_id: int,
    step_name: str,
    *,
    message: str,
) -> None:
    step = db.scalar(
        select(PipelineStep).where(
            PipelineStep.pipeline_run_id == pipeline_id,
            PipelineStep.step_name == step_name,
        )
    )
    if step is None:
        return
    step.status = PipelineStepStatus.PENDING
    step.completed_at = None
    step.message = message
    step.error_message = None


def _stage_message(stage: str, ready: int, total: int) -> str:
    labels = {
        "QUEUED": "Preparing run",
        "RESOLVING_IDENTIFIERS": "Resolving SEC identifiers",
        "PREPARING_SEC_EVIDENCE": "Preparing SEC evidence",
        "RECHECKING_READINESS": "Checking SEC preparation",
        "RETRYING_TRANSIENT_FAILURES": "Retrying temporary SEC failures",
        "COMPLETED": "SEC preparation completed",
    }
    return f"{labels.get(stage, 'Preparing run')}: {ready} / {total} ready."


def _unresolved_message(unresolved: dict[str, str]) -> str:
    details = "; ".join(
        f"{ticker} — {reason}" for ticker, reason in sorted(unresolved.items())[:25]
    )
    suffix = "; additional unresolved tickers omitted" if len(unresolved) > 25 else ""
    return (
        f"Run cannot continue: SEC identity could not be resolved for "
        f"{len(unresolved)} tickers: {details}{suffix}."
    )


def _utcnow() -> datetime:
    return datetime.now(UTC)
