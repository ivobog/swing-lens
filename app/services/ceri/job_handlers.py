from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriProcessingRun
from app.models.tables import BackgroundJob
from app.services.background_job_service import JobStatus, enqueue_job, is_cancel_requested
from app.services.background_worker import CancelRequested
from app.services.ceri.config import load_ceri_config
from app.services.ceri.enums import CeriDataset
from app.services.ceri.normalization_service import CeriNormalizationService
from app.services.ceri.orchestration import (
    CeriIngestionCancelled,
    CeriIngestionRequest,
    CeriIngestionService,
)
from app.services.ceri.provider_registry import CeriProviderRegistry
from app.services.ceri.providers.manual_provider import ManualCeriProvider

CERI_PROVIDER_INGEST = "CERI_PROVIDER_INGEST"
CERI_NORMALIZE = "CERI_NORMALIZE"

CeriJobHandler = Callable[[Session, BackgroundJob], dict[str, Any] | None]


def implemented_ceri_job_handlers() -> dict[str, CeriJobHandler]:
    return {
        CERI_PROVIDER_INGEST: execute_provider_ingest_job,
        CERI_NORMALIZE: execute_normalize_job,
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


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _maybe_scalar(db: Session, statement):
    scalar = getattr(db, "scalar", None)
    if callable(scalar):
        return scalar(statement)
    return None


def _utcnow() -> datetime:
    return datetime.now(UTC)
