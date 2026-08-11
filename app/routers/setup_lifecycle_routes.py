from __future__ import annotations

from datetime import date
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi import status as http_status
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.tables import UploadRun
from app.routers.export_responses import attachment_response
from app.security import ROUTE_CLASS_LOCAL_ADMIN, ROUTE_CLASS_PUBLIC_LOCAL, unsafe_route
from app.services.background_job_service import enqueue_job
from app.services.resource_limits import (
    ResourceLimitExceeded,
    enforce_row_limit,
    limit_error_payload,
)
from app.services.setup_lifecycle.alert_service import SetupLifecycleAlertService
from app.services.setup_lifecycle.evaluation_service import SetupLifecycleEvaluationService
from app.services.setup_lifecycle.export_service import (
    export_alerts_csv,
    export_changes_csv,
    export_episodes_csv,
    export_json,
)
from app.services.setup_lifecycle.job_handlers import SETUP_LIFECYCLE_EVALUATE_RUN
from app.services.setup_lifecycle.query_service import (
    SetupLifecycleFilters,
    SetupLifecycleListQuery,
    SetupLifecycleQueryError,
    SetupLifecycleQueryService,
)
from app.services.setup_lifecycle.replay_service import (
    SetupLifecycleReplayRequest,
    SetupLifecycleReplayService,
)
from app.settings import get_settings
from app.templates import templates

router = APIRouter(tags=["setup-lifecycle"])
DbSession = Annotated[Session, Depends(get_db)]
REPLAY_CONFIRMATION_PHRASE = "PERSIST_SETUP_LIFECYCLE_REPLAY"


@router.get("/setup-lifecycle", response_class=HTMLResponse)
def setup_lifecycle_page(
    request: Request,
    db: DbSession,
    quick_filter: str = "",
    ticker: str | None = None,
    sector: str | None = None,
    setup_family: str | None = None,
    lifecycle_state: str | None = None,
    transition: str | None = None,
    actionability: str | None = None,
    confidence_min: int | None = None,
    confidence_max: int | None = None,
    velocity_min: float | None = None,
    sort: str = "latest_event_time",
    direction: str = "desc",
    limit: int = 50,
    cursor: str | None = None,
    as_of: date | None = None,
    source_type: str | None = None,
) -> HTMLResponse:
    filter_values = _quick_filter_values(quick_filter)
    diagnostics = setup_lifecycle_diagnostics(db=db)
    selected_as_of = as_of or _date_value(diagnostics.get("latest_canonical_date"))
    payload = setup_lifecycle_changes(
        db=db,
        as_of=selected_as_of,
        source_type=source_type,
        ticker=ticker,
        sector=sector,
        setup_family=setup_family,
        lifecycle_state=filter_values.get("lifecycle_state", lifecycle_state),
        transition=filter_values.get("transition", transition),
        actionability=filter_values.get("actionability", actionability),
        confidence_min=filter_values.get("confidence_min", confidence_min),
        confidence_max=filter_values.get("confidence_max", confidence_max),
        velocity_min=filter_values.get("velocity_min", velocity_min),
        warning_flag=filter_values.get("warning_flag"),
        sort=filter_values.get("sort", sort),
        direction=direction,
        limit=limit,
        cursor=cursor,
    )
    return templates.TemplateResponse(
        request,
        "setup_lifecycle.html",
        _changes_template_context(
            payload=payload,
            diagnostics=diagnostics,
            filters={
                "quick_filter": quick_filter,
                "ticker": ticker or "",
                "sector": sector or "",
                "setup_family": setup_family or "",
                "lifecycle_state": filter_values.get("lifecycle_state", lifecycle_state) or "",
                "transition": filter_values.get("transition", transition) or "",
                "actionability": filter_values.get("actionability", actionability) or "",
                "confidence_min": filter_values.get("confidence_min", confidence_min),
                "confidence_max": filter_values.get("confidence_max", confidence_max),
                "velocity_min": filter_values.get("velocity_min", velocity_min),
                "sort": filter_values.get("sort", sort),
                "direction": direction,
                "limit": limit,
                "cursor": cursor or "",
                "as_of": selected_as_of.isoformat() if selected_as_of else "",
                "source_type": source_type or "",
            },
        ),
    )


