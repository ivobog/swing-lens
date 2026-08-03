from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.db import engine, get_db
from app.models.schemas import HealthResponse, ReadinessResponse
from app.security import ROUTE_CLASS_LOCAL_ADMIN, require_local_admin, unsafe_route
from app.services.cleanup_service import execute_cleanup, preview_cleanup
from app.services.operational_metrics import operational_metrics
from app.services.readiness_service import ReadinessService
from app.settings import get_settings

router = APIRouter(tags=["health"])
DbSession = Annotated[Session, Depends(get_db)]


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
    report = ReadinessService(engine=engine, settings=settings).report()

    return ReadinessResponse(
        app=settings.app_name,
        status=report.status,
        database_ok=report.database_ok,
        local_dirs_ok=report.local_dirs_ok,
        migrations_ok=report.checks["migrations"].ok,
        worker_ok=report.checks["worker"].ok,
        jobs_ok=report.checks["jobs"].ok,
        checks=report.response_checks(),
    )


@router.get("/metrics")
def metrics() -> Response:
    return Response(
        content=operational_metrics.as_prometheus(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@router.get("/ops/cleanup/preview")
def cleanup_preview(db: DbSession) -> dict:
    return preview_cleanup(db, get_settings())


@router.post("/ops/cleanup/execute")
@unsafe_route(
    ROUTE_CLASS_LOCAL_ADMIN,
    reason="deletes rebuildable local artifacts and old terminal background jobs",
    local_admin_required=True,
)
def cleanup_execute(request: Request, db: DbSession) -> dict:
    require_local_admin(
        request,
        enabled=True,
        disabled_message="Cleanup execution is disabled.",
        local_only_message="Cleanup execution is local only.",
    )
    try:
        report = execute_cleanup(db, get_settings())
        db.commit()
    except Exception:
        db.rollback()
        raise
    return report
