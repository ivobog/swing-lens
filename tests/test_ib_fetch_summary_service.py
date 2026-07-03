from app.models.tables import IBFetchRun
from app.services.bar_cache_service import BarFetchItem, BarFetchSummary
from app.services.ib_fetch_summary_service import (
    complete_ib_fetch_run,
    create_ib_fetch_run,
    fail_ib_fetch_run,
)


class FakeDb:
    def __init__(self) -> None:
        self.added = []
        self.flushed = 0

    def add(self, row) -> None:
        self.added.append(row)

    def flush(self) -> None:
        self.flushed += 1


def test_create_ib_fetch_run_starts_summary() -> None:
    db = FakeDb()

    fetch_run = create_ib_fetch_run(
        db,
        run_id=7,
        tickers=["MSFT"],
        include_benchmarks=True,
        symbols_including_benchmarks=["MSFT", "SPY"],
        planned_request_count=2,
        force_refresh=True,
    )

    assert fetch_run in db.added
    assert fetch_run.run_id == 7
    assert fetch_run.requested_tickers == ["MSFT"]
    assert fetch_run.symbols_including_benchmarks == ["MSFT", "SPY"]
    assert fetch_run.include_benchmarks is True
    assert fetch_run.force_refresh is True
    assert fetch_run.planned_request_count == 2
    assert fetch_run.status == "STARTED"
    assert db.flushed == 1


def test_complete_ib_fetch_run_persists_items_and_counts() -> None:
    db = FakeDb()
    fetch_run = IBFetchRun(run_id=7, requested_tickers=["MSFT"], status="STARTED")
    summary = BarFetchSummary(
        items=[
            BarFetchItem(
                ticker="MSFT",
                what_to_show="TRADES",
                fetched=252,
                inserted=10,
                updated=2,
                revised=1,
                unchanged=240,
                status="COMPLETED",
            ),
            BarFetchItem(
                ticker="AAPL",
                what_to_show="TRADES",
                status="FAILED",
                error_message="No contract",
            ),
        ]
    )

    complete_ib_fetch_run(db, fetch_run, summary)

    assert fetch_run.status == "COMPLETED_WITH_WARNINGS"
    assert fetch_run.planned_request_count == 2
    assert fetch_run.executed_request_count == 2
    assert fetch_run.success_count == 1
    assert fetch_run.fetched_count == 252
    assert fetch_run.inserted_count == 10
    assert fetch_run.updated_count == 2
    assert fetch_run.revised_count == 1
    assert fetch_run.unchanged_count == 240
    assert fetch_run.failure_count == 1
    assert len(fetch_run.items) == 2
    assert fetch_run.items[0].action == "FETCH"
    assert fetch_run.items[0].revised == 1
    assert fetch_run.items[0].unchanged == 240
    assert fetch_run.items[0].attempt_count == 1
    assert fetch_run.items[1].error_message == "No contract"
    assert db.flushed == 1


def test_fail_ib_fetch_run_sanitizes_message() -> None:
    db = FakeDb()
    fetch_run = IBFetchRun(run_id=7, requested_tickers=["MSFT"], status="STARTED")

    fail_ib_fetch_run(db, fetch_run, "line one\nline two")

    assert fetch_run.status == "FAILED"
    assert fetch_run.failure_count == 1
    assert fetch_run.executed_request_count == 1
    assert fetch_run.message == "line one line two"
    assert db.flushed == 1