@router.get("/setup-lifecycle/ticker/{ticker}", response_class=HTMLResponse)
def setup_lifecycle_ticker_page(
    request: Request,
    ticker: str,
    db: DbSession,
    timeframe: str = "1d",
    limit: int = 100,
) -> HTMLResponse:
    payload = setup_lifecycle_ticker_timeline(
        ticker=ticker,
        db=db,
        timeframe=timeframe,
        limit=limit,
    )
    return templates.TemplateResponse(
        request,
        "setup_lifecycle_ticker.html",
        _ticker_template_context(payload),
    )


@router.get("/setup-lifecycle/episodes/{episode_id}", response_class=HTMLResponse)
def setup_lifecycle_episode_page(
    request: Request,
    episode_id: int,
    db: DbSession,
) -> HTMLResponse:
    payload = setup_lifecycle_episode(episode_id=episode_id, db=db)
    return templates.TemplateResponse(
        request,
        "setup_lifecycle_episode.html",
        _episode_template_context(payload),
    )


@router.get("/setup-lifecycle/alerts", response_class=HTMLResponse)
def setup_lifecycle_alerts_page(
    request: Request,
    db: DbSession,
    ticker: str | None = None,
    status: str | None = None,
    severity: str | None = None,
    sort: str = "latest_event_time",
    direction: str = "desc",
    limit: int = 50,
    cursor: str | None = None,
    as_of: date | None = None,
    alert_type: str | None = None,
    source_type: str | None = None,
    lifecycle_state: str | None = None,
) -> HTMLResponse:
    payload = setup_lifecycle_alerts(
        db=db,
        ticker=ticker,
        status=status,
        severity=severity,
        sort=sort,
        direction=direction,
        limit=limit,
        cursor=cursor,
        as_of=as_of,
        alert_type=alert_type,
        source_type=source_type,
        lifecycle_state=lifecycle_state,
    )
    return templates.TemplateResponse(
        request,
        "setup_lifecycle_alerts.html",
        _alerts_template_context(
            payload=payload,
            filters={
                "ticker": ticker or "",
                "status": status or "",
                "severity": severity or "",
                "sort": sort,
                "direction": direction,
                "limit": limit,
                "cursor": cursor or "",
                "as_of": as_of.isoformat() if as_of else "",
                "alert_type": alert_type or "",
                "source_type": source_type or "",
                "lifecycle_state": lifecycle_state or "",
            },
        ),
    )


@router.get("/setup-lifecycle/operations", response_class=HTMLResponse)
def setup_lifecycle_operations_page(request: Request, db: DbSession) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "setup_lifecycle_operations.html",
        {
            "active_nav": "setup-lifecycle",
            "operations": setup_lifecycle_operations(db=db),
            "diagnostics": setup_lifecycle_diagnostics(db=db),
        },
    )


@router.get("/runs/{run_id}/setup-lifecycle", response_class=HTMLResponse)
def run_setup_lifecycle(run_id: int, request: Request, db: DbSession) -> HTMLResponse:
    _require_run(db, run_id)
    filters = SetupLifecycleFilters(run_id=run_id)
    payload = SetupLifecycleQueryService().changes(
        db,
        SetupLifecycleListQuery(filters=filters),
    )
    payload["run_id"] = run_id
    return templates.TemplateResponse(
        request,
        "setup_lifecycle.html",
        _changes_template_context(
            payload=payload,
            diagnostics=setup_lifecycle_diagnostics(db=db),
            filters={
                "run_id": run_id,
                "quick_filter": "",
                "ticker": "",
                "sector": "",
                "setup_family": "",
                "lifecycle_state": "",
                "transition": "",
                "actionability": "",
                "confidence_min": None,
                "sort": "latest_event_time",
                "direction": "desc",
                "limit": 50,
                "cursor": "",
            },
        ),
    )


@router.get("/setup-lifecycle/export.csv")
def export_setup_lifecycle_csv(db: DbSession) -> Response:
    payload = setup_lifecycle_changes(db=db, limit=500)
    _enforce_export_payload_rows(payload, resource="setup lifecycle changes")
    return attachment_response(
        export_changes_csv(payload),
        media_type="text/csv",
        filename="setup_lifecycle_changes.csv",
    )


@router.get("/setup-lifecycle/export.json")
def export_setup_lifecycle_json(db: DbSession) -> Response:
    payload = setup_lifecycle_changes(db=db, limit=500)
    _enforce_export_payload_rows(payload, resource="setup lifecycle changes")
    return attachment_response(
        export_json(payload),
        media_type="application/json",
        filename="setup_lifecycle_changes.json",
    )


