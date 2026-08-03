from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.tables import MarketRegimeSnapshot, UploadRun
from app.routers.export_responses import attachment_response
from app.security import ROUTE_CLASS_PUBLIC_LOCAL, unsafe_route
from app.services.market_regime_command_center import MarketRegimeCommandCenterService
from app.services.market_regime_export_service import (
    export_snapshot_csv,
    export_snapshot_json,
    history_to_payload,
    snapshot_to_payload,
)
from app.services.market_regime_repository import MarketRegimeRepository
from app.services.ranking_profile_service import get_ranking_profiles
from app.templates import templates

router = APIRouter(tags=["market-regime"])
DbSession = Annotated[Session, Depends(get_db)]

WARNING_EXPLANATIONS = {
    "spy_distribution": {
        "title": "SPY distribution",
        "description": "Broad market selling pressure is elevated.",
        "behavior": "Reduce new long exposure and favor high-quality pullbacks.",
    },
    "qqq_distribution": {
        "title": "QQQ distribution",
        "description": "Growth and technology risk appetite is weakening.",
        "behavior": "Be careful with momentum and Early Rocket setups.",
    },
    "missing_spy_market_data": {
        "title": "Missing SPY data",
        "description": "The primary market proxy is unavailable.",
        "behavior": "Treat market guidance as low confidence.",
    },
    "missing_qqq_market_data": {
        "title": "Missing QQQ data",
        "description": "The risk proxy is unavailable.",
        "behavior": "Lower confidence for growth-stock setups.",
    },
    "market_risk_off": {
        "title": "Market risk-off",
        "description": "The broad market is defensive or hostile.",
        "behavior": "Avoid or strongly reduce new long entries.",
    },
    "low_market_confidence": {
        "title": "Low market confidence",
        "description": "Market context has missing or weak input quality.",
        "behavior": "Verify market data before acting.",
    },
    "stale_market_data": {
        "title": "Stale market data",
        "description": "The latest market bar is older than the freshness window.",
        "behavior": "Refresh market data before relying on the regime.",
    },
    "severely_stale_market_data": {
        "title": "Severely stale market data",
        "description": "Market data is too old for confident guidance.",
        "behavior": "Treat market state as unavailable until refreshed.",
    },
}


@router.get("/market-regime", response_class=HTMLResponse)
def market_regime_page(request: Request, db: DbSession) -> HTMLResponse:
    snapshot = _latest_or_calculate(db)
    return templates.TemplateResponse(
        request,
        "market_regime.html",
        _snapshot_template_context(
            snapshot=snapshot,
            title="Market Regime Command Center",
            history=MarketRegimeRepository().history(db, limit=30),
        ),
    )


@router.get("/runs/{run_id}/market-regime", response_class=HTMLResponse)
def run_market_regime_page(
    request: Request,
    run_id: int,
    db: DbSession,
) -> HTMLResponse:
    _require_run(db, run_id)
    snapshot = _run_snapshot_or_calculate(db, run_id)
    return templates.TemplateResponse(
        request,
        "market_regime.html",
        _snapshot_template_context(
            snapshot=snapshot,
            title=f"Run {run_id} Market Regime",
            history=MarketRegimeRepository().history(db, limit=30),
        ),
    )


@router.get("/api/market-regime/latest")
def latest_market_regime_api(db: DbSession) -> dict:
    return snapshot_to_payload(_latest_snapshot_or_404(db))


@router.get("/api/market-regime/history")
def market_regime_history_api(db: DbSession, limit: int = 30) -> list[dict]:
    return history_to_payload(MarketRegimeRepository().history(db, limit=limit))


@router.get("/api/market-regime/run/{run_id}")
def run_market_regime_api(run_id: int, db: DbSession) -> dict:
    _require_run(db, run_id)
    snapshot = MarketRegimeRepository().latest_for_run(db, run_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="No market regime snapshot exists for run.")
    return snapshot_to_payload(snapshot)


@router.post("/api/market-regime/run/{run_id}/recalculate")
@unsafe_route(ROUTE_CLASS_PUBLIC_LOCAL, reason="recalculates persisted market regime snapshot")
def recalculate_run_market_regime_api(run_id: int, db: DbSession) -> dict:
    _require_run(db, run_id)
    try:
        MarketRegimeCommandCenterService().build_snapshot(db, run_id=run_id)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise

    snapshot = MarketRegimeRepository().latest_for_run(db, run_id)
    if snapshot is None:
        raise HTTPException(status_code=500, detail="Recalculation did not create a snapshot.")
    return snapshot_to_payload(snapshot)


