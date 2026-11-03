from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sector_refresh_helpers import configure_stubs, execute_one, prepared_database
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session

from app.db import get_db
from app.main import create_app
from app.models.tables import BackgroundJob, IBFetchRun, PriceBar
from app.services import market_sector_refresh_service as service
from app.services.ib_historical_request_scope import DAILY_BAR_ADMISSION_V3
from app.services.market_sector_intelligence_coverage import MarketSectorIntelligenceCoverageService


def test_preflight_failures_create_zero_jobs_runs_or_authorities(
    disposable_postgres_database, monkeypatch
):
    engine, _ = prepared_database(disposable_postgres_database)
    settings, health, _ = configure_stubs(monkeypatch, disposable_postgres_database)
    try:
        for fields in (
            {"status": "IB_PROCESS_NOT_RUNNING", "api_connected": False},
            {"status": "IB_PROCESS_RUNNING_API_NOT_READY", "api_ready": False},
            {"status": "IB_SESSION_LOST", "smoke_response_received": False},
            {"status": "IB_API_READY", "smoke_response_received": False},
        ):
            health.__dict__.update(
                status="IB_API_READY",
                api_connected=True,
                api_ready=True,
                smoke_response_received=True,
            )
            health.__dict__.update(fields)
            monkeypatch.setattr(service, "_last_probe", float("-inf"))
            with Session(engine) as db:
                with pytest.raises(service.SectorRefreshRejected, match="IB Gateway"):
                    service.enqueue_sector_refresh(db, settings=settings)
                db.rollback()
                assert db.scalar(select(func.count()).select_from(IBFetchRun)) == 0
                assert (
                    db.scalar(select(func.count()).select_from(BackgroundJob)) == 1
                )  # Only fenced history.
                assert (
                    db.execute(text("SELECT count(*) FROM acquisition_plan_records")).scalar() == 0
                )
    finally:
        engine.dispose()


def test_admission_failure_rolls_back_run_job_and_semantic_authority(
    disposable_postgres_database, monkeypatch
):
    engine, _ = prepared_database(disposable_postgres_database)
    settings, _, _ = configure_stubs(monkeypatch, disposable_postgres_database)
    app = create_app(settings)

    def database():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = database
    submit = Mock(side_effect=RuntimeError("Simulated enqueue interruption"))
    monkeypatch.setattr(service, "submit_fetch_job", submit)
    try:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/api/market-sector-intelligence/refresh",
            json={},
            headers={
                "origin": "http://testserver",
                "x-csrf-token": app.state.local_admin_csrf_token,
            },
        )
        assert response.status_code == 500
        submit.assert_called_once()
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(IBFetchRun)) == 0
            assert db.scalar(select(func.count()).select_from(BackgroundJob)) == 1
            assert db.execute(text("SELECT count(*) FROM acquisition_plan_records")).scalar() == 0
        # Status recovery is a read. It neither observes the fenced old job nor probes IB.
        probe = Mock(side_effect=AssertionError("GET attempted Gateway probe"))
        monkeypatch.setattr(service, "check_status", probe)
        assert client.get("/api/market-sector-intelligence/refresh/active").json() is None
        assert client.get("/api/market-sector-intelligence/refresh/183").status_code == 404
        probe.assert_not_called()
    finally:
        engine.dispose()


def test_concurrent_admission_coalesces_exact_native_job_and_preserves_fences(
    disposable_postgres_database, monkeypatch
):
    engine, _ = prepared_database(disposable_postgres_database)
    settings, health, _ = configure_stubs(monkeypatch, disposable_postgres_database)
    entered, release = Event(), Event()

    def probe(**_):
        entered.set()
        assert release.wait(10)
        return health

    monkeypatch.setattr(service, "check_status", Mock(side_effect=probe))

    def submit():
        with Session(engine) as db:
            result = service.enqueue_sector_refresh(db, settings=settings)
            db.commit()
            return result

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(submit)
            assert entered.wait(10)
            try:
                with pytest.raises(service.SectorRefreshRejected) as blocked:
                    submit()
                assert blocked.value.code == "REFRESH_ADMISSION_BUSY"
            finally:
                release.set()
            result = first.result(timeout=20)
        second = submit()
        assert second["job_id"] == result["job_id"] == 1642
        assert second["coalesced"] is True
        service.check_status.assert_called_once()
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(IBFetchRun)) == 1
            job = db.get(BackgroundJob, result["job_id"])
            plan = job.payload_json["fetch_plan"]
            assert set(plan["symbols_including_benchmarks"]) == {
                "XLB",
                "XLC",
                "XLE",
                "XLF",
                "XLI",
                "XLK",
                "XLP",
                "XLRE",
                "XLU",
                "XLV",
                "XLY",
                "SPY",
                "QQQ",
            }
            assert len(plan["items"]) == 26
            assert {row["required_bars"] for row in plan["items"]} == {756}
            assert {row["duration"] for row in plan["items"]} == {"4 Y"}
            assert {
                row["response_admission_policy"]
                for row in plan["items"]
                if row["what_to_show"] == "ADJUSTED_LAST"
            } == {DAILY_BAR_ADMISSION_V3}
            assert job.scope_id and job.refresh_cycle_id and job.acquisition_plan_id
            assert db.get(BackgroundJob, 183).status == "QUEUED"
    finally:
        engine.dispose()


def test_native_durable_worker_updates_real_cache_and_read_only_freshness(
    disposable_postgres_database, monkeypatch
):
    engine, expected = prepared_database(disposable_postgres_database)
    settings, _, calls = configure_stubs(monkeypatch, disposable_postgres_database)
    try:
        with Session(engine) as db:
            assert (
                MarketSectorIntelligenceCoverageService().build(db)["freshness"]["status"]
                == "STALE"
            )
            result = service.enqueue_sector_refresh(db, settings=settings)
            db.commit()
        final = execute_one(engine)
        assert final["status"] == "COMPLETED", [
            (item["ticker"], item["what_to_show"], item["error_message"])
            for item in final["items"]
            if item["status"] == "FAILED"
        ]
        assert final["completed_items"] == final["total_items"] == 26
        assert len(calls) == 26 and len(set(calls)) == 26
        statements = []

        def record(_c, _cur, sql, *_args):
            statements.append(sql.lstrip().split()[0].upper())

        event.listen(engine, "before_cursor_execute", record)
        with Session(engine) as db:
            db.add(
                PriceBar(
                    ticker="PENDING",
                    bar_date=expected,
                    timeframe="1 day",
                    what_to_show="TRADES",
                    source="SYNTHETIC",
                )
            )
            fresh = MarketSectorIntelligenceCoverageService().build(db)["freshness"]
            status = service.sector_refresh_status(
                db, service.find_sector_refresh(db, result["job_id"], settings=settings)
            )
            assert fresh["status"] == "CURRENT"
            assert status["status"] == "COMPLETED"
            assert {"INSERT", "UPDATE", "DELETE"}.isdisjoint(statements)
            db.rollback()
        event.remove(engine, "before_cursor_execute", record)
        with Session(engine) as db:
            assert db.scalar(select(func.max(PriceBar.bar_date))) == expected
            rows = db.scalars(select(PriceBar).where(PriceBar.bar_date == expected)).all()
            assert len(rows) == 26 and all(
                row.first_fetch_item_id and row.first_fetch_run_id for row in rows
            )
            assert all(row.source == "IB" for row in rows)
            assert db.get(BackgroundJob, 183).status == "QUEUED"
    finally:
        engine.dispose()
