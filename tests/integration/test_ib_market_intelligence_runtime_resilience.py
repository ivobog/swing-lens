from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.ib_market_intelligence_tables import (
    IBHistoricalMetricBar,
    IBIntelligenceRun,
)
from app.models.tables import BackgroundJob
from app.services.background_worker import CancelRequested
from app.services.ib_market_intelligence import orchestration
from app.services.ib_market_intelligence.adapters import (
    IBHistoricalMetricClient,
    IBLiveSnapshotManager,
)
from app.services.ib_market_intelligence.enums import AvailabilityStatus
from app.services.ib_market_intelligence.flex import IBFlexClient
from app.services.ib_market_intelligence.job_handlers import (
    IB_FLEX_IMPORT,
    IB_INTELLIGENCE_HISTORICAL_REFRESH,
)
from app.services.ib_market_intelligence.query_service import operations
from app.services.ib_market_intelligence.request_budget import (
    IBRequestBudget,
    RequestBudgetConfig,
)
from app.services.ib_market_intelligence.resilience import RetryExhausted, RetryPolicy
from app.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
CSV_REPORT = (
    "AccountId,TradeID,TradeDate,TradeTime,Symbol,Buy/Sell,Quantity,TradePrice\n"
    "U123,E1,20260804,093000,AAPL,BUY,1,200\n"
)


class FakeEvent:
    def __init__(self) -> None:
        self.handlers: list = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def __isub__(self, handler):
        self.handlers.remove(handler)
        return self

    def emit(self, *args) -> None:
        for handler in list(self.handlers):
            handler(*args)


def _complete_ticker() -> SimpleNamespace:
    return SimpleNamespace(
        callVolume=100,
        putVolume=50,
        callOpenInterest=200,
        putOpenInterest=150,
        avOptionVolume=75,
    )


def _partial_ticker() -> SimpleNamespace:
    return SimpleNamespace(
        callVolume=100,
        putVolume=50,
        callOpenInterest=None,
        putOpenInterest=None,
        avOptionVolume=75,
    )


def test_live_failure_injection_1101_resubscribes_but_1102_does_not() -> None:
    contract = SimpleNamespace(symbol="AAPL", conId=1)

    class FakeIB:
        def __init__(self, code: int) -> None:
            self.code = code
            self.errorEvent = FakeEvent()
            self.requests = 0
            self.cancelled = 0
            self.current = None

        def reqMktData(self, *_args, **_kwargs):
            self.requests += 1
            self.current = _complete_ticker() if self.requests > 1 else _partial_ticker()
            return self.current

        def waitOnUpdate(self, **_kwargs):
            self.errorEvent.emit(-1, self.code, "connectivity restored", None)
            if self.code == 1102:
                self.current.callOpenInterest = 200
                self.current.putOpenInterest = 150

        def cancelMktData(self, _contract):
            self.cancelled += 1

    lost = FakeIB(1101)
    lost_result = IBLiveSnapshotManager(
        lost,
        timeout_seconds=0.1,
        retry_policy=RetryPolicy(max_attempts=2, initial_backoff_seconds=0),
        sleep=lambda _seconds: None,
    ).capture(contract, "OPTIONS_ACTIVITY")
    assert lost_result.availability_status == AvailabilityStatus.AVAILABLE
    assert lost_result.source_request["resubscriptions"] == 1
    assert lost.requests == lost.cancelled == 2

    maintained = FakeIB(1102)
    maintained_result = IBLiveSnapshotManager(
        maintained,
        timeout_seconds=0.1,
        retry_policy=RetryPolicy(max_attempts=2, initial_backoff_seconds=0),
        sleep=lambda _seconds: None,
    ).capture(contract, "OPTIONS_ACTIVITY")
    assert maintained_result.availability_status == AvailabilityStatus.AVAILABLE
    assert maintained_result.source_request["resubscriptions"] == 0
    assert maintained.requests == maintained.cancelled == 1


