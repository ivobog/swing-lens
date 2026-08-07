from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi import status as http_status
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.ceri_tables import (
    CeriAlertEvent,
    CeriCatalystEvent,
    CeriCatalystEventRevision,
    CeriManualReview,
    CeriProcessingRun,
    CeriPurgeAudit,
)
from app.models.tables import BackgroundJob
from app.routers.export_responses import attachment_response
from app.security import (
    ROUTE_CLASS_LOCAL_ADMIN,
    local_admin_csrf_token,
    require_local_admin,
    unsafe_route,
)
from app.services.background_job_service import (
    enqueue_job,
    request_job_cancel,
)
from app.services.ceri.alert_service import CeriAlertService
from app.services.ceri.backfill_service import CeriBackfillRequest, CeriBackfillService
from app.services.ceri.export_service import CeriExportService
from app.services.ceri.feature_flags import ceri_flags, require_flag
from app.services.ceri.job_handlers import (
    CERI_ALERT_REBUILD,
    CERI_BACKFILL,
    CERI_CAPTURE_RUN,
    CERI_CHANGE_DETECTION,
    CERI_NORMALIZE,
    CERI_PROVIDER_INGEST,
    CERI_PURGE_LICENSED_DATA,
    CERI_REBUILD_FEATURES,
)
from app.services.ceri.provider_registry import CeriProviderRegistry, CeriProviderRegistryError
from app.services.ceri.query_service import (
    CeriListQuery,
    CeriQueryError,
    CeriQueryFilters,
    CeriQueryService,
)
from app.services.redaction import redact_sensitive
from app.services.resource_limits import (
    ResourceLimitExceeded,
    enforce_row_limit,
    limit_error_payload,
)
from app.settings import get_settings
from app.templates import templates


def _require_ceri_ui(request: Request) -> None:
    settings = getattr(request.app.state, "settings", None)
    flags = ceri_flags(settings)
    if not flags.ui:
        raise HTTPException(status_code=404, detail="CERI UI is disabled.")


router = APIRouter(tags=["ceri"])
DbSession = Annotated[Session, Depends(get_db)]


@router.get("/ceri", response_class=HTMLResponse, dependencies=[Depends(_require_ceri_ui)])
def ceri_dashboard_page(
    request: Request,
    db: DbSession,
    opportunity_min: float | None = None,
    risk_max: float | None = None,
    confidence: str | None = None,
    catalyst_category: str | None = None,
    posture: str | None = None,
    has_warnings: bool | None = None,
    sort: str = "opportunity_score",
    direction: str = "desc",
    limit: int = 50,
    offset: int = 0,
) -> HTMLResponse:
    query = _list_query(
        opportunity_min=opportunity_min,
        risk_max=risk_max,
        confidence=confidence,
        catalyst_category=catalyst_category,
        posture=posture,
        has_warnings=has_warnings,
        sort=sort,
        direction=direction,
        limit=limit,
        offset=offset,
    )
    latest, latest_error = _ui_payload_or_empty(lambda: CeriQueryService().latest(db, query))
    changes, _changes_error = _ui_payload_or_empty(
        lambda: CeriQueryService().changes(
            db,
            _list_query(sort="created_at", direction="desc", limit=25),
        )
    )
    operations = CeriQueryService().operations_status(db)
    return templates.TemplateResponse(
        request,
        "ceri_dashboard.html",
        {
            "active_nav": "ceri",
            "items": latest.get("items", []),
            "page": latest,
            "summary": _dashboard_summary(
                latest.get("items", []),
                changes.get("items", []),
                operations,
            ),
            "changes": _group_changes(changes.get("items", [])),
            "provider_freshness": operations.get("dataset_freshness", []),
            "filters": {
                "opportunity_min": opportunity_min,
                "risk_max": risk_max,
                "confidence": confidence or "",
                "catalyst_category": catalyst_category or "",
                "posture": posture or "",
                "has_warnings": has_warnings,
                "sort": sort,
                "direction": direction,
                "limit": limit,
                "offset": offset,
            },
            "ui_error": latest_error,
        },
    )


