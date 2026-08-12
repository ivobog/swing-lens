from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriIngestionRun
from app.models.tables import BackgroundJob
from app.services.background_job_service import (
    TERMINAL_JOB_STATUSES,
    JobStatus,
    enqueue_job,
    is_cancel_requested,
)
from app.services.background_worker import CancelRequested, JobDeferred
from app.services.ceri.batched_workflow import (
    CERI_FEATURE_BATCH,
    CERI_NORMALIZE_BATCH,
    CERI_PROVIDER_INGEST_BATCH,
    CERI_RUN_FINALIZE,
)
from app.services.ceri.config import load_ceri_config
from app.services.ceri.enums import CeriDataset
from app.services.ceri.feature_flags import ceri_flags
from app.services.ceri.feature_rebuild_service import (
    CeriFeatureRebuildRequest,
    CeriFeatureRebuildService,
)
from app.services.ceri.normalization_service import (
    CeriNormalizationCancelled,
    CeriNormalizationService,
)
from app.services.ceri.orchestration import (
    CeriIngestionCancelled,
    CeriIngestionRequest,
    CeriIngestionService,
)
from app.services.ceri.processing_run_service import CeriProcessingRunService
from app.services.ceri.provider_registry import CeriProviderRegistry
from app.settings import get_settings

CERI_CAPTURE_RUN = "CERI_CAPTURE_RUN"


def implemented_batched_ceri_job_handlers():
    return {
        CERI_PROVIDER_INGEST_BATCH: execute_provider_ingest_batch_job,
        CERI_NORMALIZE_BATCH: execute_normalize_batch_job,
        CERI_FEATURE_BATCH: execute_feature_batch_job,
        CERI_RUN_FINALIZE: execute_run_finalize_job,
    }


def execute_provider_ingest_batch_job(
    db: Session,
    job: BackgroundJob,
    *,
    ingestion_service: CeriIngestionService | None = None,
) -> dict[str, Any]:
    if not ceri_flags().provider_ingest:
        return _skipped(CERI_PROVIDER_INGEST_BATCH, "provider_ingest_disabled")
    payload = job.payload_json or {}
    workflow_key = _workflow_key(job, payload)
    provider = _required_text(payload, "provider")
    dataset = CeriDataset(_required_text(payload, "dataset"))
    tickers = _tickers(payload)
    checkpoint_interval = _checkpoint_interval(payload)
    service = ingestion_service or CeriIngestionService(
        config=load_ceri_config(),
        registry=CeriProviderRegistry(config=load_ceri_config()),
    )
    completed, results = _checkpoint_state(job)
    failed = 0
    for index, ticker in enumerate(tickers, start=1):
        if ticker in completed:
            continue
        if _heartbeat_and_cancel(db, job, heartbeat=False):
            raise CancelRequested("CERI provider batch cancelled.")
        request_key = f"{workflow_key}:ingest:{provider}:{dataset.value}:{ticker}"
        try:
            result = service.ingest(
                db,
                CeriIngestionRequest(
                    provider=provider,
                    dataset=dataset,
                    ticker=ticker,
                    request_key=request_key,
                    scope={"ticker": ticker, "run_id": job.related_run_id},
                ),
                should_cancel=lambda: _heartbeat_and_cancel(db, job),
            )
            result_values = result.as_dict()
            failed += int(result_values.get("failed") or 0)
            results[ticker] = result_values
        except CeriIngestionCancelled as exc:
            raise CancelRequested(str(exc)) from exc
        except SQLAlchemyError:
            raise
        except Exception as exc:
            failed += 1
            results[ticker] = {
                "status": "PARTIAL",
                "failed": 1,
                "error": _safe_error(exc),
            }
        completed.add(ticker)
        _save_checkpoint(job, completed=completed, results=results)
        if index % checkpoint_interval == 0:
            if _heartbeat_and_cancel(db, job):
                raise CancelRequested("CERI provider batch cancelled.")
    _save_checkpoint(job, completed=completed, results=results)
    if failed:
        job.status = JobStatus.PARTIAL
    return {
        "job_type": CERI_PROVIDER_INGEST_BATCH,
        "status": "PARTIAL" if failed else "COMPLETED",
        "provider": provider,
        "dataset": dataset.value,
        "processed_tickers": len(completed),
        "failed": failed,
        "results": results,
    }


