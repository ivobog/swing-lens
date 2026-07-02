from typing import Annotated

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.tables import UploadRun
from app.services.upload_service import UploadProcessingError, create_upload_run
from app.settings import get_settings
from app.templates import templates

router = APIRouter(tags=["upload"])
DbSession = Annotated[Session, Depends(get_db)]
CsvUpload = Annotated[UploadFile, File(...)]


def _recent_runs(db: Session) -> list[UploadRun]:
    return list(db.scalars(select(UploadRun).order_by(UploadRun.uploaded_at.desc()).limit(5)).all())


@router.get("/", response_class=HTMLResponse)
def upload_page(request: Request, db: DbSession) -> HTMLResponse:
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "upload.html",
        {
            "settings": settings,
            "ib_status": "Not tested",
            "recent_runs": _recent_runs(db),
            "error": None,
        },
    )


@router.post("/uploads", response_class=HTMLResponse)
def upload_csv(
    request: Request,
    file: CsvUpload,
    db: DbSession,
):
    try:
        run = create_upload_run(db, file)
    except UploadProcessingError as exc:
        settings = get_settings()
        return templates.TemplateResponse(
            request,
            "upload.html",
            {
                "settings": settings,
                "ib_status": "Not tested",
                "recent_runs": _recent_runs(db),
                "error": str(exc),
            },
            status_code=400,
        )

    return RedirectResponse(url=f"/runs/{run.id}", status_code=303)
