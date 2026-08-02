from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriProcessingRun
from app.models.tables import BackgroundJob
from app.services.background_job_service import JobStatus, enqueue_job, is_cancel_requested
from app.services.background_worker import CancelRequested
from app.services.ceri.alert_service import CeriAlertService
from app.services.ceri.backfill_service import CeriBackfillRequest, CeriBackfillService
from app.services.ceri.capture_service import CeriRunCaptureService
from app.services.ceri.config import load_ceri_config
from app.services.ceri.enums import CeriDataset
from app.services.ceri.normalization_service import CeriNormalizationService
from app.services.ceri.orchestration import (
    CeriIngestionCancelled,
    CeriIngestionRequest,
    CeriIngestionService,
)
from app.services.ceri.processing_run_service import CeriProcessingRunService
from app.services.ceri.provider_registry import CeriProviderRegistry
from app.services.ceri.providers.manual_provider import ManualCeriProvider
from app.services.ceri.purge_service import (
    CeriPurgeExecuteRequest,
    CeriPurgePreviewRequest,
    CeriPurgeService,
)

CERI_PROVIDER_INGEST = "CERI_PROVIDER_INGEST"
CERI_NORMALIZE = "CERI_NORMALIZE"
CERI_REBUILD_FEATURES = "CERI_REBUILD_FEATURES"
CERI_CAPTURE_RUN = "CERI_CAPTURE_RUN"
CERI_CHANGE_DETECTION = "CERI_CHANGE_DETECTION"
CERI_BACKFILL = "CERI_BACKFILL"
CERI_ALERT_REBUILD = "CERI_ALERT_REBUILD"
CERI_PURGE_LICENSED_DATA = "CERI_PURGE_LICENSED_DATA"

CeriJobHandler = Callable[[Session, BackgroundJob], dict[str, Any] | None]


def implemented_ceri_job_handlers() -> dict[str, CeriJobHandler]:
    return {
        CERI_PROVIDER_INGEST: execute_provider_ingest_job,
        CERI_NORMALIZE: execute_normalize_job,
        CERI_REBUILD_FEATURES: execute_rebuild_features_job,
        CERI_CAPTURE_RUN: execute_capture_run_job,
        CERI_CHANGE_DETECTION: execute_change_detection_job,
        CERI_BACKFILL: execute_backfill_job,
        CERI_ALERT_REBUILD: execute_alert_rebuild_job,
        CERI_PURGE_LICENSED_DATA: execute_purge_licensed_data_job,
    }


def execute_provider_ingest_job(
    db: Session,
    job: BackgroundJob,
    *,
    ingestion_service: CeriIngestionService | None = None,
) -> dict[str, Any]:
    payload = job.payload_json or {}
    dataset = _dataset(payload)
    provider = str(payload.get("provider") or "manual")
    ticker = _required_text(payload, "ticker")

    if ingestion_service is None:
        ingestion_service = _ingestion_service_from_payload(payload, dataset)

    try:
        result = ingestion_service.ingest(
            db,
            CeriIngestionRequest(
                provider=provider,
                dataset=dataset,
                ticker=ticker,
                request_key=payload.get("request_key"),
                scope=payload.get("scope") or {"ticker": ticker},
            ),
            should_cancel=lambda: _heartbeat_and_check_cancel(db, job),
        )
    except CeriIngestionCancelled as exc:
        raise CancelRequested(str(exc)) from exc

    values = result.as_dict()
    normalize_job_id = _enqueue_normalize_job(
        db,
        source_job=job,
        source_payload=payload,
        ingestion_request_key=payload.get("request_key")
        or ingestion_service.request_key(
            CeriIngestionRequest(provider=provider, dataset=dataset, ticker=ticker)
        ),
        ingestion_result=values,
    )
    if normalize_job_id is not None:
        values["normalize_job_id"] = normalize_job_id
    if values.get("status") in {"PARTIAL", "CANCELLED"} or values.get("failed", 0):
        job.status = JobStatus.PARTIAL
    return {"job_type": CERI_PROVIDER_INGEST, **values}