def execute_normalize_batch_job(
    db: Session,
    job: BackgroundJob,
    *,
    normalization_service: CeriNormalizationService | None = None,
) -> dict[str, Any]:
    if not ceri_flags().enabled:
        return _skipped(CERI_NORMALIZE_BATCH, "ceri_disabled")
    payload = job.payload_json or {}
    workflow_key = _workflow_key(job, payload)
    _require_terminal_stage(db, workflow_key, CERI_PROVIDER_INGEST_BATCH)
    provider = _required_text(payload, "provider")
    dataset = CeriDataset(_required_text(payload, "dataset"))
    tickers = _tickers(payload)
    checkpoint_interval = _checkpoint_interval(payload)
    service = normalization_service or CeriNormalizationService()
    config = load_ceri_config()
    completed, results = _checkpoint_state(job)
    failed = 0
    for index, ticker in enumerate(tickers, start=1):
        if ticker in completed:
            continue
        if _heartbeat_and_cancel(db, job, heartbeat=False):
            raise CancelRequested("CERI normalization batch cancelled.")
        ingestion_key = f"{workflow_key}:ingest:{provider}:{dataset.value}:{ticker}"
        ingestion_run = db.scalar(
            select(CeriIngestionRun).where(CeriIngestionRun.request_key == ingestion_key)
        )
        if ingestion_run is None:
            failed += 1
            results[ticker] = {
                "status": "PARTIAL",
                "failed": 1,
                "error": "provider batch produced no ingestion run",
            }
        else:
            processing_key = (
                f"{workflow_key}:normalize-ticker:{provider}:{dataset.value}:{ticker}"
            )
            processing, _ = CeriProcessingRunService().create_or_get(
                db,
                job_type=CERI_NORMALIZE_BATCH,
                request_key=processing_key,
                scope={"ticker": ticker, "run_id": job.related_run_id},
                config_version=config.engine.config_version,
                config_hash=config.config_hash,
                actor=None,
            )
            if processing.status in {"COMPLETED", "PARTIAL"}:
                results[ticker] = {
                    "processing_run_id": processing.id,
                    "status": processing.status,
                    "normalized": processing.normalized_count,
                    "coalesced": True,
                }
            else:
                try:
                    result = service.normalize(
                        db,
                        processing_run=processing,
                        ingestion_run_id=ingestion_run.id,
                        should_cancel=lambda: _heartbeat_and_cancel(
                            db, job, heartbeat=False
                        ),
                        checkpoint_interval=checkpoint_interval,
                        checkpoint_callback=lambda _checkpoint: _heartbeat_and_cancel(
                            db, job
                        ),
                    )
                except CeriNormalizationCancelled as exc:
                    raise CancelRequested(str(exc)) from exc
                results[ticker] = result.as_dict()
            failed += int(results[ticker].get("failed") or 0)
        completed.add(ticker)
        _save_checkpoint(job, completed=completed, results=results)
        if index % checkpoint_interval == 0:
            if _heartbeat_and_cancel(db, job):
                raise CancelRequested("CERI normalization batch cancelled.")
    _save_checkpoint(job, completed=completed, results=results)
    if failed or any(value.get("status") == "PARTIAL" for value in results.values()):
        job.status = JobStatus.PARTIAL
    return {
        "job_type": CERI_NORMALIZE_BATCH,
        "status": "PARTIAL" if job.status == JobStatus.PARTIAL else "COMPLETED",
        "provider": provider,
        "dataset": dataset.value,
        "processed_tickers": len(completed),
        "normalized": sum(int(value.get("normalized") or 0) for value in results.values()),
        "failed": failed,
        "results": results,
    }


def execute_feature_batch_job(
    db: Session,
    job: BackgroundJob,
    *,
    feature_service: CeriFeatureRebuildService | None = None,
) -> dict[str, Any]:
    if not ceri_flags().enabled:
        return _skipped(CERI_FEATURE_BATCH, "ceri_disabled")
    payload = job.payload_json or {}
    workflow_key = _workflow_key(job, payload)
    _require_terminal_stage(
        db,
        workflow_key,
        CERI_NORMALIZE_BATCH,
        expected=int(payload.get("expected_normalization_batches") or 0),
    )
    tickers = _tickers(payload)
    checkpoint_interval = _checkpoint_interval(payload)
    config = load_ceri_config()
    service = feature_service or CeriFeatureRebuildService(config=config)
    completed, results = _checkpoint_state(job)
    failed = 0
    for index, ticker in enumerate(tickers, start=1):
        if ticker in completed:
            continue
        if _heartbeat_and_cancel(db, job, heartbeat=False):
            raise CancelRequested("CERI feature batch cancelled.")
        processing_key = f"{workflow_key}:feature-ticker:{ticker}"
        processing, _ = CeriProcessingRunService().create_or_get(
            db,
            job_type=CERI_FEATURE_BATCH,
            request_key=processing_key,
            scope={"ticker": ticker, "run_id": job.related_run_id},
            config_version=config.engine.config_version,
            config_hash=config.config_hash,
            actor=None,
        )
        if processing.status in {"COMPLETED", "PARTIAL"}:
            values = {
                "processing_run_id": processing.id,
                "status": processing.status,
                "features": processing.feature_count,
                "coalesced": True,
            }
        else:
            result = service.rebuild(
                db,
                CeriFeatureRebuildRequest(
                    ticker=ticker,
                    run_id=int(payload.get("run_id") or job.related_run_id),
                    mode="AS_KNOWN",
                ),
                processing_run=processing,
            )
            CeriProcessingRunService().finish(
                db,
                processing,
                status="PARTIAL" if result.failed else "COMPLETED",
                counts={
                    "features": result.features,
                    "warnings": result.warnings,
                    "failed": result.failed,
                },
                checkpoint={"ticker": ticker, "processed_companies": result.processed_companies},
                errors={"records": list(result.errors)} if result.errors else None,
            )
            values = {
                "processing_run_id": processing.id,
                "status": processing.status,
                **result.as_dict(),
            }
        results[ticker] = values
        failed += int(values.get("failed") or 0)
        completed.add(ticker)
        _save_checkpoint(job, completed=completed, results=results)
        if index % checkpoint_interval == 0:
            if _heartbeat_and_cancel(db, job):
                raise CancelRequested("CERI feature batch cancelled.")
    _save_checkpoint(job, completed=completed, results=results)
    if failed or any(value.get("status") == "PARTIAL" for value in results.values()):
        job.status = JobStatus.PARTIAL
    return {
        "job_type": CERI_FEATURE_BATCH,
        "status": "PARTIAL" if job.status == JobStatus.PARTIAL else "COMPLETED",
        "processed_tickers": len(completed),
        "features": sum(int(value.get("features") or 0) for value in results.values()),
        "failed": failed,
        "results": results,
    }


