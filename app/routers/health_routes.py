from fastapi import APIRouter

from app.models.schemas import HealthResponse
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
