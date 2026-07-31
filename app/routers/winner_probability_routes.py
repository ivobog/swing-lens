from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.tables import UploadRun
from app.services.background_job_service import enqueue_job
from app.services.winner_probability.api_service import (
    WinnerProbabilityApiError,
    WinnerProbabilityApiService,
)
from app.services.winner_probability.dtos import (
    WinnerProbabilityApiQuery,
    WinnerProbabilityFilterError,
    WinnerProbabilityFilters,
)
from app.services.winner_probability.exports import (
    export_outcome_explorer_csv,
    export_reproduction_manifest_json,
    export_run_evidence_csv,
    export_run_evidence_json,
)
from app.services.winner_probability.job_handlers import (
    WINNER_COHORT_REFRESH,
    WINNER_OUTCOME_MATURATION,
    WINNER_PREDICTION_CAPTURE,
)
from app.services.winner_probability.operations_service import (
    WinnerProbabilityOperationsService,
)
from app.services.winner_probability.outcome_explorer_service import (
    OutcomeExplorerQuery,
    OutcomeExplorerService,
)
from app.services.winner_probability.reproduction_service import ReproductionService

router = APIRouter(tags=["winner-probability"])
DbSession = Annotated[Session, Depends(get_db)]

LOCAL_ADMIN_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


@router.get("/api/winner-probability/run/{run_id}")
def winner_probability_run(
    run_id: int,
    db: DbSession,
    outcome_definition_id: str | None = None,
    entry_model: str | None = None,
    horizon_sessions: int | None = None,
    as_of_date: date | None = None,
    training_cutoff: date | None = None,
    estimate_view: str = "DECISION_TIME",
    outcome_revision_view: str = "CURRENT",
    sort: str = "ticker",
    direction: str = "asc",
    cursor: str | None = None,
    page_size: int = 100,
    probability_min: float | None = None,
    lower_bound_min: float | None = None,
    interval_width_max: float | None = None,
    evidence_grade: str | None = None,
    sample_size_min: int | None = None,
    effective_sample_size_min: int | None = None,
    expected_return_min: float | None = None,
    median_return_min: float | None = None,
    mfe_min: float | None = None,
    mae_max: float | None = None,
    target_first_rate_min: float | None = None,
    setup_classification: str | None = None,
    setup_family: str | None = None,
    ranking_profile: str | None = None,
    market_regime: str | None = None,
    market_risk_state: str | None = None,
    sector_state: str | None = None,
    sector_rank_min: int | None = None,
    sector_rank_max: int | None = None,
    earnings_risk: str | None = None,
    data_quality: str | None = None,
    eligibility_status: str | None = None,
) -> dict:
    _require_run(db, run_id)
    return _call_api(
        lambda: WinnerProbabilityApiService().get_run_evidence(
            db,
            run_id=run_id,
            query=_api_query(
                outcome_definition_id=outcome_definition_id,
                entry_model=entry_model,
                horizon_sessions=horizon_sessions,
                as_of_date=as_of_date,
                training_cutoff=training_cutoff,
                estimate_view=estimate_view,
                outcome_revision_view=outcome_revision_view,
                sort=sort,
                direction=direction,
                cursor=cursor,
                page_size=page_size,
                filters=WinnerProbabilityFilters(
                    probability_min=probability_min,
                    lower_bound_min=lower_bound_min,
                    interval_width_max=interval_width_max,
                    evidence_grade=evidence_grade,
                    sample_size_min=sample_size_min,
                    effective_sample_size_min=effective_sample_size_min,
                    expected_return_min=expected_return_min,
                    median_return_min=median_return_min,
                    mfe_min=mfe_min,
                    mae_max=mae_max,
                    target_first_rate_min=target_first_rate_min,
                    setup_classification=setup_classification,
                    setup_family=setup_family,
                    ranking_profile=ranking_profile,
                    market_regime=market_regime,
                    market_risk_state=market_risk_state,
                    sector_state=sector_state,
                    sector_rank_min=sector_rank_min,
                    sector_rank_max=sector_rank_max,
                    earnings_risk=earnings_risk,
                    data_quality=data_quality,
                    eligibility_status=eligibility_status,
                ),
            ),
        )
    )


@router.get("/api/winner-probability/run/{run_id}/export.csv")
def export_winner_probability_run_csv(
    run_id: int,
    db: DbSession,
    estimate_view: str = "DECISION_TIME",
) -> Response:
    payload = winner_probability_run(run_id=run_id, db=db, estimate_view=estimate_view)
    return Response(
        content=export_run_evidence_csv(payload),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="swinglens_run_{run_id}_owpe.csv"'
        },
    )