def execute_normalize_job(
    db: Session,
    job: BackgroundJob,
    *,
    normalization_service: CeriNormalizationService | None = None,
) -> dict[str, Any]:
    payload = job.payload_json or {}
    request_key = str(payload.get("request_key") or f"ceri:normalize:{job.id}")
    existing = _maybe_scalar(
        db,
        select(CeriProcessingRun).where(
            CeriProcessingRun.deterministic_request_key == request_key
        ),
    )
    if existing is not None:
        return {
            "job_type": CERI_NORMALIZE,
            "processing_run_id": existing.id,
            "status": existing.status,
            "normalized": existing.normalized_count,
            "coalesced": True,
        }

    started_at = _utcnow()
    processing_run = CeriProcessingRun(
        job_type=CERI_NORMALIZE,
        status="RUNNING",
        deterministic_request_key=request_key,
        scope_json=payload.get("scope") or {},
        config_version=payload.get("config_version"),
        config_hash=payload.get("config_hash"),
        actor=payload.get("actor"),
        checkpoint_json={"phase": "phase_4_normalization_started"},
        started_at=started_at,
    )
    db.add(processing_run)
    db.flush()
    normalization_service = normalization_service or CeriNormalizationService()
    result = normalization_service.normalize(
        db,
        processing_run=processing_run,
        ingestion_run_id=_optional_int(payload.get("ingestion_run_id")),
    )
    return {"job_type": CERI_NORMALIZE, **result.as_dict()}


def execute_rebuild_features_job(db: Session, job: BackgroundJob) -> dict[str, Any]:
    return _finish_processing_job(db, job, CERI_REBUILD_FEATURES, feature_count=0)


def execute_capture_run_job(
    db: Session,
    job: BackgroundJob,
    *,
    capture_service: CeriRunCaptureService | None = None,
) -> dict[str, Any]:
    payload = job.payload_json or {}
    run_id = _required_int(payload, "run_id")
    processing, created = _processing_run(
        db,
        CERI_CAPTURE_RUN,
        payload,
        default_request_key=f"ceri:capture-run:{run_id}",
    )
    if not created and processing.status == "COMPLETED":
        return {
            "job_type": CERI_CAPTURE_RUN,
            "processing_run_id": processing.id,
            "status": processing.status,
            "coalesced": True,
        }
    result = (capture_service or CeriRunCaptureService()).capture_run(db, run_id)
    values = result.as_dict()
    CeriProcessingRunService().finish(
        db,
        processing,
        status="PARTIAL" if values.get("failed") else "COMPLETED",
        counts={
            "score_snapshots": values.get("score_snapshots", 0),
            "change_events": values.get("change_events", 0),
            "alerts": values.get("alerts", 0),
            "failed": values.get("failed", 0),
            "warnings": values.get("stale", 0) + values.get("conflicted", 0),
        },
        checkpoint={"run_id": run_id},
    )
    return {"job_type": CERI_CAPTURE_RUN, "processing_run_id": processing.id, **values}


def execute_change_detection_job(db: Session, job: BackgroundJob) -> dict[str, Any]:
    return _finish_processing_job(db, job, CERI_CHANGE_DETECTION, change_count=0)


def execute_backfill_job(
    db: Session,
    job: BackgroundJob,
    *,
    backfill_service: CeriBackfillService | None = None,
) -> dict[str, Any]:
    payload = job.payload_json or {}
    result = (backfill_service or CeriBackfillService()).run(
        db,
        CeriBackfillRequest(
            provider=str(payload.get("provider") or "manual"),
            dataset=str(payload.get("dataset") or "estimates"),
            ticker=payload.get("ticker"),
            start=_optional_date(payload.get("start")),
            end=_optional_date(payload.get("end")),
            mode=str(payload.get("mode") or "AS_KNOWN"),
            actor=payload.get("actor"),
        ),
    )
    return {"job_type": CERI_BACKFILL, **result.as_dict()}


