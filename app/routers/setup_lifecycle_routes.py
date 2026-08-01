from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi import status as http_status
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.tables import UploadRun
from app.services.background_job_service import enqueue_job
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

router = APIRouter(tags=["setup-lifecycle"])
DbSession = Annotated[Session, Depends(get_db)]


@router.get("/setup-lifecycle")
def setup_lifecycle_page(db: DbSession) -> dict[str, Any]:
    return setup_lifecycle_changes(db=db)


@router.get("/setup-lifecycle/ticker/{ticker}")
def setup_lifecycle_ticker_page(ticker: str, db: DbSession) -> dict[str, Any]:
    return setup_lifecycle_ticker_timeline(ticker=ticker, db=db)


@router.get("/setup-lifecycle/episodes/{episode_id}")
def setup_lifecycle_episode_page(episode_id: int, db: DbSession) -> dict[str, Any]:
    return setup_lifecycle_episode(episode_id=episode_id, db=db)


@router.get("/setup-lifecycle/alerts")
def setup_lifecycle_alerts_page(db: DbSession) -> dict[str, Any]:
    return setup_lifecycle_alerts(db=db)


@router.get("/runs/{run_id}/setup-lifecycle")
def run_setup_lifecycle(run_id: int, db: DbSession) -> dict[str, Any]:
    _require_run(db, run_id)
    filters = SetupLifecycleFilters(run_id=run_id)
    payload = SetupLifecycleQueryService().changes(
        db,
        SetupLifecycleListQuery(filters=filters),
    )
    payload["run_id"] = run_id
    return payload


@router.get("/setup-lifecycle/export.csv")
def export_setup_lifecycle_csv(db: DbSession) -> Response:
    payload = setup_lifecycle_changes(db=db, limit=500)
    return Response(
        export_changes_csv(payload),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="setup_lifecycle_changes.csv"'},
    )


@router.get("/setup-lifecycle/export.json")
def export_setup_lifecycle_json(db: DbSession) -> Response:
    payload = setup_lifecycle_changes(db=db, limit=500)
    return Response(
        export_json(payload),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="setup_lifecycle_changes.json"'},
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
) -> dict[str, Any]:
    return _query_or_http(
        lambda: SetupLifecycleQueryService().alerts(
            db,
            SetupLifecycleListQuery(
                filters=SetupLifecycleFilters(
                    ticker=ticker,
                    alert_status=status,
                    alert_severity=severity,
                ),
                sort=sort,
                direction=direction,
                limit=limit,
                cursor=cursor,
            ),
        )
    )


@router.post("/api/setup-lifecycle/alerts/{alert_id}/acknowledge")
def acknowledge_setup_lifecycle_alert(alert_id: int, db: DbSession) -> dict[str, Any]:
    alert = SetupLifecycleAlertService().acknowledge_alert(db, alert_id)
    if alert is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "ALERT_NOT_FOUND", "message": "Alert was not found."},
        )
    db.commit()
    return {"id": alert.id, "status": alert.status}


@router.post("/api/setup-lifecycle/alerts/{alert_id}/dismiss")
def dismiss_setup_lifecycle_alert(alert_id: int, db: DbSession) -> dict[str, Any]:
    alert = SetupLifecycleAlertService().dismiss_alert(db, alert_id)
    if alert is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "ALERT_NOT_FOUND", "message": "Alert was not found."},
        )
    db.commit()
    return {"id": alert.id, "status": alert.status}


@router.post("/api/setup-lifecycle/run/{run_id}/evaluate")
@router.post("/api/setup-lifecycle/evaluate")
@router.post("/api/setup-lifecycle/evaluate-run")
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
def replay_setup_lifecycle(
    db: DbSession,
    ticker: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    persist: bool = False,
) -> dict[str, Any]:
    result = SetupLifecycleReplayService().replay(
        db,
        SetupLifecycleReplayRequest(
            ticker=ticker,
            date_from=date_from,
            date_to=date_to,
            persist=persist,
            requester="api",
        ),
    )
    if persist:
        db.commit()
    return result


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
def export_setup_lifecycle_changes_csv(db: DbSession) -> Response:
    payload = setup_lifecycle_changes(db=db, limit=500)
    return Response(export_changes_csv(payload), media_type="text/csv")


@router.get("/api/setup-lifecycle/changes/export.json")
def export_setup_lifecycle_changes_json(db: DbSession) -> Response:
    payload = setup_lifecycle_changes(db=db, limit=500)
    return Response(export_json(payload), media_type="application/json")


@router.get("/api/setup-lifecycle/alerts/export.csv")
def export_setup_lifecycle_alerts_csv(db: DbSession) -> Response:
    payload = setup_lifecycle_alerts(db=db, limit=500)
    return Response(export_alerts_csv(payload), media_type="text/csv")


@router.get("/api/setup-lifecycle/alerts/export.json")
def export_setup_lifecycle_alerts_json(db: DbSession) -> Response:
    payload = setup_lifecycle_alerts(db=db, limit=500)
    return Response(export_json(payload), media_type="application/json")


@router.get("/api/setup-lifecycle/episodes/{episode_id}/export.csv")
def export_setup_lifecycle_episode_csv(episode_id: int, db: DbSession) -> Response:
    payload = setup_lifecycle_episode(episode_id=episode_id, db=db)
    return Response(
        export_episodes_csv({"items": [payload["episode"]]}),
        media_type="text/csv",
    )


@router.get("/api/setup-lifecycle/episodes/{episode_id}/export.json")
def export_setup_lifecycle_episode_json(episode_id: int, db: DbSession) -> Response:
    payload = setup_lifecycle_episode(episode_id=episode_id, db=db)
    return Response(export_json(payload), media_type="application/json")


@router.get("/api/setup-lifecycle/operations/export.json")
def export_setup_lifecycle_operations_json(db: DbSession) -> Response:
    return Response(
        export_json(setup_lifecycle_operations(db=db)),
        media_type="application/json",
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


def _require_run(db: Session, run_id: int) -> None:
    exists = db.scalar(select(UploadRun.id).where(UploadRun.id == run_id).limit(1))
    if exists is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "RUN_LIFECYCLE_NOT_FOUND", "message": "Run was not found."},
        )