@router.get("/api/setup-lifecycle/changes")
def setup_lifecycle_changes(
    db: DbSession,
    run_id: int | None = None,
    ticker: str | None = None,
    sector: str | None = None,
    setup_family: str | None = None,
    lifecycle_state: str | None = None,
    transition: str | None = None,
    actionability: str | None = None,
    confidence_min: int | None = None,
    confidence_max: int | None = None,
    state_age_min: int | None = None,
    state_age_max: int | None = None,
    setup_score_min: float | None = None,
    setup_score_max: float | None = None,
    trigger_distance_min: float | None = None,
    trigger_distance_max: float | None = None,
    sector_rank_min: int | None = None,
    sector_rank_max: int | None = None,
    velocity_min: float | None = None,
    velocity_max: float | None = None,
    market_regime: str | None = None,
    warning_flag: str | None = None,
    sort: str = "latest_event_time",
    direction: str = "desc",
    limit: int = 50,
    cursor: str | None = None,
    as_of: date | None = None,
    source_type: str | None = None,
) -> dict[str, Any]:
    return _query_or_http(
        lambda: SetupLifecycleQueryService().changes(
            db,
            _list_query(
                ticker=ticker,
                run_id=run_id,
                sector=sector,
                setup_family=setup_family,
                lifecycle_state=lifecycle_state,
                transition=transition,
                actionability=actionability,
                confidence_min=confidence_min,
                confidence_max=confidence_max,
                state_age_min=state_age_min,
                state_age_max=state_age_max,
                setup_score_min=setup_score_min,
                setup_score_max=setup_score_max,
                trigger_distance_min=trigger_distance_min,
                trigger_distance_max=trigger_distance_max,
                sector_rank_min=sector_rank_min,
                sector_rank_max=sector_rank_max,
                velocity_min=velocity_min,
                velocity_max=velocity_max,
                market_regime=market_regime,
                warning_flag=warning_flag,
                as_of=as_of,
                source_type=source_type,
                sort=sort,
                direction=direction,
                limit=limit,
                cursor=cursor,
            ),
        )
    )


@router.get("/api/setup-lifecycle/tickers/{ticker}")
@router.get("/api/setup-lifecycle/tickers/{ticker}/timeline")
def setup_lifecycle_ticker_timeline(
    ticker: str,
    db: DbSession,
    timeframe: str = "1d",
    limit: int = 100,
) -> dict[str, Any]:
    return _query_or_http(
        lambda: SetupLifecycleQueryService().ticker_timeline(
            db,
            ticker=ticker,
            timeframe=timeframe,
            limit=limit,
        )
    )


@router.get("/api/setup-lifecycle/episodes/{episode_id}")
def setup_lifecycle_episode(episode_id: int, db: DbSession) -> dict[str, Any]:
    return _query_or_http(lambda: SetupLifecycleQueryService().episode_detail(db, episode_id))


@router.get("/api/setup-lifecycle/alerts")
def setup_lifecycle_alerts(
    db: DbSession,
    ticker: str | None = None,
    status: Annotated[str | None, Query(alias="status")] = None,
    severity: str | None = None,
    sort: str = "latest_event_time",
    direction: str = "desc",
    limit: int = 50,
    cursor: str | None = None,
    as_of: date | None = None,
    alert_type: str | None = None,
    source_type: str | None = None,
    lifecycle_state: str | None = None,
) -> dict[str, Any]:
    return _query_or_http(
        lambda: SetupLifecycleQueryService().alerts(
            db,
            SetupLifecycleListQuery(
                filters=SetupLifecycleFilters(
                    ticker=ticker,
                    alert_status=status,
                    alert_severity=severity,
                    as_of_date=as_of,
                    alert_type=alert_type,
                    source_type=source_type,
                    lifecycle_state=lifecycle_state,
                ),
                sort=sort,
                direction=direction,
                limit=limit,
                cursor=cursor,
            ),
        )
    )


@router.post("/api/setup-lifecycle/alerts/{alert_id}/acknowledge")
@unsafe_route(
    ROUTE_CLASS_PUBLIC_LOCAL,
    reason="mutates setup lifecycle alert acknowledgement state",
)
def acknowledge_setup_lifecycle_alert(alert_id: int, db: DbSession) -> dict[str, Any]:
    alert = SetupLifecycleAlertService().acknowledge_alert(db, alert_id)
    if alert is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "ALERT_NOT_FOUND", "message": "Alert was not found."},
        )
    db.commit()
    return {"id": alert.id, "review_status": alert.status, "status": alert.status}


