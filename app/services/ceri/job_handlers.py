from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.ceri_tables import (
    CeriChangeEvent,
    CeriCompany,
    CeriProcessingRun,
    CeriScoreSnapshot,
)
from app.models.tables import BackgroundJob
from app.observability.db_monitor import job_phase
from app.services.background_job_service import JobStatus, enqueue_job, is_cancel_requested
from app.services.background_worker import CancelRequested
from app.services.ceri.alert_service import CeriAlertService
from app.services.ceri.backfill_service import CeriBackfillRequest, CeriBackfillService
from app.services.ceri.capture_service import CeriRunCaptureService
from app.services.ceri.change_rebuild_service import (
    CeriChangeRebuildRequest,
    CeriChangeRebuildService,
)
from app.services.ceri.config import load_ceri_config
from app.services.ceri.enums import CeriDataset
from app.services.ceri.feature_flags import ceri_flags, parse_explicit_bool
from app.services.ceri.feature_rebuild_service import (
    CeriFeatureRebuildRequest,
    CeriFeatureRebuildService,
)
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
    from app.services.ceri.batched_job_handlers import implemented_batched_ceri_job_handlers

    return {
        CERI_PROVIDER_INGEST: execute_provider_ingest_job,
        CERI_NORMALIZE: execute_normalize_job,
        CERI_REBUILD_FEATURES: execute_rebuild_features_job,
        CERI_CAPTURE_RUN: execute_capture_run_job,
        CERI_CHANGE_DETECTION: execute_change_detection_job,
        CERI_BACKFILL: execute_backfill_job,
        CERI_ALERT_REBUILD: execute_alert_rebuild_job,
        CERI_PURGE_LICENSED_DATA: execute_purge_licensed_data_job,
        **implemented_batched_ceri_job_handlers(),
    }


def execute_provider_ingest_job(
    db: Session,
    job: BackgroundJob,
    *,
    ingestion_service: CeriIngestionService | None = None,
) -> dict[str, Any]:
    if not ceri_flags().provider_ingest:
        return _skipped_job(CERI_PROVIDER_INGEST, "provider_ingest_disabled")
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
    except SQLAlchemyError:
        raise
    except Exception as exc:
        job.status = JobStatus.PARTIAL
        return {
            "job_type": CERI_PROVIDER_INGEST,
            "status": "PARTIAL",
            "provider": provider,
            "dataset": dataset.value,
            "ticker": ticker,
            "failed": 1,
            "error": _safe_job_error(exc),
        }

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
    if not ceri_flags().enabled:
        return _skipped_job(CERI_NORMALIZE, "ceri_disabled")
    payload = job.payload_json or {}
    request_key = str(payload.get("request_key") or f"ceri:normalize:{job.id}")
    existing = _maybe_scalar(
        db,
        select(CeriProcessingRun).where(CeriProcessingRun.deterministic_request_key == request_key),
    )
    if existing is not None:
        values = {
            "job_type": CERI_NORMALIZE,
            "processing_run_id": existing.id,
            "status": existing.status,
            "normalized": existing.normalized_count,
            "coalesced": True,
        }
        feature_job_id = _enqueue_feature_rebuild_after_normalize(
            db,
            source_job=job,
            source_payload=payload,
            normalize_result=values,
        )
        if feature_job_id is not None:
            values["feature_job_id"] = feature_job_id
        return values

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
    values = {"job_type": CERI_NORMALIZE, **result.as_dict()}
    feature_job_id = _enqueue_feature_rebuild_after_normalize(
        db, source_job=job, source_payload=payload, normalize_result=values
    )
    if feature_job_id is not None:
        values["feature_job_id"] = feature_job_id
    return values


