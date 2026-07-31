from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.tables import UploadRun
from app.services.background_job_service import enqueue_job
from app.services.winner_probability.job_handlers import WINNER_PREDICTION_CAPTURE

router = APIRouter(tags=["winner-probability"])
DbSession = Annotated[Session, Depends(get_db)]

LOCAL_ADMIN_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


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