@router.post("/api/setup-lifecycle/alerts/{alert_id}/dismiss")
@unsafe_route(
    ROUTE_CLASS_PUBLIC_LOCAL,
    reason="mutates setup lifecycle alert dismissal state",
)
def dismiss_setup_lifecycle_alert(alert_id: int, db: DbSession) -> dict[str, Any]:
    alert = SetupLifecycleAlertService().dismiss_alert(db, alert_id)
    if alert is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "ALERT_NOT_FOUND", "message": "Alert was not found."},
        )
    db.commit()
    return {"id": alert.id, "review_status": alert.status, "status": alert.status}


@router.post("/api/setup-lifecycle/run/{run_id}/evaluate")
@router.post("/api/setup-lifecycle/evaluate")
@router.post("/api/setup-lifecycle/evaluate-run")
@unsafe_route(
    ROUTE_CLASS_PUBLIC_LOCAL,
    reason="queues or runs setup lifecycle evaluation",
)
def evaluate_setup_lifecycle_run(
    db: DbSession,
    request: Request,
    run_id: int | None = None,
    async_: Annotated[bool, Query(alias="async")] = True,
) -> Any:
    payload_run_id = int((run_id or 0) or request.query_params.get("run_id") or 0)
    if payload_run_id <= 0:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_CONFIGURATION", "message": "run_id is required."},
        )
    _require_run(db, payload_run_id)
    if async_:
        job = enqueue_job(
            db,
            SETUP_LIFECYCLE_EVALUATE_RUN,
            {"run_id": payload_run_id, "requester": "api"},
            related_run_id=payload_run_id,
            request_key=f"setup-lifecycle:evaluate-run:{payload_run_id}",
        )
        db.commit()
        return JSONResponse(
            {
                "job_id": job.id,
                "run_id": payload_run_id,
                "status": job.status,
                "status_url": f"/api/setup-lifecycle/evaluations/{job.id}",
            },
            status_code=http_status.HTTP_202_ACCEPTED,
        )
    result = SetupLifecycleEvaluationService().evaluate_run(db, payload_run_id, requester="api")
    db.commit()
    return result.as_dict()


@router.post("/api/setup-lifecycle/replay")
@unsafe_route(
    ROUTE_CLASS_LOCAL_ADMIN,
    reason="can persist setup lifecycle replay output",
    local_admin_required=True,
)
def replay_setup_lifecycle(
    request: Request,
    db: DbSession,
    ticker: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    persist: bool = False,
    confirmation: str | None = None,
    reason: str | None = None,
    requester: str | None = None,
) -> dict[str, Any]:
    if persist:
        _require_persisted_replay_confirmation(
            confirmation=confirmation,
            reason=reason,
            requester=requester,
        )
    result = SetupLifecycleReplayService().replay(
        db,
        SetupLifecycleReplayRequest(
            ticker=ticker,
            date_from=date_from,
            date_to=date_to,
            persist=persist,
            requester=requester or (request.client.host if request.client else "api"),
        ),
    )
    if persist:
        db.commit()
    return result


def _require_persisted_replay_confirmation(
    *,
    confirmation: str | None,
    reason: str | None,
    requester: str | None,
) -> None:
    if confirmation != REPLAY_CONFIRMATION_PHRASE:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_CONFIGURATION",
                "message": f"Persisted replay requires confirmation={REPLAY_CONFIRMATION_PHRASE}.",
            },
        )
    if not reason or not requester:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_CONFIGURATION",
                "message": "Persisted replay requires reason and requester.",
            },
        )


@router.get("/api/setup-lifecycle/evaluations/{evaluation_id}")
def setup_lifecycle_evaluation(evaluation_id: int, db: DbSession) -> dict[str, Any]:
    return _query_or_http(lambda: SetupLifecycleQueryService().evaluation_run(db, evaluation_id))


@router.get("/api/setup-lifecycle/filter-options")
def setup_lifecycle_filter_options(db: DbSession) -> dict[str, Any]:
    return SetupLifecycleQueryService().filter_options(db)


@router.get("/api/setup-lifecycle/operations")
def setup_lifecycle_operations(db: DbSession) -> dict[str, Any]:
    return SetupLifecycleQueryService().operations(db)


@router.get("/api/setup-lifecycle/diagnostics")
def setup_lifecycle_diagnostics(db: DbSession) -> dict[str, Any]:
    return SetupLifecycleQueryService().diagnostics(db)