def execute_rebuild_features_job(
    db: Session,
    job: BackgroundJob,
    *,
    feature_service: CeriFeatureRebuildService | None = None,
) -> dict[str, Any]:
    if not ceri_flags().enabled:
        return _skipped_job(CERI_REBUILD_FEATURES, "ceri_disabled")
    payload = job.payload_json or {}
    processing, created = _processing_run(
        db,
        CERI_REBUILD_FEATURES,
        payload,
        default_request_key=f"ceri:feature-rebuild:{_scope_key(payload, job.id)}",
    )
    if not created and processing.status == "COMPLETED":
        values = {
            "job_type": CERI_REBUILD_FEATURES,
            "processing_run_id": processing.id,
            "status": processing.status,
            "coalesced": True,
        }
        capture_job_id = (
            _enqueue_capture_after_features(db, job=job, payload=payload)
            if processing.status == "COMPLETED"
            else None
        )
        if capture_job_id is not None:
            values["capture_job_id"] = capture_job_id
        return values
    result = (feature_service or CeriFeatureRebuildService()).rebuild(
        db,
        CeriFeatureRebuildRequest(
            company_ids=_optional_int_tuple(payload.get("company_ids")),
            ticker=payload.get("ticker"),
            as_of_session=_optional_date(payload.get("as_of_session")),
            from_session=_optional_date(payload.get("from_session")),
            to_session=_optional_date(payload.get("to_session")),
            run_id=_optional_int(payload.get("run_id")),
            mode=str(payload.get("mode") or "AS_KNOWN"),
        ),
        processing_run=processing,
    )
    CeriProcessingRunService().finish(
        db,
        processing,
        status="PARTIAL" if result.failed else "COMPLETED",
        counts={"features": result.features, "warnings": result.warnings, "failed": result.failed},
        checkpoint={
            "scope": payload.get("scope") or payload,
            "processed_companies": result.processed_companies,
        },
        errors={"records": list(result.errors)} if result.errors else None,
    )
    values = {
        "job_type": CERI_REBUILD_FEATURES,
        "processing_run_id": processing.id,
        "status": processing.status,
        **result.as_dict(),
    }
    capture_job_id = (
        _enqueue_capture_after_features(db, job=job, payload=payload)
        if processing.status == "COMPLETED"
        else None
    )
    if capture_job_id is not None:
        values["capture_job_id"] = capture_job_id
    return values


def execute_capture_run_job(
    db: Session,
    job: BackgroundJob,
    *,
    capture_service: CeriRunCaptureService | None = None,
) -> dict[str, Any]:
    if not ceri_flags().run_capture:
        return _skipped_job(CERI_CAPTURE_RUN, "run_capture_disabled")
    payload = job.payload_json or {}
    run_id = _required_int(payload, "run_id")
    processing, created = _processing_run(
        db,
        CERI_CAPTURE_RUN,
        payload,
        default_request_key=f"ceri:capture-run:{run_id}",
    )
    if not created and processing.status == "COMPLETED":
        values = {
            "job_type": CERI_CAPTURE_RUN,
            "processing_run_id": processing.id,
            "status": processing.status,
            "coalesced": True,
        }
        change_job_id = _enqueue_change_after_capture(
            db,
            job=job,
            run_id=run_id,
        )
        if change_job_id is not None:
            values["change_job_id"] = change_job_id
        return values
    with job_phase("capture_calculation_and_persistence"):
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
    change_job_id = (
        _enqueue_change_after_capture(db, job=job, run_id=run_id)
        if processing.status == "COMPLETED" or bool(job.workflow_key)
        else None
    )
    if change_job_id is not None:
        values["change_job_id"] = change_job_id
    return {"job_type": CERI_CAPTURE_RUN, "processing_run_id": processing.id, **values}