@router.get(
    "/runs/{run_id}/ceri", response_class=HTMLResponse, dependencies=[Depends(_require_ceri_ui)]
)
def ceri_run_page(
    run_id: int,
    request: Request,
    db: DbSession,
    sort: str = "opportunity_score",
    direction: str = "desc",
    limit: int = 100,
    offset: int = 0,
) -> HTMLResponse:
    payload, ui_error = _ui_payload_or_empty(
        lambda: CeriQueryService().run(
            db,
            run_id,
            _list_query(sort=sort, direction=direction, limit=limit, offset=offset),
        )
    )
    changes, _changes_error = _ui_payload_or_empty(
        lambda: CeriQueryService().changes(
            db,
            _list_query(sort="created_at", direction="desc", limit=25),
        )
    )
    return templates.TemplateResponse(
        request,
        "ceri_dashboard.html",
        {
            "active_nav": "ceri",
            "run_id": run_id,
            "items": payload.get("items", []),
            "page": payload,
            "summary": _dashboard_summary(payload.get("items", []), changes.get("items", []), {}),
            "changes": _group_changes(changes.get("items", [])),
            "provider_freshness": [],
            "filters": {
                "sort": sort,
                "direction": direction,
                "limit": limit,
                "offset": offset,
            },
            "ui_error": ui_error,
        },
    )


@router.get(
    "/ceri/ticker/{ticker}",
    response_class=HTMLResponse,
    dependencies=[Depends(_require_ceri_ui)],
)
def ceri_ticker_page(ticker: str, request: Request, db: DbSession) -> HTMLResponse:
    payload, ui_error = _ui_payload_or_empty(lambda: CeriQueryService().ticker(db, ticker))
    return templates.TemplateResponse(
        request,
        "ceri_ticker.html",
        {
            "active_nav": "ceri",
            "payload": payload,
            "ui_error": ui_error,
        },
    )


@router.get("/ceri/changes", response_class=HTMLResponse, dependencies=[Depends(_require_ceri_ui)])
def ceri_changes_page(
    request: Request,
    db: DbSession,
    ticker: str | None = None,
    status: str | None = None,
    sort: str = "created_at",
    direction: str = "desc",
    limit: int = 50,
    offset: int = 0,
) -> HTMLResponse:
    changes, changes_error = _ui_payload_or_empty(
        lambda: CeriQueryService().changes(
            db,
            _list_query(ticker=ticker, sort=sort, direction=direction, limit=limit, offset=offset),
        )
    )
    alerts, alerts_error = _ui_payload_or_empty(
        lambda: CeriQueryService().alerts(
            db,
            _list_query(ticker=ticker, sort="created_at", direction="desc", limit=limit),
        )
    )
    if status:
        alerts["items"] = [item for item in alerts.get("items", []) if item["status"] == status]
    return templates.TemplateResponse(
        request,
        "ceri_changes.html",
        {
            "active_nav": "ceri-alerts",
            "change_groups": _group_changes(changes.get("items", [])),
            "alerts": alerts.get("items", []),
            "page": changes,
            "filters": {
                "ticker": ticker or "",
                "status": status or "",
                "sort": sort,
                "direction": direction,
                "limit": limit,
                "offset": offset,
            },
            "ui_error": changes_error or alerts_error,
            "admin_enabled": _admin_enabled(request),
            "csrf_token": local_admin_csrf_token(request) if _admin_enabled(request) else "",
        },
    )


@router.get(
    "/ceri/operations",
    response_class=HTMLResponse,
    dependencies=[Depends(_require_ceri_ui)],
)
def ceri_operations_page(request: Request, db: DbSession) -> HTMLResponse:
    operations = CeriQueryService().operations_status(db)
    quarantine, _quarantine_error = _ui_payload_or_empty(
        lambda: CeriQueryService().operations_quarantine(
            db,
            _list_query(sort="ingested_at", direction="desc", limit=50),
        )
    )
    conflicts, _conflict_error = _ui_payload_or_empty(
        lambda: CeriQueryService().operations_conflicts(
            db,
            _list_query(sort="id", direction="desc", limit=50),
        )
    )
    stale, _stale_error = _ui_payload_or_empty(
        lambda: CeriQueryService().operations_stale(
            db,
            _list_query(sort="stale_days", direction="desc", limit=50),
        )
    )
    return templates.TemplateResponse(
        request,
        "ceri_operations.html",
        {
            "active_nav": "ceri-operations",
            "operations": operations,
            "provider_health": _provider_health_payload(),
            "quarantined": quarantine.get("items", []),
            "conflicts": conflicts.get("items", []),
            "stale": stale.get("items", []),
            "admin_enabled": _admin_enabled(request),
            "csrf_token": local_admin_csrf_token(request) if _admin_enabled(request) else "",
        },
    )