@router.get("/api/setup-lifecycle/changes/export.csv")
def export_setup_lifecycle_changes_csv(
    db: DbSession,
    run_id: int | None = None,
    as_of: date | None = None,
    ticker: str | None = None,
    sector: str | None = None,
    setup_family: str | None = None,
    lifecycle_state: str | None = None,
    transition: str | None = None,
    actionability: str | None = None,
    confidence_min: int | None = None,
    confidence_max: int | None = None,
    state_age_min: int | None = None,
    state_age_max: int | None = None,
    setup_score_min: float | None = None,
    setup_score_max: float | None = None,
    trigger_distance_min: float | None = None,
    trigger_distance_max: float | None = None,
    sector_rank_min: int | None = None,
    sector_rank_max: int | None = None,
    velocity_min: float | None = None,
    velocity_max: float | None = None,
    market_regime: str | None = None,
    warning_flag: str | None = None,
    source_type: str | None = None,
    sort: str = "latest_event_time",
    direction: str = "desc",
) -> Response:
    payload = setup_lifecycle_changes(
        db=db,
        run_id=run_id,
        as_of=as_of,
        ticker=ticker,
        sector=sector,
        setup_family=setup_family,
        lifecycle_state=lifecycle_state,
        transition=transition,
        actionability=actionability,
        confidence_min=confidence_min,
        confidence_max=confidence_max,
        state_age_min=state_age_min,
        state_age_max=state_age_max,
        setup_score_min=setup_score_min,
        setup_score_max=setup_score_max,
        trigger_distance_min=trigger_distance_min,
        trigger_distance_max=trigger_distance_max,
        sector_rank_min=sector_rank_min,
        sector_rank_max=sector_rank_max,
        velocity_min=velocity_min,
        velocity_max=velocity_max,
        market_regime=market_regime,
        warning_flag=warning_flag,
        source_type=source_type,
        sort=sort,
        direction=direction,
        limit=500,
    )
    _enforce_export_payload_rows(payload, resource="setup lifecycle changes")
    return attachment_response(
        export_changes_csv(payload),
        media_type="text/csv",
        filename="setup_lifecycle_changes.csv",
    )


@router.get("/api/setup-lifecycle/changes/export.json")
def export_setup_lifecycle_changes_json(
    db: DbSession,
    run_id: int | None = None,
    as_of: date | None = None,
    ticker: str | None = None,
    sector: str | None = None,
    setup_family: str | None = None,
    lifecycle_state: str | None = None,
    transition: str | None = None,
    actionability: str | None = None,
    confidence_min: int | None = None,
    confidence_max: int | None = None,
    state_age_min: int | None = None,
    state_age_max: int | None = None,
    setup_score_min: float | None = None,
    setup_score_max: float | None = None,
    trigger_distance_min: float | None = None,
    trigger_distance_max: float | None = None,
    sector_rank_min: int | None = None,
    sector_rank_max: int | None = None,
    velocity_min: float | None = None,
    velocity_max: float | None = None,
    market_regime: str | None = None,
    warning_flag: str | None = None,
    source_type: str | None = None,
    sort: str = "latest_event_time",
    direction: str = "desc",
) -> Response:
    payload = setup_lifecycle_changes(
        db=db,
        run_id=run_id,
        as_of=as_of,
        ticker=ticker,
        sector=sector,
        setup_family=setup_family,
        lifecycle_state=lifecycle_state,
        transition=transition,
        actionability=actionability,
        confidence_min=confidence_min,
        confidence_max=confidence_max,
        state_age_min=state_age_min,
        state_age_max=state_age_max,
        setup_score_min=setup_score_min,
        setup_score_max=setup_score_max,
        trigger_distance_min=trigger_distance_min,
        trigger_distance_max=trigger_distance_max,
        sector_rank_min=sector_rank_min,
        sector_rank_max=sector_rank_max,
        velocity_min=velocity_min,
        velocity_max=velocity_max,
        market_regime=market_regime,
        warning_flag=warning_flag,
        source_type=source_type,
        sort=sort,
        direction=direction,
        limit=500,
    )
    _enforce_export_payload_rows(payload, resource="setup lifecycle changes")
    return attachment_response(
        export_json(payload),
        media_type="application/json",
        filename="setup_lifecycle_changes.json",
    )