def execute_run_finalize_job(db: Session, job: BackgroundJob) -> dict[str, Any]:
    if not ceri_flags().enabled:
        return _skipped(CERI_RUN_FINALIZE, "ceri_disabled")
    payload = job.payload_json or {}
    workflow_key = _workflow_key(job, payload)
    _require_terminal_stage(
        db,
        workflow_key,
        CERI_FEATURE_BATCH,
        expected=int(payload.get("expected_feature_batches") or 0),
    )
    run_id = int(payload.get("run_id") or job.related_run_id)
    request_key = f"{workflow_key}:capture"
    capture_job = enqueue_job(
        db,
        CERI_CAPTURE_RUN,
        {
            "workflow_key": workflow_key,
            "request_key": request_key,
            "run_id": run_id,
        },
        related_run_id=run_id,
        priority=max(0, int(job.priority or 140) - 1),
        max_retries=job.max_retries or 3,
        request_key=request_key,
        workflow_key=workflow_key,
    )
    return {
        "job_type": CERI_RUN_FINALIZE,
        "status": "COMPLETED",
        "capture_job_id": capture_job.id,
        "capture_coalesced": bool(getattr(capture_job, "_coalesced", False)),
    }


def _require_terminal_stage(
    db: Session,
    workflow_key: str,
    job_type: str,
    *,
    expected: int = 0,
) -> None:
    jobs = list(
        db.scalars(
            select(BackgroundJob).where(
                BackgroundJob.workflow_key == workflow_key,
                BackgroundJob.job_type == job_type,
            )
        )
    )
    if (expected and len(jobs) != expected) or not jobs:
        raise JobDeferred(
            f"waiting for {job_type} creation",
            delay_seconds=get_settings().ceri_barrier_retry_seconds,
        )
    if any(job.status not in TERMINAL_JOB_STATUSES for job in jobs):
        raise JobDeferred(
            f"waiting for terminal {job_type} batches",
            delay_seconds=get_settings().ceri_barrier_retry_seconds,
        )


def _checkpoint_state(job: BackgroundJob) -> tuple[set[str], dict[str, dict[str, Any]]]:
    state = dict((job.operational_metadata_json or {}).get("ceri_batch") or {})
    completed = {str(ticker).upper() for ticker in state.get("completed_tickers") or []}
    results = {str(key).upper(): dict(value) for key, value in (state.get("results") or {}).items()}
    return completed, results


def _save_checkpoint(
    job: BackgroundJob,
    *,
    completed: set[str],
    results: dict[str, dict[str, Any]],
) -> None:
    metadata = dict(job.operational_metadata_json or {})
    metadata["ceri_batch"] = {
        "completed_tickers": sorted(completed),
        "results": results,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    job.operational_metadata_json = metadata


def _heartbeat_and_cancel(db: Session, job: BackgroundJob, *, heartbeat: bool = True) -> bool:
    callback = getattr(job, "_heartbeat", None)
    if heartbeat and callable(callback):
        callback()
    return bool(job.id and is_cancel_requested(db, job.id))


def _workflow_key(job: BackgroundJob, payload: dict[str, Any]) -> str:
    value = job.workflow_key or payload.get("workflow_key")
    if not value:
        raise ValueError(f"{job.job_type} job is missing workflow_key")
    return str(value)


def _tickers(payload: dict[str, Any]) -> tuple[str, ...]:
    values = tuple(
        sorted(
            {
                str(ticker).strip().upper()
                for ticker in payload.get("tickers") or []
                if str(ticker).strip()
            }
        )
    )
    if not values:
        raise ValueError("CERI batch payload has no tickers")
    return values


def _checkpoint_interval(payload: dict[str, Any]) -> int:
    configured = payload.get("checkpoint_interval")
    return max(
        1,
        int(configured or get_settings().ceri_batch_checkpoint_interval),
    )


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if value in (None, ""):
        raise ValueError(f"CERI batch payload is missing {key}")
    return str(value)


def _safe_error(exc: Exception) -> str:
    return str(exc).replace("\n", " ").strip()[:500] or exc.__class__.__name__


def _skipped(job_type: str, reason: str) -> dict[str, Any]:
    return {"job_type": job_type, "status": "SKIPPED", "reason": reason, "skipped": 1}
