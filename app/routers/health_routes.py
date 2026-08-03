from fastapi import APIRouter, Response

from app.db import engine
from app.models.schemas import HealthResponse, ReadinessResponse
from app.services.operational_metrics import operational_metrics
from app.services.readiness_service import ReadinessService
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