def execute_alert_rebuild_job(db: Session, job: BackgroundJob) -> dict[str, Any]:
    payload = job.payload_json or {}
    processing, created = _processing_run(
        db,
        CERI_ALERT_REBUILD,
        payload,
        default_request_key=f"ceri:alert-rebuild:{job.id}",
    )
    if not created and processing.status == "COMPLETED":
        return {
            "job_type": CERI_ALERT_REBUILD,
            "status": processing.status,
            "processing_run_id": processing.id,
            "coalesced": True,
        }
    result = CeriAlertService(alerts_enabled=bool(payload.get("alerts_enabled"))).rebuild_alerts(
        db,
        changes=[],
    )
    CeriProcessingRunService().finish(
        db,
        processing,
        status="COMPLETED",
        counts={
            "alerts": result.alerts,
            "warnings": 0,
            "failed": 0,
        },
        checkpoint={
            "duplicates": result.duplicates,
            "skipped": result.skipped,
        },
    )
    return {
        "job_type": CERI_ALERT_REBUILD,
        "processing_run_id": processing.id,
        "status": processing.status,
        **result.as_dict(),
    }


def execute_purge_licensed_data_job(db: Session, job: BackgroundJob) -> dict[str, Any]:
    payload = job.payload_json or {}
    preview_hash = str(payload.get("preview_manifest_hash") or f"preview:{job.id}")
    execute = bool(payload.get("execute"))
    processing, created = _processing_run(
        db,
        CERI_PURGE_LICENSED_DATA,
        payload,
        default_request_key=f"ceri:purge:{preview_hash}",
    )
    if not created and processing.status == "COMPLETED":
        return {
            "job_type": CERI_PURGE_LICENSED_DATA,
            "status": processing.status,
            "processing_run_id": processing.id,
            "coalesced": True,
        }
    service = CeriPurgeService()
    if execute:
        audit = service.execute(
            db,
            CeriPurgeExecuteRequest(
                provider=str(payload.get("provider") or "manual"),
                license_scope=str(payload.get("license_scope") or "manual"),
                actor=str(payload.get("actor") or "system"),
                reason=str(payload.get("reason") or "licensed data purge execution"),
                confirmation_token=str(payload.get("confirmation_token") or ""),
                preview_manifest_hash=preview_hash,
            ),
            job_id=job.id,
            processing_run_id=processing.id,
        )
    else:
        audit = service.preview(
            db,
            CeriPurgePreviewRequest(
                provider=str(payload.get("provider") or "manual"),
                license_scope=str(payload.get("license_scope") or "manual"),
                actor=str(payload.get("actor") or "system"),
                reason=str(payload.get("reason") or "licensed data purge preview"),
                preview_manifest_hash=preview_hash,
            ),
            job_id=job.id,
            processing_run_id=processing.id,
        )
    affected_counts = audit.affected_counts_json or {}
    CeriProcessingRunService().finish(
        db,
        processing,
        status="COMPLETED",
        counts={
            "read": int(affected_counts.get("source_records") or 0),
            "warnings": 0,
            "failed": 0,
        },
        checkpoint={
            "purge_audit_id": audit.id,
            "preview_manifest_hash": audit.preview_manifest_hash,
            "purge_status": audit.status,
        },
    )
    return {
        "job_type": CERI_PURGE_LICENSED_DATA,
        "processing_run_id": processing.id,
        "purge_audit_id": audit.id,
        "status": audit.status,
    }


def _ingestion_service_from_payload(
    payload: dict[str, Any],
    dataset: CeriDataset,
) -> CeriIngestionService:
    config = load_ceri_config()
    providers = None
    manual_path = payload.get("manual_path")
    if manual_path:
        providers = {
            "manual": ManualCeriProvider.from_path(
                Path(str(manual_path)),
                dataset=dataset,
                provider_terms_version=config.retention.provider_terms_version,
            )
        }
    return CeriIngestionService(
        config=config,
        registry=CeriProviderRegistry(providers=providers, config=config),
    )


