import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from threading import Event, Thread

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routers import gui_routes, health_routes, ib_routes, run_routes, upload_routes
from app.services.background_worker import run_worker
from app.settings import Settings, get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    worker_settings: Settings = app.state.settings
    worker_stop_event: Event | None = None
    worker_thread: Thread | None = None

    if worker_settings.job_worker_enabled:
        worker_stop_event = Event()
        worker_thread = Thread(
            target=run_worker,
            kwargs={
                "settings": worker_settings,
                "stop_event": worker_stop_event,
            },
            name=f"swinglens-{worker_settings.job_worker_id}",
            daemon=True,
        )
        worker_thread.start()
        logger.info(
            "job.worker.started",
            extra={"worker_id": worker_settings.job_worker_id},
        )

    try:
        yield
    finally:
        if worker_stop_event is not None:
            worker_stop_event.set()
        if worker_thread is not None:
            worker_thread.join(timeout=5)
            logger.info(
                "job.worker.stopped",
                extra={"worker_id": worker_settings.job_worker_id},
            )


def create_app(app_settings: Settings | None = None) -> FastAPI:
    app_settings = app_settings or settings
    app = FastAPI(
        title=app_settings.app_name,
        debug=app_settings.debug,
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = app_settings
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    app.include_router(health_routes.router)
    app.include_router(upload_routes.router)
    app.include_router(run_routes.router)
    app.include_router(gui_routes.router)
    app.include_router(ib_routes.router)
    return app


app = create_app()