def execute_change_detection_job(
    db: Session,
    job: BackgroundJob,
    *,
    change_service: CeriChangeRebuildService | None = None,
) -> dict[str, Any]:
    if not ceri_flags().enabled:
        return _skipped_job(CERI_CHANGE_DETECTION, "ceri_disabled")
    payload = job.payload_json or {}
    processing, created = _processing_run(
        db,
        CERI_CHANGE_DETECTION,
        payload,
        default_request_key=f"ceri:change-rebuild:{_scope_key(payload, job.id)}",
    )
    if not created and processing.status == "COMPLETED":
        change_ids = tuple(
            int(value) for value in (processing.checkpoint_json or {}).get("change_ids", [])
        )
        values = {
            "job_type": CERI_CHANGE_DETECTION,
            "processing_run_id": processing.id,
            "status": processing.status,
            "coalesced": True,
            "change_ids": list(change_ids),
        }
        alert_job_id = _enqueue_alert_after_change(
            db,
            job=job,
            payload=payload,
            change_ids=change_ids,
        )
        if alert_job_id is not None:
            values["alert_job_id"] = alert_job_id
        return values
    with job_phase("change_calculation_and_persistence"):
        result = (change_service or CeriChangeRebuildService()).rebuild(
            db,
            CeriChangeRebuildRequest(
                company_ids=_optional_int_tuple(payload.get("company_ids")),
                ticker=payload.get("ticker"),
                run_id=_optional_int(payload.get("run_id")),
                from_session=_optional_date(payload.get("from_session")),
                to_session=_optional_date(payload.get("to_session")),
                changed_since=_optional_datetime(payload.get("changed_since")),
            ),
        )
    raw_change_ids = getattr(result, "change_ids", None)
    change_ids = tuple(int(value) for value in (raw_change_ids or ()))
    CeriProcessingRunService().finish(
        db,
        processing,
        status="PARTIAL" if result.failed else "COMPLETED",
        counts={
            "change_events": result.changes,
            "warnings": result.warnings,
            "failed": result.failed,
        },
        checkpoint={
            "scope": payload.get("scope") or payload,
            "change_count": result.changes,
            "change_ids": list(change_ids),
        },
        errors={"records": list(result.errors)} if result.errors else None,
    )
    values = {
        "job_type": CERI_CHANGE_DETECTION,
        "processing_run_id": processing.id,
        "status": processing.status,
        **result.as_dict(),
    }
    alert_job_id = (
        _enqueue_alert_after_change(
            db,
            job=job,
            payload=payload,
            change_ids=change_ids,
        )
        if (change_ids or (raw_change_ids is None and result.changes) or job.workflow_key)
        and processing.status == "COMPLETED"
        else None
    )
    if alert_job_id is not None:
        values["alert_job_id"] = alert_job_id
    return values


def execute_backfill_job(
    db: Session,
    job: BackgroundJob,
    *,
    backfill_service: CeriBackfillService | None = None,
) -> dict[str, Any]:
    if not ceri_flags().backfill:
        return _skipped_job(CERI_BACKFILL, "backfill_disabled")
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
            tickers=tuple(
                str(ticker) for ticker in payload.get("tickers", []) if str(ticker).strip()
            ),
        ),
    )
    if result.status == "PARTIAL":
        job.status = JobStatus.PARTIAL
    return {"job_type": CERI_BACKFILL, **result.as_dict()}


def execute_alert_rebuild_job(db: Session, job: BackgroundJob) -> dict[str, Any]:
    if not ceri_flags().alerts:
        return _skipped_job(CERI_ALERT_REBUILD, "alerts_disabled")
    payload = job.payload_json or {}
    processing, created = _processing_run(
        db,
        CERI_ALERT_REBUILD,
        payload,
        default_request_key=f"ceri:alert-rebuild:{_scope_key(payload, job.id)}",
    )
    if not created and processing.status == "COMPLETED":
        return {
            "job_type": CERI_ALERT_REBUILD,
            "status": processing.status,
            "processing_run_id": processing.id,
            "coalesced": True,
        }
    changes = _eligible_changes(db, payload)
    ticker_by_company = {
        company.id: company.ticker
        for company in _load_rows(db, CeriCompany)
        if company.id in {change.company_id for change in changes}
    }
    alerts_enabled = parse_explicit_bool(payload.get("alerts_enabled"), default=ceri_flags().alerts)
    result = CeriAlertService(alerts_enabled=bool(alerts_enabled)).rebuild_alerts(
        db,
        changes=changes,
        ticker_by_company=ticker_by_company,
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
            "eligible_change_count": len(changes),
            "alerts_enabled": bool(alerts_enabled),
        },
    )
    return {
        "job_type": CERI_ALERT_REBUILD,
        "processing_run_id": processing.id,
        "status": processing.status,
        "alerts_status": "REBUILT" if alerts_enabled else "SKIPPED_DISABLED",
        **result.as_dict(),
    }