@router.get("/api/winner-probability/run/{run_id}/export.json")
def export_winner_probability_run_json(
    run_id: int,
    db: DbSession,
    estimate_view: str = "DECISION_TIME",
) -> Response:
    payload = winner_probability_run(run_id=run_id, db=db, estimate_view=estimate_view)
    return Response(
        content=export_run_evidence_json(payload),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="swinglens_run_{run_id}_owpe.json"'
        },
    )


@router.get("/api/winner-probability/predictions/{prediction_id}")
def winner_probability_prediction(
    prediction_id: int,
    db: DbSession,
    outcome_definition_id: str | None = None,
    entry_model: str | None = None,
    horizon_sessions: int | None = None,
    as_of_date: date | None = None,
    estimate_view: str = "DECISION_TIME",
    outcome_revision_view: str = "CURRENT",
) -> dict:
    return _call_api(
        lambda: WinnerProbabilityApiService().get_prediction_detail(
            db,
            prediction_id=prediction_id,
            query=_api_query(
                outcome_definition_id=outcome_definition_id,
                entry_model=entry_model,
                horizon_sessions=horizon_sessions,
                as_of_date=as_of_date,
                estimate_view=estimate_view,
                outcome_revision_view=outcome_revision_view,
            ),
        )
    )


@router.get("/api/winner-probability/predictions/{prediction_id}/neighbors")
def winner_probability_neighbors(
    prediction_id: int,
    db: DbSession,
    outcome_definition_id: str | None = None,
    limit: int = 25,
) -> dict:
    return _call_api(
        lambda: WinnerProbabilityApiService().get_neighbors(
            db,
            prediction_id=prediction_id,
            query=_api_query(outcome_definition_id=outcome_definition_id),
            limit=limit,
        )
    )


@router.get("/api/winner-probability/tickers/{ticker}/history")
def winner_probability_ticker_history(
    ticker: str,
    db: DbSession,
    outcome_definition_id: str | None = None,
    estimate_view: str = "DECISION_TIME",
    page_size: int = 100,
) -> dict:
    return _call_api(
        lambda: WinnerProbabilityApiService().get_ticker_history(
            db,
            ticker=ticker,
            query=_api_query(
                outcome_definition_id=outcome_definition_id,
                estimate_view=estimate_view,
                page_size=page_size,
            ),
        )
    )


@router.get("/api/winner-probability/estimates/{estimate_id}/reproduction")
def winner_probability_estimate_reproduction(estimate_id: int, db: DbSession) -> dict:
    try:
        result = ReproductionService().reproduce_estimate(db, estimate_id=estimate_id)
    except ValueError as exc:
        raise _structured_http_error("ESTIMATE_NOT_FOUND", str(exc), status_code=404) from exc
    return {
        "estimate_id": result.estimate_id,
        "matches": result.matches,
        "mismatches": list(result.mismatches),
        "evidence_manifest_hash": result.evidence_manifest_hash,
        "point_probability": float(result.point_probability)
        if result.point_probability is not None
        else None,
        "sample_n": result.sample_n,
    }


@router.get("/api/winner-probability/estimates/{estimate_id}/reproduction/export.json")
def export_winner_probability_reproduction_json(
    estimate_id: int,
    db: DbSession,
) -> Response:
    payload = winner_probability_estimate_reproduction(estimate_id=estimate_id, db=db)
    return Response(
        content=export_reproduction_manifest_json(payload),
        media_type="application/json",
        headers={
            "Content-Disposition": (
                'attachment; filename='
                f'"winner_probability_estimate_{estimate_id}_reproduction.json"'
            )
        },
    )


@router.get("/api/winner-probability/outcomes/explorer")
def winner_probability_outcome_explorer(
    db: DbSession,
    segment_by: str = "setup_family",
    min_sample: int = 10,
    estimate_view: str = "DECISION_TIME",
) -> dict:
    return _call_api(
        lambda: OutcomeExplorerService().explorer_table(
            db,
            query=OutcomeExplorerQuery(
                segment_by=segment_by,
                min_sample=min_sample,
                api_query=_api_query(estimate_view=estimate_view),
            ),
        )
    )


@router.get("/api/winner-probability/outcomes/explorer/export.csv")
def export_winner_probability_outcome_explorer_csv(
    db: DbSession,
    segment_by: str = "setup_family",
    min_sample: int = 10,
) -> Response:
    payload = winner_probability_outcome_explorer(
        db=db,
        segment_by=segment_by,
        min_sample=min_sample,
    )
    return Response(
        content=export_outcome_explorer_csv(payload),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="winner_probability_explorer.csv"'},
    )


@router.get("/api/winner-probability/operations/status")
def winner_probability_operations_status(db: DbSession) -> dict:
    return WinnerProbabilityOperationsService().status(db)


@router.get("/api/winner-probability/models")
def winner_probability_models(db: DbSession) -> dict:
    return _call_api(lambda: WinnerProbabilityApiService().list_models(db))


