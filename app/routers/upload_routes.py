from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.settings import get_settings
from app.templates import templates

router = APIRouter(tags=["upload"])


@router.get("/", response_class=HTMLResponse)
def upload_page(request: Request) -> HTMLResponse:
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "upload.html",
        {
            "settings": settings,
            "ib_status": "Not tested",
        },
    )
