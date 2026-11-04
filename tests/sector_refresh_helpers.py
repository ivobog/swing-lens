"""Synthetic data and provider doubles for task-owned PostgreSQL only."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.database_safety import assert_disposable_database, run_guarded_alembic_upgrade
from app.models.tables import BackgroundJob, IBContract, PriceBar
from app.services import ib_fetch_executor, market_sector_refresh_service
from app.services.background_job_service import claim_next_job, mark_job_completed
from app.services.background_worker import execute_job
from app.services.ib_data_fetcher import HistoricalBar
from app.services.ib_fetch_job_service import execute_durable_fetch_job
from app.services.market_clock_service import MarketClockService
from app.services.market_sector_intelligence_config import load_market_sector_intelligence_config
from app.services.us_market_calendar import next_us_trading_day, previous_us_trading_day
from app.services.worker_registry import register_worker
from app.settings import Settings


def prepared_database(url):
    assert_disposable_database(url)
    run_guarded_alembic_upgrade(Config("alembic.ini"), url)
    engine = create_engine(url)
    expected = (
        MarketClockService()
        .cutoff_for(datetime.now(UTC), reason="DISPOSABLE_REFRESH")
        .latest_completed_session
    )
    prior = previous_us_trading_day(expected)
    symbols = [row.etf_symbol for row in load_market_sector_intelligence_config().sectors] + [
        "SPY",
        "QQQ",
    ]
    with Session(engine) as db:
        for index, symbol in enumerate(symbols):
            db.add(
                IBContract(
                    ticker=symbol,
                    ib_conid=900000 + index,
                    symbol=symbol,
                    sec_type="STK",
                    exchange="SMART",
                    primary_exchange="NYSE",
                    currency="USD",
                    local_symbol=symbol,
                    trading_class=symbol,
                    resolution_status="RESOLVED",
                    last_resolved_at=datetime.now(UTC),
                )
            )
            for basis in market_sector_refresh_service.BASES:
                db.add(
                    PriceBar(
                        ticker=symbol,
                        bar_date=prior,
                        timeframe="1 day",
                        open=90,
                        high=92,
                        low=88,
                        close=90,
                        volume=1000,
                        source="SYNTHETIC_DISPOSABLE",
                        what_to_show=basis,
                        created_at=datetime.now(UTC) - timedelta(days=1),
                        first_seen_at=datetime.now(UTC) - timedelta(days=1),
                    )
                )
        # A matching historic job is deliberately below the deployment fence.
        db.add(
            BackgroundJob(
                id=183,
                job_type="IB_FETCH",
                status="QUEUED",
                payload_json={"msi_refresh": market_sector_refresh_service.MARKER},
            )
        )
        db.execute(text("SELECT setval('background_jobs_id_seq', 1641, true)"))
        db.commit()
    return engine, expected


def configure_stubs(monkeypatch, url):
    settings = Settings(
        _env_file=None,
        database_url=url,
        job_worker_enabled=False,
        job_worker_profile="sector_refresh",
        job_activation_min_id=1642,
        pipeline_activation_min_id=46,
        ib_max_retries=1,
        observability_metrics_enabled=False,
        ib_requests_per_minute=60,
        ib_intelligence_historical_requests_per_minute=60,
        ib_min_seconds_between_requests=0,
        ib_intelligence_historical_min_spacing_seconds=0,
        worker_memory_warning_mb=8192,
        worker_memory_critical_mb=12288,
    )
    for module in (
        "app.services.pipeline_execution_authority",
        "app.services.ib_fetch_job_service",
        "app.services.background_job_service",
        "app.services.ib_fetch_executor",
    ):
        monkeypatch.setattr(module + ".get_settings", lambda: settings)
    health = SimpleNamespace(
        status="IB_API_READY",
        api_connected=True,
        api_ready=True,
        smoke_response_received=True,
        acquisition_client_ids=[],
    )
    monkeypatch.setattr(market_sector_refresh_service, "check_status", lambda **_: health)
    monkeypatch.setattr(market_sector_refresh_service, "_last_probe", float("-inf"))

    class FakeIB:
        connected = False

        def connect(self, *_a, **_kw):
            health.acquisition_client_ids.append(_kw["clientId"])
            if hasattr(health, "on_connect"):
                health.on_connect()
            self.connected = True

        def disconnect(self):
            self.connected = False

        def isConnected(self):  # noqa: N802
            return self.connected

    class NoWait:
        def __init__(self, *_args):
            pass

        def wait_before_request(self, *_args):
            return True

        def backoff_after_error(self, *_a):
            return True

    calls = []

    def bars(_ib, contract, basis, **kwargs):
        calls.append((contract.symbol, basis))
        expected = (
            MarketClockService()
            .cutoff_for(datetime.now(UTC), reason="DISPOSABLE_PROVIDER")
            .latest_completed_session
        )
        invalid = (
            getattr(health, "zero_close_symbol", None) == contract.symbol
            and basis == "ADJUSTED_LAST"
        )
        bar = HistoricalBar(
            contract.symbol,
            expected,
            "1 day",
            100,
            102,
            0 if invalid else 98,
            0 if invalid else 101,
            1200,
            "IB",
            basis,
            None,
        )
        # v3 ADJUSTED_LAST includes a forming response which must be excluded.
        forming = next_us_trading_day(expected)
        has_forming = forming == datetime.now(ZoneInfo("America/New_York")).date()
        return (
            [
                bar,
                HistoricalBar(
                    contract.symbol, forming, "1 day", 101, 103, 99, 102, 500, "IB", basis, None
                ),
            ]
            if basis == "ADJUSTED_LAST" and has_forming
            else [bar]
        )

    monkeypatch.setattr(ib_fetch_executor, "create_ib_client", FakeIB)
    monkeypatch.setattr(ib_fetch_executor, "IbHistoricalRateLimiter", NoWait)
    monkeypatch.setattr(ib_fetch_executor, "fetch_daily_bars", bars)
    # A forbidden network escape fails immediately even if a new code path appears.
    monkeypatch.setattr(
        "ib_insync.IB.connect",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("Live IB forbidden")),
    )
    return settings, health, calls


def execute_one(engine):
    with Session(engine) as db:
        register_worker(
            db,
            worker_id="sector-refresh-disposable-worker",
            instance_id="disposable-instance",
            queues=("background", "interactive"),
            hostname="disposable",
            process_id=12345,
            heartbeat_timeout_seconds=900,
            operation_profile="sector_refresh",
        )
        job = claim_next_job(
            db,
            "sector-refresh-disposable-worker",
            lease_seconds=900,
            worker_instance_id="disposable-instance",
            included_job_types=("IB_FETCH",),
        )
        assert job is not None and job.id >= 1642
        db.commit()
        result = execute_job(
            db,
            job,
            handlers={"IB_FETCH": execute_durable_fetch_job},
            execution_token=job.execution_token,
        )
        mark_job_completed(db, job, result=result, execution_token=job.execution_token)
        db.commit()
        return result