@router.get("/api/winner-probability/models/{id}/calibration")
def winner_probability_model_calibration(id: int, db: DbSession) -> dict:
    return _call_api(lambda: WinnerProbabilityApiService().get_model_calibration(db, model_id=id))


@router.get("/api/winner-probability/models/{id}/drift")
def winner_probability_model_drift(id: int, db: DbSession) -> dict:
    return _call_api(lambda: WinnerProbabilityApiService().get_model_drift(db, model_id=id))


@router.post("/api/winner-probability/runs/{run_id}/capture")
def queue_winner_prediction_capture(
    request: Request,
    run_id: int,
    db: DbSession,
) -> dict:
    _require_local_admin(request)
    _require_run(db, run_id)
    try:
        job = enqueue_job(
            db,
            job_type=WINNER_PREDICTION_CAPTURE,
            payload={"run_id": run_id},
            related_run_id=run_id,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {
        "job_id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "run_id": run_id,
    }


@router.post("/api/winner-probability/outcomes/process")
def queue_winner_outcome_maturation(
    request: Request,
    db: DbSession,
    limit: int = 500,
) -> dict:
    _require_local_admin(request)
    if limit <= 0 or limit > 5000:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 5000.")
    try:
        job = enqueue_job(
            db,
            job_type=WINNER_OUTCOME_MATURATION,
            payload={"limit": limit},
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {
        "job_id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "limit": limit,
    }


@router.post("/api/winner-probability/cohorts/refresh")
def queue_winner_cohort_refresh(
    request: Request,
    db: DbSession,
    outcome_definition_id: str | None = None,
) -> dict:
    _require_local_admin(request)
    payload = {
        key: value
        for key, value in {"outcome_definition_id": outcome_definition_id}.items()
        if value is not None
    }
    try:
        job = enqueue_job(
            db,
            job_type=WINNER_COHORT_REFRESH,
            payload=payload,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {
        "job_id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "payload": payload,
    }


@router.post("/api/winner-probability/models/{id}/retire")
def retire_winner_probability_model(
    request: Request,
    id: int,
    db: DbSession,
    reason: str = "Retired through local OWPE API.",
) -> dict:
    _require_local_admin(request)
    try:
        payload = WinnerProbabilityApiService().retire_model(
            db,
            model_id=id,
            actor=request.client.host if request.client else "local",
            reason=reason,
        )
        db.commit()
    except WinnerProbabilityApiError as exc:
        db.rollback()
        raise _structured_http_error(exc.code, str(exc), status_code=exc.status_code) from exc
    except Exception:
        db.rollback()
        raise
    return payload


def _require_local_admin(request: Request) -> None:
    settings = getattr(request.app.state, "settings", None)
    if settings is None or not settings.winner_probability_admin_enabled:
        raise HTTPException(status_code=404, detail="Winner probability admin is disabled.")
    host = request.client.host if request.client is not None else None
    if host not in LOCAL_ADMIN_HOSTS:
        raise HTTPException(status_code=403, detail="Winner probability admin is local only.")


def _require_run(db: Session, run_id: int) -> None:
    exists = db.scalar(select(UploadRun.id).where(UploadRun.id == run_id))
    if exists is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} was not found.")


def _api_query(
    *,
    outcome_definition_id: str | None = None,
    entry_model: str | None = None,
    horizon_sessions: int | None = None,
    as_of_date: date | None = None,
    training_cutoff: date | None = None,
    estimate_view: str = "DECISION_TIME",
    outcome_revision_view: str = "CURRENT",
    sort: str = "ticker",
    direction: str = "asc",
    cursor: str | None = None,
    page_size: int = 100,
    filters: WinnerProbabilityFilters | None = None,
) -> WinnerProbabilityApiQuery:
    try:
        return WinnerProbabilityApiQuery(
            outcome_definition_id=outcome_definition_id,
            entry_model=entry_model,
            horizon_sessions=horizon_sessions,
            as_of_date=as_of_date,
            training_cutoff=training_cutoff,
            estimate_view=estimate_view,
            outcome_revision_view=outcome_revision_view,
            sort=sort,
            direction=direction,
            cursor=cursor,
            page_size=page_size,
            filters=filters or WinnerProbabilityFilters(),
        )
    except WinnerProbabilityFilterError as exc:
        raise _structured_http_error("INVALID_FILTER", str(exc), status_code=422) from exc


def _call_api(callback):
    try:
        return callback()
    except WinnerProbabilityFilterError as exc:
        raise _structured_http_error("INVALID_FILTER", str(exc), status_code=422) from exc
    except WinnerProbabilityApiError as exc:
        raise _structured_http_error(exc.code, str(exc), status_code=exc.status_code) from exc
    except ValueError as exc:
        raise _structured_http_error("INVALID_REQUEST", str(exc), status_code=422) from exc


def _structured_http_error(code: str, message: str, *, status_code: int) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})