@router.get("/api/setup-lifecycle/alerts/export.csv")
def export_setup_lifecycle_alerts_csv(
    db: DbSession,
    as_of: date | None = None,
    ticker: str | None = None,
    status: str | None = None,
    severity: str | None = None,
    alert_type: str | None = None,
    source_type: str | None = None,
    lifecycle_state: str | None = None,
    sort: str = "latest_event_time",
    direction: str = "desc",
) -> Response:
    payload = setup_lifecycle_alerts(
        db=db,
        as_of=as_of,
        ticker=ticker,
        status=status,
        severity=severity,
        alert_type=alert_type,
        source_type=source_type,
        lifecycle_state=lifecycle_state,
        sort=sort,
        direction=direction,
        limit=500,
    )
    _enforce_export_payload_rows(payload, resource="setup lifecycle alerts")
    return attachment_response(
        export_alerts_csv(payload),
        media_type="text/csv",
        filename="setup_lifecycle_alerts.csv",
    )


@router.get("/api/setup-lifecycle/alerts/export.json")
def export_setup_lifecycle_alerts_json(
    db: DbSession,
    as_of: date | None = None,
    ticker: str | None = None,
    status: str | None = None,
    severity: str | None = None,
    alert_type: str | None = None,
    source_type: str | None = None,
    lifecycle_state: str | None = None,
    sort: str = "latest_event_time",
    direction: str = "desc",
) -> Response:
    payload = setup_lifecycle_alerts(
        db=db,
        as_of=as_of,
        ticker=ticker,
        status=status,
        severity=severity,
        alert_type=alert_type,
        source_type=source_type,
        lifecycle_state=lifecycle_state,
        sort=sort,
        direction=direction,
        limit=500,
    )
    _enforce_export_payload_rows(payload, resource="setup lifecycle alerts")
    return attachment_response(
        export_json(payload),
        media_type="application/json",
        filename="setup_lifecycle_alerts.json",
    )


@router.get("/api/setup-lifecycle/episodes/{episode_id}/export.csv")
def export_setup_lifecycle_episode_csv(episode_id: int, db: DbSession) -> Response:
    payload = setup_lifecycle_episode(episode_id=episode_id, db=db)
    return attachment_response(
        export_episodes_csv({"items": [payload["episode"]]}),
        media_type="text/csv",
        filename=f"setup_lifecycle_episode_{episode_id}.csv",
    )


@router.get("/api/setup-lifecycle/episodes/{episode_id}/export.json")
def export_setup_lifecycle_episode_json(episode_id: int, db: DbSession) -> Response:
    payload = setup_lifecycle_episode(episode_id=episode_id, db=db)
    return attachment_response(
        export_json(payload),
        media_type="application/json",
        filename=f"setup_lifecycle_episode_{episode_id}.json",
    )


@router.get("/api/setup-lifecycle/operations/export.json")
def export_setup_lifecycle_operations_json(db: DbSession) -> Response:
    return attachment_response(
        export_json(setup_lifecycle_operations(db=db)),
        media_type="application/json",
        filename="setup_lifecycle_operations.json",
    )


def _list_query(
    *,
    ticker: str | None,
    run_id: int | None,
    sector: str | None,
    setup_family: str | None,
    lifecycle_state: str | None,
    transition: str | None,
    actionability: str | None,
    confidence_min: int | None,
    confidence_max: int | None,
    state_age_min: int | None,
    state_age_max: int | None,
    setup_score_min: float | None,
    setup_score_max: float | None,
    trigger_distance_min: float | None,
    trigger_distance_max: float | None,
    sector_rank_min: int | None,
    sector_rank_max: int | None,
    velocity_min: float | None,
    velocity_max: float | None,
    market_regime: str | None,
    warning_flag: str | None,
    as_of: date | None,
    source_type: str | None,
    sort: str,
    direction: str,
    limit: int,
    cursor: str | None,
) -> SetupLifecycleListQuery:
    return SetupLifecycleListQuery(
        filters=SetupLifecycleFilters(
            ticker=ticker,
            run_id=run_id,
            sector=sector,
            setup_family=setup_family,
            lifecycle_state=lifecycle_state,
            transition=transition,
            actionability=actionability,
            confidence_min=confidence_min,
            confidence_max=confidence_max,
            state_age_min=state_age_min,
            state_age_max=state_age_max,
            setup_score_min=setup_score_min,
            setup_score_max=setup_score_max,
            trigger_distance_min=trigger_distance_min,
            trigger_distance_max=trigger_distance_max,
            sector_rank_min=sector_rank_min,
            sector_rank_max=sector_rank_max,
            velocity_min=velocity_min,
            velocity_max=velocity_max,
            market_regime=market_regime,
            warning_flag=warning_flag,
            as_of_date=as_of,
            source_type=source_type,
        ),
        sort=sort,
        direction=direction,
        limit=limit,
        cursor=cursor,
    )


