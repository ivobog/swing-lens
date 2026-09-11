import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import copy

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.routing import BaseRoute

from app.db import engine
from app.observability.correlation import CorrelationMiddleware
from app.observability.db_monitor import (
    DatabaseHealthSampler,
    DatabaseMonitorMiddleware,
)
from app.observability.metrics import operational_metrics
from app.observability.resource_sampler import ResourceSampler, SystemMetricsCollector
from app.routers import (
    ceri_provider_routes,
    ceri_routes,
    gui_routes,
    health_routes,
    ib_gateway_admin_routes,
    ib_market_intelligence_routes,
    ib_routes,
    market_data_routes,
    market_regime_routes,
    operations_routes,
    run_routes,
    sector_rotation_routes,
    setup_lifecycle_routes,
    upload_routes,
    winner_probability_routes,
)
from app.security import install_trusted_host_middleware, issue_local_admin_csrf_token
from app.settings import ProcessRole, Settings, get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


class SwingLensFastAPI(FastAPI):
    @property
    def routes(self) -> list[BaseRoute]:
        return _introspection_routes(self.router.routes)


def _introspection_routes(routes: list[BaseRoute]) -> list[BaseRoute]:
    flat_routes: list[BaseRoute] = []
    for route in routes:
        effective_contexts = getattr(route, "effective_route_contexts", None)
        if not callable(effective_contexts):
            flat_routes.append(route)
            continue
        for context in effective_contexts():
            original_route = context.original_route
            if context.starlette_route is not None:
                flat_routes.append(context.starlette_route)
            elif getattr(original_route, "path", None) == context.path:
                flat_routes.append(original_route)
            else:
                route_copy = copy(original_route)
                route_copy.path = context.path
                flat_routes.append(route_copy)
    return flat_routes


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    worker_settings: Settings = app.state.settings
    if worker_settings.process_role not in {
        ProcessRole.WEB,
        ProcessRole.CLI_OR_MAINTENANCE,
    }:
        raise RuntimeError(
            "FastAPI lifespan may run only in WEB (or an explicit test/maintenance process)"
        )
    operational_metrics.configure(enabled=worker_settings.observability_metrics_enabled)
    database_health_sampler = DatabaseHealthSampler(worker_settings)
    database_health_sampler.start()
    resource_sampler = None
    system_metrics = None
    if worker_settings.observability_metrics_enabled:
        resource_sampler = ResourceSampler(
            process_role="web",
            interval_seconds=worker_settings.observability_collection_interval_seconds,
        )
        system_metrics = SystemMetricsCollector(engine, worker_settings)
        resource_sampler.start()
        system_metrics.start()
    try:
        yield
    finally:
        if system_metrics is not None:
            system_metrics.stop()
        if resource_sampler is not None:
            resource_sampler.stop()
        database_health_sampler.stop()


def create_app(app_settings: Settings | None = None) -> FastAPI:
    app_settings = app_settings or settings
    app = SwingLensFastAPI(
        title=app_settings.app_name,
        debug=app_settings.debug,
        version=app_settings.application_version,
        lifespan=lifespan,
    )
    app.state.settings = app_settings
    app.state.local_admin_csrf_token = issue_local_admin_csrf_token()

    app.add_middleware(DatabaseMonitorMiddleware, enabled=app_settings.db_monitor_enabled)
    app.add_middleware(CorrelationMiddleware)
    install_trusted_host_middleware(app, app_settings.app_host)
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    app.include_router(health_routes.router)
    app.include_router(operations_routes.router)
    app.include_router(upload_routes.router)
    app.include_router(run_routes.router)
    app.include_router(market_regime_routes.router)
    app.include_router(market_data_routes.router)
    app.include_router(sector_rotation_routes.router)
    app.include_router(gui_routes.router)
    app.include_router(ib_routes.router)
    app.include_router(ib_gateway_admin_routes.router)
    app.include_router(ib_market_intelligence_routes.router)
    app.include_router(winner_probability_routes.router)
    app.include_router(setup_lifecycle_routes.router)
    app.include_router(ceri_routes.router)
    app.include_router(ceri_provider_routes.router)
    return app


app = create_app()