def _finish_processing_job(
    db: Session,
    job: BackgroundJob,
    job_type: str,
    *,
    feature_count: int = 0,
    change_count: int = 0,
) -> dict[str, Any]:
    payload = job.payload_json or {}
    run, created = _processing_run(
        db,
        job_type,
        payload,
        default_request_key=f"ceri:{job_type.lower()}:{job.id}",
    )
    if not created and run.status == "COMPLETED":
        return {
            "job_type": job_type,
            "processing_run_id": run.id,
            "status": run.status,
            "coalesced": True,
        }
    CeriProcessingRunService().finish(
        db,
        run,
        status="COMPLETED",
        counts={"features": feature_count, "change_events": change_count},
        checkpoint={"phase": job_type.lower()},
    )
    return {"job_type": job_type, "processing_run_id": run.id, "status": run.status}


def _processing_run(
    db: Session,
    job_type: str,
    payload: dict[str, Any],
    *,
    default_request_key: str,
) -> tuple[CeriProcessingRun, bool]:
    config = load_ceri_config()
    return CeriProcessingRunService().create_or_get(
        db,
        job_type=job_type,
        request_key=str(payload.get("request_key") or default_request_key),
        scope=payload.get("scope") or payload,
        config_version=str(payload.get("config_version") or config.engine.config_version),
        config_hash=str(payload.get("config_hash") or config.config_hash),
        actor=payload.get("actor"),
    )


def _enqueue_normalize_job(
    db: Session,
    *,
    source_job: BackgroundJob,
    source_payload: dict[str, Any],
    ingestion_request_key: str,
    ingestion_result: dict[str, Any],
) -> int | None:
    if not ingestion_result.get("ingestion_run_id"):
        return None
    if not any(
        ingestion_result.get(key, 0)
        for key in ("inserted", "deduplicated", "corrected", "quarantined")
    ):
        return None

    request_key = f"ceri:normalize:{ingestion_request_key}"
    existing = _maybe_scalar(
        db,
        select(BackgroundJob)
        .where(BackgroundJob.job_type == CERI_NORMALIZE)
        .where(BackgroundJob.payload_json["request_key"].astext == request_key),
    )
    if existing is not None:
        return existing.id

    job = enqueue_job(
        db,
        CERI_NORMALIZE,
        {
            "request_key": request_key,
            "ingestion_run_id": ingestion_result["ingestion_run_id"],
            "provider": ingestion_result["provider"],
            "dataset": ingestion_result["dataset"],
            "scope": source_payload.get("scope") or {"ticker": source_payload.get("ticker")},
            "config_version": source_payload.get("config_version"),
            "config_hash": source_payload.get("config_hash"),
            "actor": source_payload.get("actor"),
        },
        related_run_id=source_job.related_run_id,
        priority=(source_job.priority or 100) + 10,
        max_retries=source_job.max_retries or 3,
    )
    return job.id


def _heartbeat_and_check_cancel(db: Session, job: BackgroundJob) -> bool:
    heartbeat = getattr(job, "_heartbeat", None)
    if callable(heartbeat):
        heartbeat()
    return is_cancel_requested(db, job.id)


def _dataset(payload: dict[str, Any]) -> CeriDataset:
    value = payload.get("dataset")
    if value is None:
        raise ValueError(f"{CERI_PROVIDER_INGEST} job payload is missing dataset.")
    try:
        return CeriDataset(str(value))
    except ValueError as exc:
        raise ValueError(f"Unsupported CERI dataset: {value}") from exc


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not value:
        raise ValueError(f"{CERI_PROVIDER_INGEST} job payload is missing {key}.")
    return str(value)


def _required_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if value in (None, ""):
        raise ValueError(f"job payload is missing {key}.")
    return int(value)


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _optional_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _maybe_scalar(db: Session, statement):
    scalar = getattr(db, "scalar", None)
    if callable(scalar):
        return scalar(statement)
    return None


def _utcnow() -> datetime:
    return datetime.now(UTC)