def execute_purge_licensed_data_job(db: Session, job: BackgroundJob) -> dict[str, Any]:
    if not ceri_flags().admin:
        return _skipped_job(CERI_PURGE_LICENSED_DATA, "admin_disabled")
    payload = job.payload_json or {}
    execute = parse_explicit_bool(payload.get("execute"), default=False)
    preview_hash = str(payload.get("preview_manifest_hash") or "")
    processing_key = preview_hash or f"scope:{_scope_key(payload, job.id)}"
    processing, created = _processing_run(
        db,
        CERI_PURGE_LICENSED_DATA,
        payload,
        default_request_key=f"ceri:purge:{processing_key}",
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
                preview_manifest_hash=preview_hash or None,
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


def _eligible_changes(db: Session, payload: dict[str, Any]) -> list[CeriChangeEvent]:
    changes = _load_rows(db, CeriChangeEvent)
    ids = {int(value) for value in payload.get("change_ids", []) if str(value).isdigit()}
    if ids:
        changes = [change for change in changes if change.id in ids]
    run_id = _optional_int(payload.get("run_id"))
    if run_id is not None and not ids:
        snapshots = {
            snapshot.id
            for snapshot in _load_rows(db, CeriScoreSnapshot)
            if snapshot.run_id == run_id
        }
        changes = [
            change
            for change in changes
            if change.from_snapshot_id in snapshots or change.to_snapshot_id in snapshots
        ]
    company_ids = set(_optional_int_tuple(payload.get("company_ids")) or ())
    if company_ids:
        changes = [change for change in changes if change.company_id in company_ids]
    if payload.get("ticker"):
        company_ids_for_ticker = {
            company.id
            for company in _load_rows(db, CeriCompany)
            if company.ticker.upper() == str(payload["ticker"]).upper()
        }
        changes = [change for change in changes if change.company_id in company_ids_for_ticker]
    since = _optional_datetime(payload.get("changed_since"))
    if since is not None:
        changes = [change for change in changes if change.created_at >= since]
    return sorted(changes, key=lambda change: (change.created_at, change.id or 0))


def _load_rows(db: Session, model: Any) -> list[Any]:
    scalars = getattr(db, "scalars", None)
    if not callable(scalars):
        return []
    result = scalars(select(model))
    return list(result.all() if hasattr(result, "all") else result)


def _scope_key(payload: dict[str, Any], fallback: int) -> str:
    import hashlib
    import json

    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:24] or str(fallback)


def _child_priority(job: BackgroundJob) -> int:
    """Run dependent work before returning to the wider provider backlog.

    Background jobs sort smaller numeric priorities first. CERI previously
    added ten at each stage, which inverted the intended dependency order and
    starved normalize/feature/capture jobs behind every provider request.
    """

    return max(0, int(job.priority or 100) - 10)


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
            "ticker": source_payload.get("ticker"),
            "run_id": source_payload.get("run_id") or source_job.related_run_id,
            "config_version": source_payload.get("config_version"),
            "config_hash": source_payload.get("config_hash"),
            "actor": source_payload.get("actor"),
        },
        related_run_id=source_job.related_run_id,
        priority=_child_priority(source_job),
        max_retries=source_job.max_retries or 3,
        request_key=request_key,
    )
    return job.id