def test_live_failure_injection_disconnect_timeout_partial_callback_and_cancellation() -> None:
    contract = SimpleNamespace(symbol="AAPL", conId=1)

    class DisconnectIB:
        def __init__(self) -> None:
            self.errorEvent = FakeEvent()
            self.requests = 0
            self.cancelled = 0

        def reqMktData(self, *_args, **_kwargs):
            self.requests += 1
            if self.requests == 1:
                raise ConnectionError("socket disconnected")
            return _complete_ticker()

        def cancelMktData(self, _contract):
            self.cancelled += 1

    reconnects: list[int] = []
    disconnected = DisconnectIB()
    recovered = IBLiveSnapshotManager(
        disconnected,
        retry_policy=RetryPolicy(max_attempts=2, initial_backoff_seconds=0),
        reconnect=lambda: reconnects.append(1),
        sleep=lambda _seconds: None,
    ).capture(contract, "OPTIONS_ACTIVITY")
    assert recovered.availability_status == AvailabilityStatus.AVAILABLE
    assert recovered.source_request["attempts"] == 2
    assert reconnects == [1]

    class PartialCallbackIB:
        def __init__(self) -> None:
            self.errorEvent = FakeEvent()
            self.ticker = _partial_ticker()
            self.waits = 0

        def reqMktData(self, *_args, **_kwargs):
            return self.ticker

        def waitOnUpdate(self, **_kwargs):
            self.waits += 1
            self.ticker.callOpenInterest = 200
            self.ticker.putOpenInterest = 150

        def cancelMktData(self, _contract):
            return None

    partial = PartialCallbackIB()
    completed = IBLiveSnapshotManager(partial, timeout_seconds=0.1).capture(
        contract, "OPTIONS_ACTIVITY"
    )
    assert completed.availability_status == AvailabilityStatus.AVAILABLE
    assert partial.waits == 1

    class TimeoutIB:
        def __init__(self) -> None:
            self.errorEvent = FakeEvent()
            self.requests = 0

        def reqMktData(self, *_args, **_kwargs):
            self.requests += 1
            return _partial_ticker()

        def cancelMktData(self, _contract):
            return None

    timed_out = TimeoutIB()
    unavailable = IBLiveSnapshotManager(
        timed_out,
        timeout_seconds=0.001,
        retry_policy=RetryPolicy(max_attempts=3, initial_backoff_seconds=0),
        sleep=lambda _seconds: None,
    ).capture(contract, "OPTIONS_ACTIVITY")
    assert unavailable.availability_status == AvailabilityStatus.UNAVAILABLE
    assert unavailable.source_request["attempts"] == 3
    assert timed_out.requests == 3

    guard_calls = 0

    def cancel_during_backoff() -> None:
        nonlocal guard_calls
        guard_calls += 1
        if guard_calls >= 3:
            raise CancelRequested("injected cancellation")

    with pytest.raises(CancelRequested, match="injected cancellation"):
        IBLiveSnapshotManager(
            TimeoutIB(),
            timeout_seconds=0.001,
            retry_policy=RetryPolicy(max_attempts=3, initial_backoff_seconds=1),
            guard=cancel_during_backoff,
            sleep=lambda _seconds: None,
        ).capture(contract, "OPTIONS_ACTIVITY")


def test_market_data_line_cap_is_observable_and_releases_capacity() -> None:
    contract = SimpleNamespace(symbol="AAPL", conId=1)
    budget = IBRequestBudget(
        RequestBudgetConfig(
            historical_min_spacing_seconds=0,
            tws_min_spacing_seconds=0,
            live_snapshot_concurrency=2,
            market_data_line_cap=1,
        )
    )

    class LineCapIB:
        def __init__(self) -> None:
            self.errorEvent = FakeEvent()

        def reqMktData(self, *_args, **_kwargs):
            return _partial_ticker()

        def waitOnUpdate(self, **_kwargs):
            self.errorEvent.emit(-1, 101, "Max number of tickers reached", None)

        def cancelMktData(self, _contract):
            return None

    result = IBLiveSnapshotManager(LineCapIB(), budget=budget, timeout_seconds=0.1).capture(
        contract, "OPTIONS_ACTIVITY"
    )
    capacity = budget.observability()
    assert "MARKET_DATA_LINE_CAP" in result.warning_flags
    assert capacity["configured_account_line_cap"] == 1
    assert capacity["effective_live_concurrency"] == 1
    assert capacity["line_cap_errors"] == 1
    assert capacity["active_lines"] == 0


