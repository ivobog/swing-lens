"""Disposable browser server only. Never import this module from the application.

Uses actual routes, PIT readers and engines; synthetic cache and explicit test-only
D2 captures. No application DB, provider, worker, lifespan or operational runtime.
"""

from collections import Counter
from contextvars import ContextVar
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import Mock, patch

import psutil
from evidence_output import output_directory
from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from market_sector_technical_helpers import CUTOFF
from sqlalchemy import JSON, Column, MetaData, Table, bindparam, create_engine, event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session
from test_market_sector_intelligence_rotation_storage import apply_migration, capture
from test_market_sector_intelligence_technical_pit import seed_cache

from app.db import get_db
from app.main import create_app
from app.models.tables import PriceBar, PriceBarRevision, UploadRun
from app.routers import market_sector_intelligence_routes as routes
from app.services.market_clock_service import MarketClockService
from app.services.market_sector_intelligence_chart_service import MarketSectorChartService
from app.services.market_sector_intelligence_config import load_market_sector_intelligence_config
from app.services.market_sector_intelligence_coverage import MarketSectorIntelligenceCoverageService
from app.services.market_sector_intelligence_rotation_service import MarketSectorRotationService
from app.services.market_sector_intelligence_technical_service import MarketSectorTechnicalService
from app.services.us_market_calendar import us_market_session
from app.settings import Settings


def create_fixture_app():
    evidence = output_directory("market-sector-intelligence-d4")
    temporary = TemporaryDirectory(prefix="browser-fixture-", dir=evidence)
    directory = Path(temporary.name).resolve()
    assert directory.parent == evidence
    mode = ContextVar("d4_fixture_mode", default="ready")
    engines = {}
    sql_counts, requests, observations = Counter(), Counter(), []
    guards = []
    for name in ("ready", "empty", "partial", "qqq", "unactivated"):
        engine = create_engine(
            "sqlite:///" + (directory / (name + ".sqlite3")).as_posix(),
            connect_args={"check_same_thread": False},
        )
        engines[name] = engine
        if name != "unactivated":
            apply_migration(engine)
        if name == "empty":
            for model in (PriceBar, PriceBarRevision, UploadRun):
                Table(
                    model.__tablename__,
                    MetaData(),
                    *[
                        Column(
                            column.name,
                            JSON() if isinstance(column.type, JSONB) else column.type,
                            primary_key=column.primary_key,
                        )
                        for column in model.__table__.columns
                    ],
                ).create(engine)
        else:
            seed_cache(engine)
            with engine.begin() as connection:
                dates = connection.scalars(select(PriceBar.bar_date).distinct()).all()
                connection.execute(
                    PriceBar.__table__.update()
                    .where(PriceBar.bar_date == bindparam("wanted_session"))
                    .values(
                        created_at=bindparam("receipt"),
                        first_seen_at=bindparam("receipt"),
                        last_seen_at=bindparam("receipt"),
                    ),
                    [
                        {
                            "wanted_session": day,
                            "receipt": us_market_session(day).close_at + timedelta(minutes=5),
                        }
                        for day in dates
                    ],
                )
                if name in {"partial", "qqq"}:
                    connection.execute(
                        PriceBar.__table__.delete().where(
                            PriceBar.ticker == ("XLK" if name == "partial" else "QQQ"),
                            PriceBar.bar_date == CUTOFF.latest_completed_session,
                        )
                    )
    for day in (16, 19, 21):
        stamp = datetime(2026, 10, day, 20, 30, tzinfo=UTC)
        cutoff = MarketClockService().cutoff_for(stamp, reason="D4_DISPOSABLE_CAPTURE")
        with Session(engines["ready"]) as db:
            preview = MarketSectorRotationService().preview(db, cutoff=cutoff)
        assert capture(engines["ready"], preview, now=stamp)["status"] == "CREATED"

    def track(_connection, _cursor, sql, *_args):
        sql_counts[sql.lstrip().split()[0].upper()] += 1

    for engine in engines.values():
        event.listen(engine, "before_cursor_execute", track)

    app = create_app(
        Settings(
            _env_file=None,
            debug=False,
            job_worker_enabled=False,
            observability_metrics_enabled=False,
        )
    )

    def database(request: Request):
        name = request.cookies.get("d4_fixture", "ready")
        with Session(
            engines.get(name, engines["empty"] if name == "disabled" else engines["ready"])
        ) as db:
            yield db

    app.dependency_overrides[get_db] = database
    original_config = load_market_sector_intelligence_config
    guards.append(
        patch.object(
            routes,
            "load_market_sector_intelligence_config",
            lambda: replace(original_config(), enabled=mode.get() != "disabled"),
        )
    )
    class FixtureClock(MarketClockService):
        def cutoff_for(self, _stamp, *, reason):
            return CUTOFF

    clock = FixtureClock()
    for attr, service in (
        ("MarketSectorIntelligenceCoverageService", MarketSectorIntelligenceCoverageService),
        ("MarketSectorRotationService", MarketSectorRotationService),
        ("MarketSectorTechnicalService", MarketSectorTechnicalService),
        ("MarketSectorChartService", MarketSectorChartService),
    ):
        guards.append(patch.object(routes, attr, lambda service=service: service(clock=clock)))
    forbidden = Mock(side_effect=AssertionError("Browser GET attempted mutation/provider work"))
    for target in (
        "ib_insync.IB.connect",
        "app.services.background_job_service.enqueue_job",
        "app.services.technical_artifact_cache.upsert_local_artifact",
        "app.services.market_calculation_context_service.create_pipeline_market_context",
        "app.services.technical_score_service.build_technical_score",
        "app.services.market_sector_intelligence_rotation_repository.MarketSectorRotationRepository.capture",
    ):
        guards.append(patch(target, forbidden))
    for guard in guards:
        guard.start()

    @app.middleware("http")
    async def fixture_context(request, call_next):
        token = mode.set(request.cookies.get("d4_fixture", "ready"))
        start = perf_counter()
        try:
            if request.url.path.startswith("/api/market-sector-intelligence/"):
                requests[request.url.path] += 1
                if mode.get() == "unavailable" and request.url.path.endswith("/technicals/preview"):
                    return JSONResponse(
                        {"detail": {"code": "FIXTURE_UNAVAILABLE"}}, status_code=503
                    )
            response = await call_next(request)
            observations.append(
                {
                    "path": request.url.path,
                    "status": response.status_code,
                    "seconds": perf_counter() - start,
                    "bytes": response.headers.get("content-length"),
                }
            )
            return response
        finally:
            mode.reset(token)

    @app.get("/__fixture__/mode/{name}")
    def select_mode(name: str):
        if name not in {*engines, "disabled", "unavailable"}:
            return JSONResponse({"code": "UNKNOWN_FIXTURE"}, status_code=400)
        response = RedirectResponse("/sector-rotation")
        response.set_cookie("d4_fixture", name, samesite="strict")
        return response

    @app.get("/__fixture__/metrics")
    def metrics():
        return {
            "sql": dict(sql_counts),
            "requests": dict(requests),
            "observations": observations[-100:],
            "rss_bytes": psutil.Process().memory_info().rss,
            "forbidden_calls": forbidden.call_count,
        }

    def close():
        for guard in reversed(guards):
            guard.stop()
        for engine in engines.values():
            engine.dispose()
        assert Path(temporary.name).resolve().parent == evidence
        temporary.cleanup()

    return app, close


if __name__ == "__main__":
    import uvicorn

    app, close = create_fixture_app()
    try:
        uvicorn.run(app, host="127.0.0.1", port=8768, lifespan="off", log_level="warning")
    finally:
        close()