@router.get("/api/ceri/latest", dependencies=[Depends(_require_ceri_ui)])
def ceri_latest(
    db: DbSession,
    opportunity_min: float | None = None,
    risk_max: float | None = None,
    confidence: str | None = None,
    catalyst_category: str | None = None,
    posture: str | None = None,
    alignment_flag: str | None = None,
    has_warnings: bool | None = None,
    config_version: str | None = None,
    sort: str = "opportunity_score",
    direction: str = "desc",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    return _query_or_http(
        lambda: CeriQueryService().latest(
            db,
            _list_query(
                opportunity_min=opportunity_min,
                risk_max=risk_max,
                confidence=confidence,
                catalyst_category=catalyst_category,
                posture=posture,
                alignment_flag=alignment_flag,
                has_warnings=has_warnings,
                config_version=config_version,
                sort=sort,
                direction=direction,
                limit=limit,
                offset=offset,
            ),
        )
    )


@router.get("/api/ceri/run/{run_id}", dependencies=[Depends(_require_ceri_ui)])
def ceri_run(
    run_id: int,
    db: DbSession,
    sort: str = "opportunity_score",
    direction: str = "desc",
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    return _query_or_http(
        lambda: CeriQueryService().run(
            db,
            run_id,
            _list_query(sort=sort, direction=direction, limit=limit, offset=offset),
        )
    )


@router.get("/api/ceri/ticker/{ticker}", dependencies=[Depends(_require_ceri_ui)])
def ceri_ticker(ticker: str, db: DbSession) -> dict[str, Any]:
    return _query_or_http(lambda: CeriQueryService().ticker(db, ticker))


@router.get("/api/ceri/ticker/{ticker}/history", dependencies=[Depends(_require_ceri_ui)])
def ceri_ticker_history(
    ticker: str,
    db: DbSession,
    mode: str | None = None,
    as_of: datetime | None = None,
    sort: str = "cutoff_at",
    direction: str = "desc",
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    return _query_or_http(
        lambda: CeriQueryService().ticker_history(
            db,
            ticker,
            _list_query(
                mode=mode,
                as_of=as_of,
                sort=sort,
                direction=direction,
                limit=limit,
                offset=offset,
            ),
        )
    )


@router.get("/api/ceri/changes", dependencies=[Depends(_require_ceri_ui)])
def ceri_changes(
    db: DbSession,
    ticker: str | None = None,
    changed_since: datetime | None = None,
    sort: str = "created_at",
    direction: str = "desc",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    return _query_or_http(
        lambda: CeriQueryService().changes(
            db,
            _list_query(
                ticker=ticker,
                changed_since=changed_since,
                sort=sort,
                direction=direction,
                limit=limit,
                offset=offset,
            ),
        )
    )


@router.get("/api/ceri/events", dependencies=[Depends(_require_ceri_ui)])
def ceri_events(
    db: DbSession,
    ticker: str | None = None,
    catalyst_category: str | None = None,
    event_date_from: datetime | None = None,
    event_date_to: datetime | None = None,
    has_conflicts: bool | None = None,
    sort: str = "event_date",
    direction: str = "asc",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    return _query_or_http(
        lambda: CeriQueryService().events(
            db,
            _list_query(
                ticker=ticker,
                catalyst_category=catalyst_category,
                event_date_from=event_date_from.date() if event_date_from else None,
                event_date_to=event_date_to.date() if event_date_to else None,
                has_conflicts=has_conflicts,
                sort=sort,
                direction=direction,
                limit=limit,
                offset=offset,
            ),
        )
    )


@router.get("/api/ceri/events/{event_id}", dependencies=[Depends(_require_ceri_ui)])
def ceri_event(event_id: int, db: DbSession) -> dict[str, Any]:
    return _query_or_http(lambda: CeriQueryService().event_detail(db, event_id))


@router.get("/api/ceri/events/{event_id}/revisions", dependencies=[Depends(_require_ceri_ui)])
def ceri_event_revisions(
    event_id: int,
    db: DbSession,
    sort: str = "revision_number",
    direction: str = "asc",
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    return _query_or_http(
        lambda: CeriQueryService().event_revisions(
            db,
            event_id,
            _list_query(sort=sort, direction=direction, limit=limit, offset=offset),
        )
    )


@router.get("/api/ceri/revisions", dependencies=[Depends(_require_ceri_ui)])
def ceri_revisions(
    db: DbSession,
    ticker: str | None = None,
    eps_revision_window: int | None = None,
    revenue_revision_window: int | None = None,
    revision_breadth_min: float | None = None,
    sort: str = "as_of_session",
    direction: str = "desc",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    return _query_or_http(
        lambda: CeriQueryService().revisions(
            db,
            _list_query(
                ticker=ticker,
                eps_revision_window=eps_revision_window,
                revenue_revision_window=revenue_revision_window,
                revision_breadth_min=revision_breadth_min,
                sort=sort,
                direction=direction,
                limit=limit,
                offset=offset,
            ),
        )
    )


@router.get("/api/ceri/revisions/{revision_id}", dependencies=[Depends(_require_ceri_ui)])
def ceri_revision(revision_id: int, db: DbSession) -> dict[str, Any]:
    return _query_or_http(lambda: CeriQueryService().revision_detail(db, revision_id))


@router.get("/api/ceri/alerts", dependencies=[Depends(_require_ceri_ui)])
def ceri_alerts(
    db: DbSession,
    ticker: str | None = None,
    status: Annotated[str | None, Query(alias="status")] = None,
    sort: str = "created_at",
    direction: str = "desc",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    payload = _list_query(ticker=ticker, sort=sort, direction=direction, limit=limit, offset=offset)
    result = _query_or_http(lambda: CeriQueryService().alerts(db, payload))
    if status:
        items = [item for item in result["items"] if item["status"] == status]
        result = {**result, "items": items, "total": len(items)}
    return result


@router.get("/api/ceri/operations/quarantine", dependencies=[Depends(_require_ceri_ui)])
def ceri_operations_quarantine(
    db: DbSession,
    sort: str = "ingested_at",
    direction: str = "desc",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    return _query_or_http(
        lambda: CeriQueryService().operations_quarantine(
            db,
            _list_query(sort=sort, direction=direction, limit=limit, offset=offset),
        )
    )


@router.get("/api/ceri/operations/conflicts", dependencies=[Depends(_require_ceri_ui)])
def ceri_operations_conflicts(
    db: DbSession,
    sort: str = "id",
    direction: str = "desc",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    return _query_or_http(
        lambda: CeriQueryService().operations_conflicts(
            db,
            _list_query(sort=sort, direction=direction, limit=limit, offset=offset),
        )
    )


@router.get("/api/ceri/operations/stale", dependencies=[Depends(_require_ceri_ui)])
def ceri_operations_stale(
    db: DbSession,
    sort: str = "stale_days",
    direction: str = "desc",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    return _query_or_http(
        lambda: CeriQueryService().operations_stale(
            db,
            _list_query(sort=sort, direction=direction, limit=limit, offset=offset),
        )
    )


@router.get("/api/ceri/operations/status", dependencies=[Depends(_require_ceri_ui)])
def ceri_operations_status(db: DbSession) -> dict[str, Any]:
    return CeriQueryService().operations_status(db)


@router.get("/api/ceri/jobs/{job_id}", dependencies=[Depends(_require_ceri_ui)])
def ceri_job_status(job_id: int, db: DbSession) -> dict[str, Any]:
    job = db.get(BackgroundJob, job_id)
    if job is None:
        raise _structured_http_error(
            "RUN_NOT_FOUND",
            f"CERI job was not found: {job_id}",
            status_code=404,
        )
    return {
        "job_id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "requested_cancel": bool(job.requested_cancel),
        "payload": redact_sensitive(job.payload_json),
        "result": redact_sensitive(job.result_json),
        "error_message": redact_sensitive(job.error_message),
    }


@router.get("/ceri/export.csv", dependencies=[Depends(_require_ceri_ui)])
def export_ceri_csv(
    db: DbSession,
    run_id: int | None = None,
    ticker: str | None = None,
) -> Response:
    result = CeriExportService().current_view(
        db,
        run_id=run_id,
        tickers=[ticker] if ticker else None,
        output_format="csv",
    )
    content = result.to_csv()
    _enforce_export_rows(_export_result_row_count(result, content), resource="CERI export")
    return attachment_response(
        content,
        media_type="text/csv",
        filename="ceri_export.csv",
    )


@router.get("/ceri/export.json", dependencies=[Depends(_require_ceri_ui)])
def export_ceri_json(
    db: DbSession,
    run_id: int | None = None,
    ticker: str | None = None,
) -> Response:
    result = CeriExportService().current_view(
        db,
        run_id=run_id,
        tickers=[ticker] if ticker else None,
        output_format="json",
    )
    content = result.to_json()
    _enforce_export_rows(_export_result_row_count(result, content), resource="CERI export")
    return attachment_response(
        content,
        media_type="application/json",
        filename="ceri_export.json",
    )


@router.post("/api/ceri/ingestion-runs")
@unsafe_route(
    ROUTE_CLASS_LOCAL_ADMIN,
    reason="queues licensed/provider CERI ingestion",
    csrf_required=True,
    local_admin_required=True,
)
def create_ceri_ingestion_run(
    request: Request,
    db: DbSession,
    payload: dict[str, Any] | None = None,
) -> JSONResponse:
    _require_local_admin(request)
    _require_child_flag(request, "provider_ingest")
    payload = dict(payload or {})
    if not payload.get("ticker") or not payload.get("dataset"):
        raise _structured_http_error(
            "INVALID_FILTER", "ticker and dataset are required.", status_code=400
        )
    job = _enqueue_job_once(
        db,
        CERI_PROVIDER_INGEST,
        payload,
        related_run_id=payload.get("run_id"),
    )
    db.commit()
    return _job_response(job, coalesced=getattr(job, "_coalesced", False))


@router.post("/api/ceri/recalculate")
@unsafe_route(
    ROUTE_CLASS_LOCAL_ADMIN,
    reason="queues CERI recalculation or feature rebuild",
    csrf_required=True,
    local_admin_required=True,
)
def recalculate_ceri(
    request: Request,
    db: DbSession,
    payload: dict[str, Any] | None = None,
) -> JSONResponse:
    _require_local_admin(request)
    payload = dict(payload or {})
    job_type = CERI_CAPTURE_RUN if payload.get("run_id") else CERI_REBUILD_FEATURES
    _require_child_flag(request, "run_capture" if job_type == CERI_CAPTURE_RUN else "enabled")
    job = _enqueue_job_once(db, job_type, payload, related_run_id=payload.get("run_id"))
    db.commit()
    return _job_response(job, coalesced=getattr(job, "_coalesced", False))


@router.post("/api/ceri/events/{event_id}/review")
@unsafe_route(
    ROUTE_CLASS_LOCAL_ADMIN,
    reason="persists manual CERI review state",
    csrf_required=True,
    local_admin_required=True,
)
def review_ceri_event(
    event_id: int,
    request: Request,
    db: DbSession,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _require_local_admin(request)
    event = db.get(CeriCatalystEvent, event_id)
    if event is None:
        raise _structured_http_error(
            "TICKER_NOT_FOUND", "CERI event was not found.", status_code=404
        )
    current_revision = db.scalar(
        select(CeriCatalystEventRevision)
        .where(CeriCatalystEventRevision.catalyst_event_id == event_id)
        .where(CeriCatalystEventRevision.is_current.is_(True))
    )
    review_payload = dict(payload or {})
    existing = _active_manual_review(db, "CATALYST_EVENT", event_id)
    if existing is not None and existing.new_value_json != review_payload.get("new_value"):
        raise _structured_http_error(
            "REVIEW_CONFLICT", "Active review already exists.", status_code=409
        )
    review = existing or CeriManualReview(
        target_type="CATALYST_EVENT",
        target_id=event_id,
        prior_value_json={"review_state": getattr(current_revision, "review_state", None)},
        new_value_json=review_payload.get("new_value") or {"review_state": "REVIEWED"},
        reviewer=str(review_payload.get("reviewer") or "local-admin"),
        reason=str(review_payload.get("reason") or "CERI local review"),
        is_current=True,
    )
    if existing is None:
        db.add(review)
    if current_revision is not None:
        current_revision.review_state = (review.new_value_json or {}).get(
            "review_state", "REVIEWED"
        )
    db.commit()
    return {
        "id": review.id,
        "target_type": review.target_type,
        "target_id": review.target_id,
        "status": "RECORDED",
    }


@router.post("/api/ceri/jobs/{job_id}/cancel")
@unsafe_route(
    ROUTE_CLASS_LOCAL_ADMIN,
    reason="requests cancellation of a CERI background job",
    csrf_required=True,
    local_admin_required=True,
)
def cancel_ceri_job(job_id: int, request: Request, db: DbSession) -> dict[str, Any]:
    _require_local_admin(request)
    try:
        job = request_job_cancel(db, job_id)
    except ValueError as exc:
        raise _structured_http_error("RUN_NOT_FOUND", str(exc), status_code=404) from exc
    db.commit()
    return {
        "job_id": job.id,
        "status": job.status,
        "requested_cancel": bool(job.requested_cancel),
    }


@router.post("/api/ceri/backfills")
@unsafe_route(
    ROUTE_CLASS_LOCAL_ADMIN,
    reason="queues CERI backfill",
    csrf_required=True,
    local_admin_required=True,
)
def create_ceri_backfill(
    request: Request,
    db: DbSession,
    payload: dict[str, Any] | None = None,
) -> JSONResponse:
    _require_local_admin(request)
    _require_child_flag(request, "backfill")
    payload = dict(payload or {})
    backfill_request = CeriBackfillRequest(
        provider=str(payload.get("provider") or "manual"),
        dataset=str(payload.get("dataset") or "estimates"),
        ticker=payload.get("ticker"),
        start=_optional_date_payload(payload.get("start")),
        end=_optional_date_payload(payload.get("end")),
        mode=str(payload.get("mode") or "AS_KNOWN"),
        tickers=tuple(str(ticker) for ticker in payload.get("tickers", []) if str(ticker).strip()),
        actor=payload.get("actor"),
    )
    request_key = CeriBackfillService().request_key(backfill_request)
    if _active_processing_run(db, "CERI_BACKFILL", request_key):
        raise _structured_http_error(
            "BACKFILL_ALREADY_ACTIVE",
            "Matching CERI backfill is already active.",
            status_code=409,
        )
    payload["request_key"] = request_key
    job = _enqueue_job_once(db, CERI_BACKFILL, payload)
    db.commit()
    return _job_response(job, coalesced=getattr(job, "_coalesced", False))


@router.post("/api/ceri/reprocess")
@unsafe_route(
    ROUTE_CLASS_LOCAL_ADMIN,
    reason="queues CERI reprocessing",
    csrf_required=True,
    local_admin_required=True,
)
def reprocess_ceri(
    request: Request,
    db: DbSession,
    payload: dict[str, Any] | None = None,
) -> JSONResponse:
    _require_local_admin(request)
    payload = dict(payload or {})
    job_type = str(payload.get("job_type") or CERI_CHANGE_DETECTION)
    if job_type not in {
        CERI_NORMALIZE,
        CERI_CHANGE_DETECTION,
        CERI_ALERT_REBUILD,
        CERI_CAPTURE_RUN,
    }:
        raise _structured_http_error(
            "INVALID_FILTER",
            f"Unsupported reprocess job_type: {job_type}",
            status_code=400,
        )
    flag_name = {
        CERI_CAPTURE_RUN: "run_capture",
        CERI_ALERT_REBUILD: "alerts",
    }.get(job_type, "enabled")
    _require_child_flag(request, flag_name)
    job = _enqueue_job_once(db, job_type, payload, related_run_id=payload.get("run_id"))
    db.commit()
    return _job_response(job, coalesced=getattr(job, "_coalesced", False))


@router.post("/api/ceri/alerts/{alert_id}/acknowledge")
@unsafe_route(
    ROUTE_CLASS_LOCAL_ADMIN,
    reason="mutates CERI alert acknowledgement state",
    csrf_required=True,
    local_admin_required=True,
)
def acknowledge_ceri_alert(alert_id: int, request: Request, db: DbSession) -> dict[str, Any]:
    _require_local_admin(request)
    _require_child_flag(request, "alerts")
    alert = db.get(CeriAlertEvent, alert_id)
    if alert is None:
        raise _structured_http_error(
            "TICKER_NOT_FOUND", "CERI alert was not found.", status_code=404
        )
    CeriAlertService().acknowledge(db, alert)
    db.commit()
    return {"id": alert.id, "status": alert.status}


@router.post("/api/ceri/alerts/{alert_id}/dismiss")
@unsafe_route(
    ROUTE_CLASS_LOCAL_ADMIN,
    reason="mutates CERI alert dismissal state",
    csrf_required=True,
    local_admin_required=True,
)
def dismiss_ceri_alert(alert_id: int, request: Request, db: DbSession) -> dict[str, Any]:
    _require_local_admin(request)
    _require_child_flag(request, "alerts")
    alert = db.get(CeriAlertEvent, alert_id)
    if alert is None:
        raise _structured_http_error(
            "TICKER_NOT_FOUND", "CERI alert was not found.", status_code=404
        )
    CeriAlertService().dismiss(db, alert)
    db.commit()
    return {"id": alert.id, "status": alert.status}


@router.post("/api/ceri/purge/preview")
@unsafe_route(
    ROUTE_CLASS_LOCAL_ADMIN,
    reason="queues CERI licensed-data purge preview",
    csrf_required=True,
    local_admin_required=True,
)
def preview_ceri_purge(
    request: Request,
    db: DbSession,
    payload: dict[str, Any] | None = None,
) -> JSONResponse:
    _require_local_admin(request)
    payload = dict(payload or {})
    if not payload.get("provider") or not payload.get("license_scope"):
        raise _structured_http_error(
            "INVALID_FILTER",
            "provider and license_scope are required.",
            status_code=400,
        )
    payload.pop("preview_manifest_hash", None)
    job = _enqueue_job_once(db, CERI_PURGE_LICENSED_DATA, payload)
    db.commit()
    return _job_response(job, coalesced=getattr(job, "_coalesced", False))


@router.post("/api/ceri/purge/execute")
@unsafe_route(
    ROUTE_CLASS_LOCAL_ADMIN,
    reason="queues CERI licensed-data purge execution",
    csrf_required=True,
    local_admin_required=True,
)
def execute_ceri_purge(
    request: Request,
    db: DbSession,
    payload: dict[str, Any] | None = None,
) -> JSONResponse:
    _require_local_admin(request)
    payload = dict(payload or {})
    if not payload.get("confirmation_token"):
        raise _structured_http_error(
            "PURGE_CONFIRMATION_REQUIRED",
            "CERI purge execution requires confirmation_token.",
            status_code=400,
        )
    preview_hash = payload.get("preview_manifest_hash")
    if preview_hash:
        audit = db.scalar(
            select(CeriPurgeAudit).where(CeriPurgeAudit.preview_manifest_hash == str(preview_hash))
        )
        if audit is None:
            raise _structured_http_error(
                "PURGE_CONFIRMATION_REQUIRED",
                "Purge preview was not found.",
                status_code=400,
            )
    payload["execute"] = True
    payload["preview_manifest_hash"] = preview_hash or _stable_request_key(payload)
    job = _enqueue_job_once(db, CERI_PURGE_LICENSED_DATA, payload)
    db.commit()
    return _job_response(job, coalesced=getattr(job, "_coalesced", False))


def _list_query(
    *,
    sort: str = "opportunity_score",
    direction: str = "desc",
    limit: int = 50,
    offset: int = 0,
    **filters: Any,
) -> CeriListQuery:
    return CeriListQuery(
        filters=CeriQueryFilters(**filters),
        sort=sort,
        direction=direction,
        limit=limit,
        offset=offset,
    )


def _query_or_http(callback) -> dict[str, Any]:
    try:
        return callback()
    except CeriQueryError as exc:
        raise _structured_http_error(exc.code, exc.message, status_code=exc.status_code) from exc
    except ValueError as exc:
        raise _structured_http_error("INVALID_FILTER", str(exc), status_code=400) from exc


def _ui_payload_or_empty(callback) -> tuple[dict[str, Any], dict[str, str] | None]:
    try:
        return callback(), None
    except CeriQueryError as exc:
        return {"items": [], "total": 0}, {"code": exc.code, "message": exc.message}
    except ValueError as exc:
        return {"items": [], "total": 0}, {"code": "INVALID_FILTER", "message": str(exc)}


def _dashboard_summary(
    items: list[dict[str, Any]],
    changes: list[dict[str, Any]],
    operations: dict[str, Any],
) -> dict[str, Any]:
    return {
        "candidate_count": len(items),
        "high_opportunity_low_risk": sum(
            1
            for item in items
            if (item.get("opportunity_score") or 0) >= 7
            and (item.get("event_risk_score") or 10) <= 3
        ),
        "upward_revision_leaders": sum(
            1 for change in changes if change.get("change_type") == "REVISION_UP"
        ),
        "guidance_raises": sum(
            1 for change in changes if change.get("change_type") == "GUIDANCE_RAISED"
        ),
        "binary_risks": sum(
            1 for change in changes if change.get("change_type") == "NEW_BINARY_EVENT"
        ),
        "meaningful_changes": len(changes),
        "stale_count": operations.get("stale_count", 0),
        "conflicted_count": operations.get("conflicted_count", 0),
        "quarantined_count": operations.get("quarantined_count", 0),
    }


def _group_changes(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups = [
        ("Upward revisions", {"REVISION_UP", "REVISION_ACCELERATED"}),
        ("Downward revisions", {"REVISION_DOWN", "REVISION_DECELERATED"}),
        ("Guidance changes", {"GUIDANCE_RAISED", "GUIDANCE_LOWERED", "GUIDANCE_WITHDRAWN"}),
        ("New or updated catalysts", {"NEW_CATALYST", "CATALYST_UPDATED", "NEW_BINARY_EVENT"}),
        ("Opportunity changes", {"OPPORTUNITY_UPGRADED", "OPPORTUNITY_DOWNGRADED"}),
        ("Risk changes", {"RISK_ESCALATED", "RISK_DEESCALATED"}),
        ("Resolved events", {"CATALYST_RESOLVED", "CONFLICT_RESOLVED", "DATA_REFRESHED"}),
    ]
    assigned_ids: set[int] = set()
    payload = []
    for label, change_types in groups:
        rows = [
            change
            for change in changes
            if change.get("change_type") in change_types and change.get("id") not in assigned_ids
        ]
        assigned_ids.update(change.get("id") for change in rows if change.get("id") is not None)
        payload.append({"label": label, "items": rows})
    other = [
        change
        for change in changes
        if change.get("id") not in assigned_ids and change.get("id") is not None
    ]
    if other:
        payload.append({"label": "Other changes", "items": other})
    return payload


def _provider_health_payload() -> list[dict[str, Any]]:
    registry = CeriProviderRegistry()
    rows = []
    for provider in registry.priority_order():
        try:
            health = asdict(registry.health(provider))
            capabilities = asdict(registry.capabilities(provider))
            health["capabilities"] = sorted(
                str(capability) for capability in capabilities["capabilities"]
            )
            health["datasets"] = sorted(str(dataset) for dataset in capabilities["datasets"])
        except CeriProviderRegistryError as exc:
            health = {
                "provider": provider,
                "healthy": False,
                "checked_at": None,
                "quota_status": None,
                "message": str(exc),
                "capabilities": [],
                "datasets": [],
            }
        rows.append(health)
    return rows


def _admin_enabled(request: Request) -> bool:
    settings = getattr(request.app.state, "settings", None)
    return ceri_flags(settings).admin


def _require_local_admin(request: Request) -> None:
    settings = getattr(request.app.state, "settings", None)
    require_local_admin(
        request,
        enabled=ceri_flags(settings).admin,
        disabled_message="CERI admin is disabled.",
        local_only_message="CERI admin is local only.",
        csrf_message="CERI admin CSRF token is required.",
        structured_code="ADMIN_FORBIDDEN",
        csrf_required=True,
    )


def _require_child_flag(request: Request, name: str) -> None:
    settings = getattr(request.app.state, "settings", None)
    try:
        require_flag(ceri_flags(settings), name)
    except Exception as exc:
        raise _structured_http_error("CERI_DISABLED", str(exc), status_code=404) from exc


def _enqueue_job_once(
    db: Session,
    job_type: str,
    payload: dict[str, Any],
    *,
    related_run_id: int | None = None,
) -> BackgroundJob:
    request_key = str(
        payload.get("request_key") or _stable_request_key({"job_type": job_type, **payload})
    )
    payload["request_key"] = request_key
    return enqueue_job(
        db,
        job_type,
        payload,
        related_run_id=related_run_id,
        request_key=request_key,
    )


def _active_processing_run(db: Session, job_type: str, request_key: str) -> bool:
    for run in _load_processing_runs(db):
        if run.job_type == job_type and run.deterministic_request_key == request_key:
            return run.status in {"PENDING", "RUNNING"}
    return False


def _active_manual_review(db: Session, target_type: str, target_id: int) -> CeriManualReview | None:
    return db.scalar(
        select(CeriManualReview)
        .where(CeriManualReview.target_type == target_type)
        .where(CeriManualReview.target_id == target_id)
        .where(CeriManualReview.is_current.is_(True))
    )


def _load_processing_runs(db: Session) -> list[CeriProcessingRun]:
    scalars = getattr(db, "scalars", None)
    if not callable(scalars):
        return []
    result = scalars(select(CeriProcessingRun))
    return list(result.all() if hasattr(result, "all") else result)


def _stable_request_key(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _job_response(job: BackgroundJob, *, coalesced: bool = False) -> JSONResponse:
    return JSONResponse(
        {
            "job_id": job.id,
            "job_type": job.job_type,
            "status": job.status,
            "coalesced": coalesced,
            "status_url": f"/api/ceri/jobs/{job.id}",
        },
        status_code=http_status.HTTP_202_ACCEPTED,
    )


def _enforce_export_rows(row_count: int, *, resource: str) -> None:
    settings = get_settings()
    try:
        enforce_row_limit(
            row_count,
            settings.max_export_rows,
            resource=resource,
            code="EXPORT_ROW_LIMIT_EXCEEDED",
        )
    except ResourceLimitExceeded as exc:
        raise HTTPException(
            status_code=413,
            detail=limit_error_payload(
                exc,
                hint="Narrow filters or lower the page/export size before retrying.",
            ),
        ) from exc


def _export_result_row_count(result: Any, content: str) -> int:
    rows = getattr(result, "rows", None)
    if rows is not None:
        return len(rows)
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        lines = [line for line in content.splitlines() if line.strip()]
        return max(len(lines) - 1, 0)
    return len(parsed) if isinstance(parsed, list) else 1


def _structured_http_error(code: str, message: str, *, status_code: int) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _optional_date_payload(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise _structured_http_error(
            "INVALID_FILTER", "date values must be ISO dates", status_code=400
        ) from exc
