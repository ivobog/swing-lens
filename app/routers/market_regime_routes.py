from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.tables import MarketRegimeSnapshot, UploadRun
from app.services.market_regime_command_center import MarketRegimeCommandCenterService
from app.services.market_regime_export_service import (
    export_snapshot_csv,
    export_snapshot_json,
    history_to_payload,
    snapshot_to_payload,
)
from app.services.market_regime_repository import MarketRegimeRepository

router = APIRouter(tags=["market-regime"])
DbSession = Annotated[Session, Depends(get_db)]


@router.get("/market-regime", response_class=HTMLResponse)
def market_regime_page(request: Request, db: DbSession) -> HTMLResponse:
    snapshot = _latest_or_calculate(db)
    return HTMLResponse(_render_snapshot_html(snapshot, "Market Regime Command Center"))


@router.get("/runs/{run_id}/market-regime", response_class=HTMLResponse)
def run_market_regime_page(
    request: Request,
    run_id: int,
    db: DbSession,
) -> HTMLResponse:
    _require_run(db, run_id)
    snapshot = _run_snapshot_or_calculate(db, run_id)
    return HTMLResponse(_render_snapshot_html(snapshot, f"Run {run_id} Market Regime"))


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


def _render_snapshot_html(snapshot: MarketRegimeSnapshot, title: str) -> str:
    safe_title = _html_text(title)
    regime = _html_text(snapshot.regime)
    risk_state = _html_text(snapshot.risk_state)
    score = _html_text(str(snapshot.score))
    confidence = _html_text(snapshot.confidence)
    action = _html_text(snapshot.action_summary)
    return (
        "<!doctype html>"
        f"<html><head><title>{safe_title}</title></head>"
        "<body>"
        "<main>"
        f"<h1>{safe_title}</h1>"
        f"<p><strong>Regime:</strong> {regime}</p>"
        f"<p><strong>Risk state:</strong> {risk_state}</p>"
        f"<p><strong>Score:</strong> {score}</p>"
        f"<p><strong>Confidence:</strong> {confidence}</p>"
        f"<p><strong>Action:</strong> {action}</p>"
        "</main>"
        "</body></html>"
    )


def _json_export_response(content: str, filename: str) -> Response:
    return Response(
        content,
        media_type="application/json",
        headers={"content-disposition": f'attachment; filename="{filename}"'},
    )


def _csv_export_response(content: str, filename: str) -> Response:
    return Response(
        content,
        media_type="text/csv",
        headers={"content-disposition": f'attachment; filename="{filename}"'},
    )


def _html_text(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