@router.get("/market-regime/export.json")
def export_latest_market_regime_json(db: DbSession) -> Response:
    snapshot = _latest_snapshot_or_404(db)
    return _json_export_response(
        export_snapshot_json(snapshot),
        filename="market_regime_latest.json",
    )


@router.get("/market-regime/export.csv")
def export_latest_market_regime_csv(db: DbSession) -> Response:
    snapshot = _latest_snapshot_or_404(db)
    return _csv_export_response(
        export_snapshot_csv(snapshot),
        filename="market_regime_latest.csv",
    )


@router.get("/runs/{run_id}/market-regime/export.json")
def export_run_market_regime_json(run_id: int, db: DbSession) -> Response:
    _require_run(db, run_id)
    snapshot = _run_snapshot_or_404(db, run_id)
    return _json_export_response(
        export_snapshot_json(snapshot),
        filename=f"swinglens_run_{run_id}_market_regime.json",
    )


@router.get("/runs/{run_id}/market-regime/export.csv")
def export_run_market_regime_csv(run_id: int, db: DbSession) -> Response:
    _require_run(db, run_id)
    snapshot = _run_snapshot_or_404(db, run_id)
    return _csv_export_response(
        export_snapshot_csv(snapshot),
        filename=f"swinglens_run_{run_id}_market_regime.csv",
    )


def _latest_or_calculate(db: Session) -> MarketRegimeSnapshot:
    snapshot = MarketRegimeRepository().latest(db)
    if snapshot is not None:
        return snapshot

    try:
        MarketRegimeCommandCenterService().build_snapshot(db)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return _latest_snapshot_or_404(db)


def _run_snapshot_or_calculate(db: Session, run_id: int) -> MarketRegimeSnapshot:
    snapshot = MarketRegimeRepository().latest_for_run(db, run_id)
    if snapshot is not None:
        return snapshot

    try:
        MarketRegimeCommandCenterService().build_snapshot(db, run_id=run_id)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return _run_snapshot_or_404(db, run_id)


def _latest_snapshot_or_404(db: Session) -> MarketRegimeSnapshot:
    snapshot = MarketRegimeRepository().latest(db)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="No market regime snapshot exists.")
    return snapshot


def _run_snapshot_or_404(db: Session, run_id: int) -> MarketRegimeSnapshot:
    snapshot = MarketRegimeRepository().latest_for_run(db, run_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="No market regime snapshot exists for run.")
    return snapshot


def _require_run(db: Session, run_id: int) -> None:
    exists = db.scalar(select(UploadRun.id).where(UploadRun.id == run_id))
    if exists is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} was not found.")


def _json_export_response(content: str, filename: str) -> Response:
    return attachment_response(
        content,
        media_type="application/json",
        filename=filename,
    )


def _csv_export_response(content: str, filename: str) -> Response:
    return attachment_response(
        content,
        media_type="text/csv",
        filename=filename,
    )


def _snapshot_template_context(
    snapshot: MarketRegimeSnapshot,
    title: str,
    history: list[MarketRegimeSnapshot],
) -> dict:
    payload = snapshot_to_payload(snapshot)
    return {
        "active_nav": "market-regime",
        "title": title,
        "snapshot": payload,
        "history": history_to_payload(history),
        "profile_alignment": _profile_alignment(payload["policy"]),
        "warning_explanations": [
            {**WARNING_EXPLANATIONS[warning], "code": warning}
            for warning in payload["warnings"]
            if warning in WARNING_EXPLANATIONS
        ],
    }


def _profile_alignment(policy: dict) -> list[dict[str, str]]:
    try:
        profiles = get_ranking_profiles()
    except Exception:
        profiles = []

    if not profiles:
        names = sorted(
            set(policy["preferred_profiles"])
            | set(policy["allowed_profiles"])
            | set(policy["reduced_profiles"])
            | set(policy["blocked_profiles"])
        )
        return [
            _profile_alignment_row(name=name, label=name.replace("_", " ").title(), policy=policy)
            for name in names
        ]

    return [
        _profile_alignment_row(name=profile.name, label=profile.label, policy=policy)
        for profile in profiles
    ]


def _profile_alignment_row(name: str, label: str, policy: dict) -> dict[str, str]:
    if name in policy["preferred_profiles"]:
        return {"name": name, "label": label, "status": "Preferred", "tone": "success"}
    if name in policy["blocked_profiles"]:
        return {"name": name, "label": label, "status": "Blocked", "tone": "danger"}
    if name in policy["reduced_profiles"]:
        return {"name": name, "label": label, "status": "Reduced", "tone": "warning"}
    if name in policy["allowed_profiles"]:
        return {"name": name, "label": label, "status": "Allowed", "tone": "muted"}
    return {"name": name, "label": label, "status": "Unavailable", "tone": "muted"}
