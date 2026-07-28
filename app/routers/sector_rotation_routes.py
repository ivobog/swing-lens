from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.tables import SectorRotationSnapshot, UploadRun
from app.services.sector_rotation_export_service import (
    export_sector_rotation_csv,
    export_sector_rotation_json,
    export_sector_rotation_markdown,
    snapshot_to_payload,
)
from app.services.sector_rotation_repository import SectorRotationRepository
from app.services.sector_rotation_service import SectorRotationService

router = APIRouter(tags=["sector-rotation"])
DbSession = Annotated[Session, Depends(get_db)]


@router.get("/runs/{run_id}/sector-rotation", response_class=HTMLResponse)
def sector_rotation_dashboard(run_id: int, db: DbSession) -> HTMLResponse:
    _require_run(db, run_id)
    payload = _run_payload_or_calculate(db, run_id)
    snapshot = payload["snapshot"]
    rows = payload["rows"]
    body = [
        "<!doctype html><html><head><title>Sector Rotation</title></head><body>",
        f"<h1>Run {run_id} Sector Rotation</h1>",
        f"<p>Mode: {snapshot['mode']}</p>",
        "<table><thead><tr><th>Rank</th><th>Sector</th><th>State</th><th>Permission</th></tr></thead><tbody>",
    ]
    for row in rows:
        body.append(
            "<tr>"
            f"<td>{row['rank'] or ''}</td>"
            f"<td>{row['sector']}</td>"
            f"<td>{row['rotation_state']}</td>"
            f"<td>{row['permission']}</td>"
            "</tr>"
        )
    body.extend(["</tbody></table>", "</body></html>"])
    return HTMLResponse("".join(body))


@router.get("/runs/{run_id}/sector-rotation/{sector_slug}", response_class=HTMLResponse)
def sector_rotation_drilldown(run_id: int, sector_slug: str, db: DbSession) -> HTMLResponse:
    _require_run(db, run_id)
    payload = _run_payload_or_calculate(db, run_id)
    row = _payload_sector_row(payload, sector_slug)
    if row is None:
        raise HTTPException(status_code=404, detail="Sector was not found in this snapshot.")
    return HTMLResponse(
        "<!doctype html><html><head><title>Sector Rotation Drilldown</title></head><body>"
        f"<h1>{row['sector']} Sector Rotation</h1>"
        f"<p>State: {row['rotation_state']}</p>"
        f"<p>Permission: {row['permission']}</p>"
        "</body></html>"
    )


@router.get("/api/runs/{run_id}/sector-rotation")
def api_sector_rotation(run_id: int, db: DbSession) -> dict:
    _require_run(db, run_id)
    return _run_payload_or_calculate(db, run_id)


@router.get("/api/runs/{run_id}/sector-rotation/{sector_slug}")
def api_sector_rotation_drilldown(run_id: int, sector_slug: str, db: DbSession) -> dict:
    _require_run(db, run_id)
    payload = _run_payload_or_calculate(db, run_id)
    row = _payload_sector_row(payload, sector_slug)
    if row is None:
        raise HTTPException(status_code=404, detail="Sector was not found in this snapshot.")
    return {"snapshot": payload["snapshot"], "row": row}


@router.get("/api/sector-rotation/snapshots")
def api_sector_rotation_snapshots(
    db: DbSession,
    limit: int = 30,
    run_id: int | None = None,
) -> list[dict]:
    repo = SectorRotationRepository()
    return [
        snapshot_to_payload(snapshot, repo.get_snapshot_rows(db, snapshot.id))["snapshot"]
        for snapshot in repo.history(db, limit=limit, run_id=run_id)
    ]


@router.get("/api/sector-rotation/snapshots/{snapshot_id}")
def api_sector_rotation_snapshot(snapshot_id: int, db: DbSession) -> dict:
    snapshot = _snapshot_or_404(db, snapshot_id)
    repo = SectorRotationRepository()
    return snapshot_to_payload(snapshot, repo.get_snapshot_rows(db, snapshot.id))


@router.post("/api/runs/{run_id}/sector-rotation/recalculate")
def recalculate_run_sector_rotation_api(run_id: int, db: DbSession) -> dict:
    _require_run(db, run_id)
    try:
        dto = SectorRotationService().build_sector_rotation_snapshot(
            db,
            run_id=run_id,
            persist=True,
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise
    return snapshot_to_payload(dto)


@router.get("/runs/{run_id}/sector-rotation/export.csv")
def export_sector_rotation_dashboard_csv(run_id: int, db: DbSession) -> Response:
    _require_run(db, run_id)
    snapshot, rows = _run_snapshot_and_rows_or_404(db, run_id)
    return _attachment_response(
        export_sector_rotation_csv(snapshot, rows),
        media_type="text/csv",
        filename=f"swinglens_run_{run_id}_sector_rotation.csv",
    )


@router.get("/runs/{run_id}/sector-rotation/export.json")
def export_sector_rotation_dashboard_json(run_id: int, db: DbSession) -> Response:
    _require_run(db, run_id)
    snapshot, rows = _run_snapshot_and_rows_or_404(db, run_id)
    return _attachment_response(
        export_sector_rotation_json(snapshot, rows),
        media_type="application/json",
        filename=f"swinglens_run_{run_id}_sector_rotation.json",
    )


@router.get("/runs/{run_id}/sector-rotation/brief.md")
def export_sector_rotation_dashboard_markdown(run_id: int, db: DbSession) -> Response:
    _require_run(db, run_id)
    snapshot, rows = _run_snapshot_and_rows_or_404(db, run_id)
    return _attachment_response(
        export_sector_rotation_markdown(snapshot, rows),
        media_type="text/markdown",
        filename=f"swinglens_run_{run_id}_sector_rotation_brief.md",
    )


def _run_payload_or_calculate(db: Session, run_id: int) -> dict:
    repo = SectorRotationRepository()
    snapshot = repo.latest_for_run(db, run_id)
    if snapshot is not None:
        return snapshot_to_payload(snapshot, repo.get_snapshot_rows(db, snapshot.id))

    try:
        dto = SectorRotationService().build_sector_rotation_snapshot(
            db,
            run_id=run_id,
            persist=True,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return snapshot_to_payload(dto)


def _run_snapshot_and_rows_or_404(
    db: Session,
    run_id: int,
) -> tuple[SectorRotationSnapshot, list]:
    repo = SectorRotationRepository()
    snapshot = repo.latest_for_run(db, run_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="No sector rotation snapshot exists for run.")
    return snapshot, repo.get_snapshot_rows(db, snapshot.id)


def _snapshot_or_404(db: Session, snapshot_id: int) -> SectorRotationSnapshot:
    snapshot = db.get(SectorRotationSnapshot, snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Sector rotation snapshot was not found.")
    return snapshot


def _payload_sector_row(payload: dict, sector_slug: str) -> dict | None:
    return next(
        (row for row in payload["rows"] if row["sector_slug"] == sector_slug),
        None,
    )


def _require_run(db: Session, run_id: int) -> None:
    exists = db.scalar(select(UploadRun.id).where(UploadRun.id == run_id))
    if exists is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} was not found.")


def _attachment_response(content: str, media_type: str, filename: str) -> Response:
    return Response(
        content,
        media_type=media_type,
        headers={"content-disposition": f'attachment; filename="{filename}"'},
    )
