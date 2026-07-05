from datetime import date

from app.models.tables import IBFetchItem, IBFetchRun
from app.services.ib_fetch_job_service import (
    FetchJobOptions,
    cancel_fetch_job,
    create_queued_fetch_run,
    fetch_progress,
    resume_fetch_job,
)
from app.services.ib_fetch_plan_service import FetchAction, FetchPlan, FetchPlanItem


def test_create_queued_fetch_run_persists_plan_context() -> None:
    db = FakeDb()
    plan = _plan(["MSFT"], [_plan_item("MSFT", FetchAction.TOP_UP_RECENT)])
    options = FetchJobOptions(include_benchmarks=True, force_refresh=True)

    fetch_run = create_queued_fetch_run(db, plan, options)

    assert fetch_run in db.added
    assert fetch_run.status == "QUEUED"
    assert fetch_run.message == "IB fetch is queued."
    assert fetch_run.run_id == 7
    assert fetch_run.requested_tickers == ["MSFT"]
    assert fetch_run.symbols_including_benchmarks == ["MSFT", "SPY"]
    assert fetch_run.include_benchmarks is True
    assert fetch_run.force_refresh is True
    assert fetch_run.planned_request_count == 1
    assert db.flushes == 1


def test_fetch_progress_reports_per_ticker_status() -> None:
    fetch_run = IBFetchRun(
        id=11,
        run_id=7,
        requested_tickers=["MSFT"],
        planned_request_count=2,
        executed_request_count=1,
        success_count=1,
        failure_count=0,
        skipped_count=0,
        fetched_count=5,
        inserted_count=4,
        updated_count=1,
        revised_count=1,
        unchanged_count=0,
        status="RUNNING",
        message="working",
    )
    fetch_run.items = [
        IBFetchItem(
            fetch_run_id=11,
            ticker="MSFT",
            what_to_show="TRADES",
            status="SUCCESS",
            action="TOP_UP_RECENT",
            fetched=5,
            inserted=4,
            updated=1,
            revised=1,
            unchanged=0,
            attempt_count=1,
        ),
        IBFetchItem(
            fetch_run_id=11,
            ticker="MSFT",
            what_to_show="ADJUSTED_LAST",
            status="PLANNED",
            action="TOP_UP_RECENT",
            fetched=0,
            inserted=0,
            updated=0,
            revised=0,
            unchanged=0,
            attempt_count=0,
        ),
    ]

    progress = fetch_progress(fetch_run, cancel_requested=True)

    assert progress["percentage"] == 50.0
    assert progress["cancel_requested"] is True
    assert progress["completed_items"] == 1
    assert progress["total_items"] == 2
    assert progress["tickers"] == [
        {
            "ticker": "MSFT",
            "completed_items": 1,
            "total_items": 2,
            "success_count": 1,
            "failure_count": 0,
            "skipped_count": 0,
        }
    ]


def test_resume_fetch_job_queues_failed_tickers_only(monkeypatch) -> None:
    failed_run = IBFetchRun(
        id=11,
        run_id=7,
        requested_tickers=["MSFT", "AAPL"],
        include_benchmarks=True,
        force_refresh=False,
        force_full_backfill=True,
        status="PARTIAL",
    )
    failed_run.items = [
        IBFetchItem(fetch_run_id=11, ticker="MSFT", what_to_show="TRADES", status="SUCCESS"),
        IBFetchItem(fetch_run_id=11, ticker="AAPL", what_to_show="TRADES", status="FAILED"),
    ]
    db = FakeDb(existing=failed_run)
    plan = _plan(["AAPL"], [_plan_item("AAPL", FetchAction.FORCE_REFRESH)])
    calls = {}

    def fake_build_fetch_plan(**kwargs):
        calls.update(kwargs)
        return plan

    monkeypatch.setattr("app.services.ib_fetch_job_service.build_fetch_plan", fake_build_fetch_plan)

    fetch_run, resume_plan, options = resume_fetch_job(db, 11)

    assert calls["tickers"] == ["AAPL"]
    assert calls["run_id"] == 7
    assert calls["include_benchmarks"] is False
    assert calls["force_full_backfill"] is True
    assert calls["what_to_show_values"] == ("TRADES",)
    assert fetch_run.status == "QUEUED"
    assert resume_plan is plan
    assert options.include_benchmarks is False
    assert options.force_full_backfill is True


def test_cancel_fetch_job_marks_orphaned_queued_run_cancelled() -> None:
    fetch_run = IBFetchRun(
        id=11,
        run_id=7,
        requested_tickers=["MSFT"],
        planned_request_count=1,
        status="QUEUED",
    )
    db = FakeDb(existing=fetch_run)

    progress = cancel_fetch_job(db, 11)

    assert progress["status"] == "CANCELLED"
    assert progress["message"] == "IB fetch was cancelled before it started."
    assert db.flushes == 1


def _plan(tickers: list[str], items: list[FetchPlanItem]) -> FetchPlan:
    return FetchPlan(
        run_id=7,
        requested_tickers=tickers,
        symbols_including_benchmarks=[*tickers, "SPY"],
        items=items,
        estimated_request_count=sum(item.estimated_request_count for item in items),
        estimated_full_backfills=0,
        estimated_top_ups=sum(item.action == FetchAction.TOP_UP_RECENT for item in items),
        estimated_refreshes=0,
        estimated_skips=sum(item.action == FetchAction.SKIP for item in items),
        warnings=[],
    )


def _plan_item(ticker: str, action: FetchAction) -> FetchPlanItem:
    return FetchPlanItem(
        ticker=ticker,
        contract_status="RESOLVED",
        what_to_show="TRADES",
        action=action,
        duration="10 D",
        bar_size="1 day",
        current_bar_count=250,
        first_bar_date=date(2026, 1, 1),
        latest_bar_date=date(2026, 7, 1),
        required_bars=252,
        reason="test",
        estimated_request_count=0 if action == FetchAction.SKIP else 1,
    )


class FakeDb:
    def __init__(self, existing: IBFetchRun | None = None) -> None:
        self.added = []
        self.flushes = 0
        self.existing = existing

    def add(self, row) -> None:
        self.added.append(row)

    def flush(self) -> None:
        self.flushes += 1

    def scalar(self, statement):
        return self.existing
