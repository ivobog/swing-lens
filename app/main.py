from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routers import health_routes, ib_routes, run_routes, upload_routes
from app.settings import get_settings

settings = get_settings()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
        version="0.1.0",
    )
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    app.include_router(health_routes.router)
    app.include_router(upload_routes.router)
    app.include_router(run_routes.router)
    app.include_router(ib_routes.router)
    return app


app = create_app()
