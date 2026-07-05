from datetime import date
from types import SimpleNamespace

import app.services.ib_fetch_executor as executor
from app.models.tables import IBFetchItem
from app.services.bar_cache_service import BarUpsertSummary
from app.services.ib_fetch_executor import execute_fetch_plan
from app.services.ib_fetch_plan_service import FetchAction, FetchPlan, FetchPlanItem
from app.settings import Settings


def test_execute_fetch_plan_skips_and_fetches_items(monkeypatch) -> None:
    db = FakeDb()
    ib = FakeIB()
    limiter = FakeLimiter()
    monkeypatch.setattr(
        executor,
        "resolve_us_stock_contract",
        lambda db, ticker, ib: SimpleNamespace(
            contract=SimpleNamespace(symbol=ticker),
            error_message=None,
        ),
    )
    monkeypatch.setattr(executor, "fetch_daily_bars", lambda *args, **kwargs: ["bar"])
    monkeypatch.setattr(
        executor,
        "cache_bars",
        lambda db, bars: BarUpsertSummary(inserted=1, updated=0, revised=0, unchanged=0),
    )

    fetch_run = execute_fetch_plan(
        db=db,
        plan=FetchPlan(
            run_id=7,
            requested_tickers=["MSFT"],
            symbols_including_benchmarks=["MSFT", "SPY"],
            items=[
                _plan_item("MSFT", FetchAction.SKIP, duration=None),
                _plan_item("SPY", FetchAction.TOP_UP_RECENT, duration="10 D"),
            ],
            estimated_request_count=1,
            estimated_full_backfills=0,
            estimated_top_ups=1,
            estimated_refreshes=0,
            estimated_skips=1,
            warnings=[],
        ),
        ib_client_factory=lambda: ib,
        rate_limiter=limiter,
        settings=Settings(ib_max_retries=1),
        include_benchmarks=True,
    )

    assert fetch_run.status == "COMPLETED"
    assert fetch_run.skipped_count == 1
    assert fetch_run.success_count == 1
    assert fetch_run.executed_request_count == 1
    assert fetch_run.inserted_count == 1
    assert db.commits >= 2
    assert limiter.waits == 1
    assert ib.connected is False