def _query_or_http(factory):
    try:
        return factory()
    except SetupLifecycleQueryError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc


def _enforce_export_payload_rows(payload: dict[str, Any], *, resource: str) -> None:
    settings = get_settings()
    row_count = len(payload.get("items") or [])
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


def _changes_template_context(
    *,
    payload: dict[str, Any],
    diagnostics: dict[str, Any],
    filters: dict[str, Any],
) -> dict[str, Any]:
    items = list(payload.get("items", []))
    dates = sorted(
        {item.get("effective_date") for item in items if item.get("effective_date")},
        reverse=True,
    )
    aggregate = dict(payload.get("summary") or {})
    export_query = urlencode(
        {key: value for key, value in filters.items() if value not in (None, "", False, 0)}
    )
    summary = {
        "selected_date": filters.get("as_of")
        or (dates[0] if dates else diagnostics.get("latest_canonical_date")),
        "comparison_date": next(
            (item.get("comparison_date") for item in items if item.get("comparison_date")),
            None,
        ),
        "missing_session_gap": diagnostics.get("stale_lease_count", 0),
        "total": payload.get("total", 0),
        **aggregate,
    }
    summary.setdefault("low_confidence_share", diagnostics.get("low_confidence_share", 0))
    return {
        "active_nav": "setup-lifecycle",
        "payload": payload,
        "items": items,
        "filters": filters,
        "diagnostics": diagnostics,
        "summary": summary,
        "quick_filters": _quick_filters(filters.get("quick_filter", "")),
        "state_tones": _STATE_TONES,
        "actionability_tones": _ACTIONABILITY_TONES,
        "confidence_tones": _CONFIDENCE_TONES,
        "pagination": _pagination(payload, filters, "/setup-lifecycle"),
        "export_query": export_query,
    }


def _ticker_template_context(payload: dict[str, Any]) -> dict[str, Any]:
    episodes = list(payload.get("episodes", []))
    active = [episode for episode in episodes if episode.get("status") == "ACTIVE"]
    primary = next((episode for episode in active if episode.get("is_primary")), None)
    if primary is None and active:
        primary = sorted(active, key=lambda item: item.get("primary_rank") or 999)[0]
    snapshots = list(payload.get("snapshots", []))
    return {
        "active_nav": "setup-lifecycle",
        "payload": payload,
        "primary_episode": primary,
        "secondary_episodes": [episode for episode in active if episode is not primary],
        "latest_snapshot": snapshots[0] if snapshots else None,
        "previous_snapshot": snapshots[1] if len(snapshots) > 1 else None,
        "timeline": _ticker_timeline_items(payload),
        "state_tones": _STATE_TONES,
        "actionability_tones": _ACTIONABILITY_TONES,
        "confidence_tones": _CONFIDENCE_TONES,
    }


def _episode_template_context(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "active_nav": "setup-lifecycle",
        "payload": payload,
        "episode": payload.get("episode") or {},
        "snapshots": payload.get("snapshots") or [],
        "lifecycle_events": payload.get("lifecycle_events") or [],
        "signal_changes": payload.get("signal_changes") or [],
        "state_tones": _STATE_TONES,
        "actionability_tones": _ACTIONABILITY_TONES,
        "confidence_tones": _CONFIDENCE_TONES,
    }


def _alerts_template_context(
    *,
    payload: dict[str, Any],
    filters: dict[str, Any],
) -> dict[str, Any]:
    items = list(payload.get("items", []))
    aggregate = dict(payload.get("summary") or {})
    export_query = urlencode(
        {key: value for key, value in filters.items() if value not in (None, "", False, 0)}
    )
    return {
        "active_nav": "setup-lifecycle-alerts",
        "payload": payload,
        "alerts": items,
        "filters": filters,
        "summary": aggregate,
        "severity_tones": _SEVERITY_TONES,
        "pagination": _pagination(payload, filters, "/setup-lifecycle/alerts"),
        "export_query": export_query,
    }