def test_historical_disconnect_uses_bounded_retry_and_respects_range() -> None:
    contract = SimpleNamespace(symbol="AAPL", conId=1)

    class HistoricalIB:
        def __init__(self) -> None:
            self.calls = 0

        def reqHistoricalData(self, _contract, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise ConnectionError("historical socket disconnected")
            requested_end = kwargs["endDateTime"].date() - timedelta(days=1)
            return [SimpleNamespace(date=requested_end, open=1, high=2, low=1, close=2)]

    ib = HistoricalIB()
    reconnects: list[int] = []
    client = IBHistoricalMetricClient(
        ib,
        settings=Settings(_env_file=None, job_worker_enabled=False),
        retry_policy=RetryPolicy(max_attempts=2, initial_backoff_seconds=0),
        reconnect=lambda: reconnects.append(1),
        sleep=lambda _seconds: None,
    )
    bars = client.fetch(
        contract,
        "FEE_RATE",
        duration="2 D",
        start_date=datetime(2026, 8, 3).date(),
        end_date=datetime(2026, 8, 4).date(),
    )
    assert [bar.session_date.isoformat() for bar in bars] == ["2026-08-04"]
    assert ib.calls == 2
    assert reconnects == [1]


def test_restart_consumes_date_checkpoint_and_flex_cancellation_resumes_get_statement(
    disposable_postgres_database: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = {**os.environ, "DATABASE_URL": disposable_postgres_database}
    upgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr
    engine = create_engine(disposable_postgres_database)
    settings = Settings(
        _env_file=None,
        job_worker_enabled=False,
        ib_intelligence_request_max_attempts=2,
        ib_intelligence_retry_initial_seconds=0,
        ib_intelligence_retry_max_seconds=0,
        ib_intelligence_historical_chunk_days=2,
        ib_flex_token="test-token",
        ib_flex_trade_query_id="test-query",
        ib_flex_poll_attempts=3,
        ib_flex_poll_seconds=1,
    )
    budget = IBRequestBudget(
        RequestBudgetConfig(
            historical_weighted_tokens_per_minute=100,
            historical_min_spacing_seconds=0,
            tws_min_spacing_seconds=0,
        )
    )
    monkeypatch.setattr(orchestration, "shared_request_budget", lambda: budget)
    monkeypatch.setattr(
        orchestration,
        "resolve_us_stock_contract",
        lambda _db, ticker, _ib: SimpleNamespace(
            contract=SimpleNamespace(symbol=ticker, conId=1)
        ),
    )
    monkeypatch.setattr(orchestration, "_rebuild_ticker_feature", lambda *_args, **_kwargs: None)

    class HistoricalIB:
        def __init__(self, failing_end=None) -> None:
            self.connected = False
            self.failing_end = failing_end
            self.calls: list = []

        def connect(self, *_args, **_kwargs):
            self.connected = True

        def isConnected(self):
            return self.connected

        def disconnect(self):
            self.connected = False

        def reqHistoricalData(self, _contract, **kwargs):
            requested_end = kwargs["endDateTime"].date() - timedelta(days=1)
            self.calls.append(requested_end)
            if requested_end == self.failing_end:
                self.connected = False
                raise ConnectionError("injected historical restart")
            return [SimpleNamespace(date=requested_end, open=1, high=2, low=1, close=2)]

    now = datetime.now(UTC)
    with Session(engine) as db:
        historical_job = BackgroundJob(
            job_type=IB_INTELLIGENCE_HISTORICAL_REFRESH,
            request_key="resumable-history",
            status="RUNNING",
            priority=100,
            payload_json={
                "module": "LIQUIDITY",
                "tickers": ["AAPL"],
                "start_date": "2026-08-01",
                "end_date": "2026-08-04",
            },
            max_retries=3,
            run_after=now,
        )
        db.add(historical_job)
        db.commit()
        first_ib = HistoricalIB(failing_end=datetime(2026, 8, 4).date())
        with pytest.raises(RetryExhausted):
            orchestration.execute_historical_refresh(
                db, historical_job, settings=settings, ib_factory=lambda: first_ib
            )
        db.refresh(historical_job)
        checkpoint = historical_job.payload_json["checkpoint"]
        assert checkpoint["phase"] == "retry_pending"
        assert len(checkpoint["completed_ranges"]) == 1

        resumed_ib = HistoricalIB()
        result = orchestration.execute_historical_refresh(
            db, historical_job, settings=settings, ib_factory=lambda: resumed_ib
        )
        assert result["status"] == "COMPLETED"
        assert [value.isoformat() for value in resumed_ib.calls] == ["2026-08-04"]
        assert len(db.scalars(select(IBHistoricalMetricBar)).all()) == 2
        db.refresh(historical_job)
        assert historical_job.payload_json["checkpoint"]["completed_tickers"] == ["AAPL"]

        flex_job = BackgroundJob(
            job_type=IB_FLEX_IMPORT,
            request_key="resumable-flex",
            status="RUNNING",
            priority=100,
            payload_json={"query_type": "TRADE_CONFIRMATIONS", "dry_run": False},
            max_retries=3,
            run_after=now,
        )
        db.add(flex_job)
        db.commit()
        first_urls: list[str] = []

        def cancelling_transport(url: str, _timeout: float) -> str:
            first_urls.append(url)
            if "SendRequest" in url:
                return (
                    "<FlexStatementResponse><Status>Success</Status>"
                    "<ReferenceCode>RESUME-REF</ReferenceCode></FlexStatementResponse>"
                )
            flex_job.requested_cancel = True
            db.commit()
            return (
                "<FlexStatementResponse><Status>Fail</Status><ErrorCode>1019</ErrorCode>"
                "<ErrorMessage>Statement generation in progress</ErrorMessage>"
                "</FlexStatementResponse>"
            )

        cancelling_client = IBFlexClient(
            token="test-token",
            base_url="https://example.test",
            transport=cancelling_transport,
            sleep=lambda _seconds: None,
        )
        with pytest.raises(CancelRequested):
            orchestration.execute_flex_import(
                db,
                flex_job,
                settings=settings,
                client_factory=lambda: cancelling_client,
            )
        db.refresh(flex_job)
        flex_checkpoint = flex_job.payload_json["checkpoint"]
        assert flex_checkpoint["phase"] == "get_statement"
        assert flex_checkpoint["_reference_code"] == "RESUME-REF"
        cancelled_run = db.scalars(
            select(IBIntelligenceRun)
            .where(IBIntelligenceRun.background_job_id == flex_job.id)
            .order_by(IBIntelligenceRun.id.desc())
        ).first()
        assert "_reference_code" not in cancelled_run.checkpoint_json

        flex_job.requested_cancel = False
        db.commit()
        resumed_urls: list[str] = []

        def resumed_transport(url: str, _timeout: float) -> str:
            resumed_urls.append(url)
            if "SendRequest" in url:
                raise AssertionError("resume must not issue a second Flex SendRequest")
            return CSV_REPORT

        resumed_client = IBFlexClient(
            token="test-token",
            base_url="https://example.test",
            transport=resumed_transport,
        )
        flex_result = orchestration.execute_flex_import(
            db,
            flex_job,
            settings=settings,
            client_factory=lambda: resumed_client,
        )
        assert flex_result["status"] == "COMPLETED"
        assert len(resumed_urls) == 1 and "GetStatement" in resumed_urls[0]
        db.refresh(flex_job)
        assert flex_job.payload_json["checkpoint"]["phase"] == "complete"
        assert "_reference_code" not in flex_job.payload_json["checkpoint"]
        assert operations(db)["request_budget"]["configured_account_line_cap"] == 100
