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
    settings, health, calls = configure_stubs(monkeypatch, disposable_postgres_database)
    try:
        with Session(engine) as db:
            assert (
                MarketSectorIntelligenceCoverageService().build(db)["freshness"]["status"]
                == "STALE"
            )
            result = service.enqueue_sector_refresh(db, settings=settings)
            db.commit()
        connect_phases = []

        def observe_connection():
            with Session(engine) as observer:
                job = observer.get(BackgroundJob, result["job_id"])
                connect_phases.append(service.sector_refresh_status(observer, job)["phase"])

        health.on_connect = observe_connection
        final = execute_one(engine)
        assert final["status"] == "COMPLETED", [
            (item["ticker"], item["what_to_show"], item["error_message"])
            for item in final["items"]
            if item["status"] == "FAILED"
        ]
        assert final["completed_items"] == final["total_items"] == 26
        assert len(calls) == 26 and len(set(calls)) == 26
        assert health.acquisition_client_ids == [30]
        assert connect_phases == ["FETCHING"]
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


def test_native_refresh_preserves_pipeline_spy_qqq_pit_manifests_during_revision(
    disposable_postgres_database, monkeypatch
):
    from dataclasses import replace
    from datetime import UTC, datetime
    from time import sleep

    from app.models.tables import MarketCalculationContext, PipelineRun, UploadRun
    from app.services import ib_fetch_executor
    from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
    from app.services.market_calculation_context_service import create_pipeline_market_context
    from app.services.price_bar_repository import load_price_bars_frame

    engine, _ = prepared_database(disposable_postgres_database)
    settings, _, _ = configure_stubs(monkeypatch, disposable_postgres_database)
    try:
        with Session(engine) as db:
            admitted = service.enqueue_sector_refresh(db, settings=settings, request_id="initial")
            db.commit()
        assert execute_one(engine)["status"] == "COMPLETED"
        with Session(engine, expire_on_commit=False) as db:
            upload = UploadRun(filename="frozen-pipeline.csv", status="COMPLETED")
            db.add(upload)
            db.flush()
            pipeline = PipelineRun(id=46, upload_run_id=upload.id, status="RUNNING")
            db.add(pipeline)
            db.flush()
            cutoff = create_pipeline_market_context(db, pipeline, cutoff_at=datetime.now(UTC))
            db.commit()
            pipeline_before = {
                column.name: getattr(pipeline, column.name)
                for column in PipelineRun.__table__.columns
            }
            context = db.get(MarketCalculationContext, cutoff.context_id)
            context_before = {
                column.name: getattr(context, column.name)
                for column in MarketCalculationContext.__table__.columns
            }

        def frozen():
            with Session(engine) as db:
                frames = {
                    (symbol, basis): load_price_bars_frame(
                        db,
                        symbol,
                        basis,
                        max_session=cutoff.latest_completed_session,
                        as_of=cutoff.cutoff_at,
                        calculation_context_id=cutoff.context_id,
                    )
                    for symbol in ("SPY", "QQQ")
                    for basis in ("TRADES", "ADJUSTED_LAST")
                }
                return {
                    key: (
                        frame.to_dict("records"),
                        Canonical.fingerprint(frame.attrs["pit_source_manifest"]),
                    )
                    for key, frame in frames.items()
                }

        before = frozen()
        original = ib_fetch_executor.fetch_daily_bars

        def revised(*args, **kwargs):
            values = original(*args, **kwargs)
            return [
                replace(bar, close=bar.close + 7, high=bar.high + 7)
                if bar.ticker in {"SPY", "QQQ"}
                else bar
                for bar in values
            ]

        monkeypatch.setattr(ib_fetch_executor, "fetch_daily_bars", revised)
        with Session(engine) as db:
            created = db.get(BackgroundJob, admitted["job_id"]).created_at
        remaining = service.RETRY_SECONDS - (datetime.now(UTC) - created).total_seconds()
        if remaining > 0:
            sleep(remaining)
        with Session(engine) as db:
            service.enqueue_sector_refresh(db, settings=settings, request_id="revision")
            db.commit()
        # Real native acquisition commits revisions while another Session reads
        # the pipeline's immutable cutoff, including its exact source manifests.
        with ThreadPoolExecutor(max_workers=1) as pool:
            refresh = pool.submit(execute_one, engine)
            while not refresh.done():
                assert frozen() == before
            assert refresh.result()["status"] == "COMPLETED"
        assert frozen() == before
        with Session(engine) as db:
            pipeline = db.get(PipelineRun, 46)
            context = db.get(MarketCalculationContext, cutoff.context_id)
            assert {
                column.name: getattr(pipeline, column.name)
                for column in PipelineRun.__table__.columns
            } == pipeline_before
            assert {
                column.name: getattr(context, column.name)
                for column in MarketCalculationContext.__table__.columns
            } == context_before
            for symbol, basis in before:
                current = load_price_bars_frame(db, symbol, basis)
                assert current.iloc[-1]["close"] == before[symbol, basis][0][-1]["close"] + 7
    finally:
        engine.dispose()


def test_expiry_after_broker_wait_preserves_original_plan_and_sends_zero_requests(
    disposable_postgres_database, monkeypatch
):
    from copy import deepcopy
    from time import monotonic, sleep

    from sqlalchemy.orm import sessionmaker

    from app.services.background_worker import run_worker_once
    from app.services.ib_acquisition_gate import HistoricalSlot
    from app.services.ib_fetch_job_service import execute_durable_fetch_job
    from app.services.us_market_calendar import next_us_trading_day

    engine, expected = prepared_database(disposable_postgres_database)
    settings, _, calls = configure_stubs(monkeypatch, disposable_postgres_database)
    holder = HistoricalSlot(engine, settings)
    try:
        with Session(engine) as db:
            admitted = service.enqueue_sector_refresh(
                db, settings=settings, request_id="expires-in-wait"
            )
            db.commit()
            plan = deepcopy(db.get(BackgroundJob, admitted["job_id"]).payload_json)
        holder.acquire_historical_slot()
        with ThreadPoolExecutor(max_workers=1) as pool:
            worker = pool.submit(
                run_worker_once,
                worker_id="expiry-sector",
                operation_profile="sector_refresh",
                stale_after_seconds=90,
                heartbeat_interval_seconds=0.2,
                session_factory=sessionmaker(bind=engine),
                handlers={"IB_FETCH": execute_durable_fetch_job},
            )
            deadline = monotonic() + 60
            try:
                while monotonic() < deadline:
                    with Session(engine) as db:
                        if (
                            db.get(BackgroundJob, admitted["job_id"]).progress_stage
                            == "WAITING_FOR_IB_ACQUISITION"
                        ):
                            break
                    sleep(0.05)
                else:
                    pytest.fail("sector worker did not reach broker wait")
                monkeypatch.setattr(
                    "app.services.ib_fetch_plan_service.latest_completed_us_trading_day",
                    lambda: next_us_trading_day(expected),
                )
            finally:
                holder.release_historical_slot()
            assert worker.result(60)
        assert calls == []
        with Session(engine) as db:
            job = db.get(BackgroundJob, admitted["job_id"])
            assert job.status == "FAILED" and job.payload_json == plan
            assert "REVIEW_SCOPE_EXPIRED" in job.error_message
            assert db.get(IBFetchRun, admitted["fetch_run_id"]).status == "FAILED"
    finally:
        holder.release_historical_slot()
        engine.dispose()