def _quick_filters(active: str) -> list[dict[str, str | bool]]:
    definitions = {
        "newly-ready": {"label": "Newly Ready", "lifecycle_state": "READY"},
        "newly-triggered": {"label": "Newly Triggered", "lifecycle_state": "TRIGGERED"},
        "improving-fast": {"label": "Improving Fast", "sort": "velocity"},
        "failed-today": {"label": "Failed Today", "lifecycle_state": "FAILED"},
        "extended": {"label": "Extended", "lifecycle_state": "EXTENDED"},
        "gate-blocked": {"label": "Gate Blocked", "actionability": "BLOCKED"},
        "low-confidence": {"label": "Low Confidence", "confidence_min": 0},
        "no-material-change": {"label": "No Material Change", "transition": "NO_MATERIAL_CHANGE"},
    }
    return [
        {
            "key": key,
            "label": str(value["label"]),
            "href": "/setup-lifecycle?" + urlencode({"quick_filter": key}),
            "active": key == active,
        }
        for key, value in definitions.items()
    ]


def _quick_filter_values(key: str) -> dict[str, Any]:
    if key == "newly-ready":
        return {"transition": "TO_READY"}
    if key == "newly-triggered":
        return {"transition": "TO_TRIGGERED"}
    if key == "improving-fast":
        return {"sort": "velocity", "velocity_min": 0.5}
    if key == "failed-today":
        return {"transition": "TO_FAILED"}
    if key == "extended":
        return {"transition": "TO_EXTENDED"}
    if key == "gate-blocked":
        return {"actionability": "BLOCKED"}
    if key == "low-confidence":
        return {"confidence_max": 69}
    if key == "no-material-change":
        return {"transition": "NO_MATERIAL_CHANGE"}
    return {}


def _pagination(
    payload: dict[str, Any],
    filters: dict[str, Any],
    base_path: str,
) -> dict[str, str | None]:
    query = {
        key: value
        for key, value in filters.items()
        if value not in (None, "", False) and key != "cursor"
    }
    next_cursor = payload.get("next_cursor")
    next_url = None
    if next_cursor:
        next_url = f"{base_path}?{urlencode({**query, 'cursor': next_cursor})}"
    return {"next_url": next_url}


def _ticker_timeline_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in payload.get("lifecycle_events") or []:
        rows.append(
            {
                "anchor_id": f"lifecycle-event-{event.get('id')}",
                "kind": "Lifecycle",
                "date": event.get("effective_date"),
                "title": event.get("event_type"),
                "state": event.get("to_state"),
                "detail": event.get("to_phase"),
                "payload": event,
            }
        )
    for change in payload.get("signal_changes") or []:
        rows.append(
            {
                "anchor_id": f"signal-change-{change.get('id')}",
                "kind": "Signal",
                "date": change.get("effective_date"),
                "title": change.get("signal_key"),
                "state": change.get("direction"),
                "detail": change.get("threshold_name"),
                "payload": change,
            }
        )
    for alert in payload.get("alerts") or []:
        rows.append(
            {
                "anchor_id": f"alert-{alert.get('id')}",
                "kind": "Alert",
                "date": alert.get("effective_date"),
                "title": alert.get("severity"),
                "state": alert.get("status"),
                "detail": ", ".join(alert.get("reason_codes") or []),
                "payload": alert,
            }
        )
    return sorted(rows, key=lambda item: item.get("date") or "", reverse=True)


_STATE_TONES = {
    "READY": "success",
    "TRIGGERED": "success",
    "CONFIRMED": "success",
    "EXTENDED": "warning",
    "FAILED": "danger",
    "EXPIRED": "muted",
}
_ACTIONABILITY_TONES = {
    "ACTIONABLE": "success",
    "WATCH_ONLY": "muted",
    "BLOCKED": "danger",
    "LOW_CONFIDENCE": "warning",
}
_CONFIDENCE_TONES = {
    "HIGH": "success",
    "NORMAL": "muted",
    "LOW": "warning",
    "INSUFFICIENT": "danger",
}
_SEVERITY_TONES = {
    "ACTIONABLE": "success",
    "RISK": "danger",
    "INFO": "muted",
    "NOTABLE": "warning",
}


def _require_run(db: Session, run_id: int) -> None:
    exists = db.scalar(select(UploadRun.id).where(UploadRun.id == run_id).limit(1))
    if exists is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "RUN_LIFECYCLE_NOT_FOUND", "message": "Run was not found."},
        )


def _date_value(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None