def _enqueue_feature_rebuild_after_normalize(
    db: Session,
    *,
    source_job: BackgroundJob,
    source_payload: dict[str, Any],
    normalize_result: dict[str, Any],
) -> int | None:
    if normalize_result.get("status") != "COMPLETED":
        return None
    ticker = source_payload.get("ticker")
    if not ticker:
        return None
    upstream_id = normalize_result.get("processing_run_id") or source_job.id
    request_key = f"ceri:feature-rebuild:upstream:{upstream_id}"
    job = enqueue_job(
        db,
        CERI_REBUILD_FEATURES,
        {
            "request_key": request_key,
            "ticker": str(ticker).upper(),
            "run_id": source_payload.get("run_id") or source_job.related_run_id,
            "scope": source_payload.get("scope") or {"ticker": str(ticker).upper()},
            "mode": "AS_KNOWN",
        },
        related_run_id=source_payload.get("run_id") or source_job.related_run_id,
        priority=_child_priority(source_job),
        max_retries=source_job.max_retries or 3,
        request_key=request_key,
    )
    return job.id


def _enqueue_capture_after_features(
    db: Session, *, job: BackgroundJob, payload: dict[str, Any]
) -> int | None:
    if not ceri_flags().run_capture:
        return None
    run_id = _optional_int(payload.get("run_id") or job.related_run_id)
    if run_id is None:
        return None
    # A provider run rebuilds features independently for each ticker/dataset.
    # Use the completed feature job as part of the capture identity so later
    # feature arrivals can capture newly eligible tickers. CeriRunCaptureService
    # remains idempotent and skips snapshots already stored for this run/version.
    upstream_id = job.id or payload.get("request_key") or "unknown"
    request_key = f"ceri:capture-run:{run_id}:upstream:{upstream_id}"
    capture_job = enqueue_job(
        db,
        CERI_CAPTURE_RUN,
        {"request_key": request_key, "run_id": run_id},
        related_run_id=run_id,
        priority=_child_priority(job),
        max_retries=job.max_retries or 3,
        request_key=request_key,
    )
    return capture_job.id


def _enqueue_change_after_capture(db: Session, *, job: BackgroundJob, run_id: int) -> int | None:
    request_key = (
        f"{job.workflow_key}:change" if job.workflow_key else f"ceri:change-rebuild:run:{run_id}"
    )
    change_job = enqueue_job(
        db,
        CERI_CHANGE_DETECTION,
        {
            "request_key": request_key,
            "run_id": run_id,
            "workflow_key": job.workflow_key,
        },
        related_run_id=run_id,
        priority=_child_priority(job),
        max_retries=job.max_retries or 3,
        request_key=request_key,
        workflow_key=job.workflow_key,
    )
    return change_job.id


def _enqueue_alert_after_change(
    db: Session,
    *,
    job: BackgroundJob,
    payload: dict[str, Any],
    change_ids: tuple[int, ...] = (),
) -> int | None:
    if not ceri_flags().alerts:
        return None
    upstream_id = payload.get("run_id") or job.related_run_id or job.id
    request_key = (
        f"{job.workflow_key}:alert"
        if job.workflow_key
        else f"ceri:alert-rebuild:upstream:{upstream_id}"
    )
    alert_job = enqueue_job(
        db,
        CERI_ALERT_REBUILD,
        {
            "request_key": request_key,
            "run_id": payload.get("run_id"),
            "workflow_key": job.workflow_key,
            "change_ids": list(change_ids),
        },
        related_run_id=job.related_run_id,
        priority=_child_priority(job),
        max_retries=job.max_retries or 3,
        request_key=request_key,
        workflow_key=job.workflow_key,
    )
    return alert_job.id


def _skipped_job(job_type: str, reason: str) -> dict[str, Any]:
    return {"job_type": job_type, "status": "SKIPPED", "skipped": 1, "reason": reason}


def _safe_job_error(exc: Exception) -> str:
    return str(exc).replace("\n", " ").strip()[:500] or exc.__class__.__name__


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


def _optional_int_tuple(value: Any) -> tuple[int, ...] | None:
    if value in (None, ""):
        return None
    if isinstance(value, (str, int)):
        value = [value]
    return tuple(int(item) for item in value if str(item).strip())


def _optional_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


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