def test_execute_fetch_plan_retries_failed_fetch(monkeypatch) -> None:
    db = FakeDb()
    ib = FakeIB()
    limiter = FakeLimiter()
    attempts = {"count": 0}
    monkeypatch.setattr(
        executor,
        "resolve_us_stock_contract",
        lambda db, ticker, ib: SimpleNamespace(
            contract=SimpleNamespace(symbol=ticker),
            error_message=None,
        ),
    )

    def flaky_fetch(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise TimeoutError("temporary")
        return ["bar"]

    monkeypatch.setattr(executor, "fetch_daily_bars", flaky_fetch)
    monkeypatch.setattr(
        executor,
        "cache_bars",
        lambda db, bars: BarUpsertSummary(inserted=0, updated=0, revised=0, unchanged=1),
    )

    fetch_run = execute_fetch_plan(
        db=db,
        plan=FetchPlan(
            run_id=7,
            requested_tickers=["MSFT"],
            symbols_including_benchmarks=["MSFT"],
            items=[_plan_item("MSFT", FetchAction.TOP_UP_RECENT, duration="10 D")],
            estimated_request_count=1,
            estimated_full_backfills=0,
            estimated_top_ups=1,
            estimated_refreshes=0,
            estimated_skips=0,
            warnings=[],
        ),
        ib_client_factory=lambda: ib,
        rate_limiter=limiter,
        settings=Settings(ib_max_retries=2),
    )

    assert fetch_run.status == "COMPLETED"
    assert attempts["count"] == 2
    assert limiter.waits == 2
    assert limiter.backoffs == 1
    assert fetch_run.unchanged_count == 1


def test_execute_fetch_plan_persists_contract_resolution_failure(monkeypatch) -> None:
    db = FakeDb()
    monkeypatch.setattr(
        executor,
        "resolve_us_stock_contract",
        lambda db, ticker, ib: SimpleNamespace(
            contract=None,
            error_message="No contract",
        ),
    )

    fetch_run = execute_fetch_plan(
        db=db,
        plan=FetchPlan(
            run_id=7,
            requested_tickers=["MSFT"],
            symbols_including_benchmarks=["MSFT"],
            items=[
                _plan_item(
                    "MSFT",
                    FetchAction.CONTRACT_RESOLUTION_REQUIRED,
                    duration=None,
                )
            ],
            estimated_request_count=0,
            estimated_full_backfills=0,
            estimated_top_ups=0,
            estimated_refreshes=0,
            estimated_skips=0,
            warnings=[],
        ),
        ib_client_factory=FakeIB,
        rate_limiter=FakeLimiter(),
        settings=Settings(ib_max_retries=1),
    )

    assert fetch_run.status == "FAILED"
    assert fetch_run.failure_count == 1
    assert fetch_run.items[0].status == "FAILED"
    assert fetch_run.items[0].error_message == "No contract"


def test_execute_fetch_plan_can_cancel_before_next_item(monkeypatch) -> None:
    db = FakeDb()
    monkeypatch.setattr(
        executor,
        "resolve_us_stock_contract",
        lambda db, ticker, ib: SimpleNamespace(
            contract=SimpleNamespace(symbol=ticker),
            error_message=None,
        ),
    )
    monkeypatch.setattr(executor, "fetch_daily_bars", lambda *args, **kwargs: ["bar"])
    monkeypatch.setattr(
        executor,
        "cache_bars",
        lambda db, bars: BarUpsertSummary(inserted=1, updated=0, revised=0, unchanged=0),
    )
    calls = {"count": 0}

    def should_cancel() -> bool:
        calls["count"] += 1
        return calls["count"] > 1

    fetch_run = execute_fetch_plan(
        db=db,
        plan=FetchPlan(
            run_id=7,
            requested_tickers=["MSFT", "AAPL"],
            symbols_including_benchmarks=["MSFT", "AAPL"],
            items=[
                _plan_item("MSFT", FetchAction.TOP_UP_RECENT, duration="10 D"),
                _plan_item("AAPL", FetchAction.TOP_UP_RECENT, duration="10 D"),
            ],
            estimated_request_count=2,
            estimated_full_backfills=0,
            estimated_top_ups=2,
            estimated_refreshes=0,
            estimated_skips=0,
            warnings=[],
        ),
        ib_client_factory=FakeIB,
        rate_limiter=FakeLimiter(),
        settings=Settings(ib_max_retries=1),
        should_cancel=should_cancel,
    )

    assert fetch_run.status == "CANCELLED"
    assert fetch_run.success_count == 1
    assert fetch_run.executed_request_count == 1
    assert len(fetch_run.items) == 1
    assert fetch_run.message == "IB fetch was cancelled."


def _plan_item(
    ticker: str,
    action: FetchAction,
    duration: str | None,
) -> FetchPlanItem:
    return FetchPlanItem(
        ticker=ticker,
        contract_status="RESOLVED",
        what_to_show="TRADES",
        action=action,
        duration=duration,
        bar_size="1 day",
        current_bar_count=300,
        first_bar_date=date(2025, 1, 1),
        latest_bar_date=date.today(),
        required_bars=252,
        reason=f"{action.value} reason",
        estimated_request_count=0 if action == FetchAction.SKIP else 1,
    )


class FakeDb:
    def __init__(self) -> None:
        self.added = []
        self.flushes = 0
        self.commits = 0

    def add(self, row) -> None:
        self.added.append(row)
        if isinstance(row, IBFetchItem) and row.fetch_run and row not in row.fetch_run.items:
            row.fetch_run.items.append(row)

    def flush(self) -> None:
        self.flushes += 1

    def commit(self) -> None:
        self.commits += 1


class FakeIB:
    def __init__(self) -> None:
        self.connected = False

    def connect(self, *args, **kwargs) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def isConnected(self) -> bool:
        return self.connected


class FakeLimiter:
    def __init__(self) -> None:
        self.waits = 0
        self.backoffs = 0

    def wait_before_request(self) -> None:
        self.waits += 1

    def backoff_after_error(self, error: Exception, attempt: int) -> None:
        self.backoffs += 1
