from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db import engine
from app.models.schemas import HealthResponse, ReadinessResponse
from app.settings import get_settings

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        app=settings.app_name,
        status="ok",
        database_configured=bool(settings.database_url),
        ib_host=settings.ib_host,
        ib_port=settings.ib_port,
    )


@router.get("/ready", response_model=ReadinessResponse)
def ready() -> ReadinessResponse:
    settings = get_settings()
    checks = {
        "database": "not checked",
        "local_dirs": "not checked",
    }

    database_ok = True
    try:
        with engine.connect() as connection:
            connection.execute(text("select 1"))
        checks["database"] = "ok"
    except SQLAlchemyError as exc:
        database_ok = False
        checks["database"] = str(exc)

    local_dirs_ok = True
    for directory in (settings.upload_dir, settings.export_dir, settings.cache_dir):
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            local_dirs_ok = False
            checks["local_dirs"] = f"{directory}: {exc}"
            break
    if local_dirs_ok:
        checks["local_dirs"] = "ok"

    return ReadinessResponse(
        app=settings.app_name,
        status="ok" if database_ok and local_dirs_ok else "degraded",
        database_ok=database_ok,
        local_dirs_ok=local_dirs_ok,
        checks=checks,
    )
