from datetime import UTC, datetime
from types import SimpleNamespace

from app.models.tables import IBFetchItem, IBFetchRun, RawCompanyRow, UploadRun
from app.routers import run_routes
from app.services.ib_fetch_job_service import FetchJobOptions, fetch_progress
from app.services.ib_fetch_plan_service import FetchPlan
from app.templates import templates


def test_fetch_progress_payload_includes_time_and_current_ticker() -> None:
    started = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)
    fetch_run = IBFetchRun(
        id=11,
        run_id=7,
        requested_tickers=["MSFT"],
        symbols_including_benchmarks=["MSFT"],
        planned_request_count=2,
        status="RUNNING",
        started_at=started,
    )
    fetch_run.items = [
        IBFetchItem(
            fetch_run_id=11,
            ticker="MSFT",
            what_to_show="TRADES",
            status="SUCCESS",
        ),
        IBFetchItem(
            fetch_run_id=11,
            ticker="AAPL",
            what_to_show="TRADES",
            status="PLANNED",
        ),
    ]

    progress = fetch_progress(fetch_run)

    assert progress["started_at"] == started
    assert progress["completed_at"] is None
    assert progress["current_ticker"] == "AAPL"
    assert progress["percentage"] == 50.0


def test_fetch_progress_template_renders_polling_and_recovery_actions(monkeypatch) -> None:
    monkeypatch.setitem(templates.env.globals, "url_for", lambda _name, path: path)
    run = UploadRun(id=7, filename="sample.csv", status="COMPLETED")

    html = templates.get_template("fetch_progress.html").render(
        run=run,
        progress=_progress(status="PARTIAL"),
        terminal_statuses=["COMPLETED", "PARTIAL", "FAILED", "CANCELLED"],
        status_url="/runs/7/ib/fetches/11/status",
    )

    assert "IB Fetch 11" in html
    assert 'data-fetch-progress' in html
    assert 'data-status-url="/runs/7/ib/fetches/11/status"' in html
    assert "Retry failed items" in html
    assert "Resume remaining items" in html
    assert "/runs/7/ib/fetches/11/failed.csv" in html
    assert 'data-fetch-item-row data-ticker="MSFT"' in html


def test_fetch_progress_template_renders_cancel_for_running_fetch(monkeypatch) -> None:
    monkeypatch.setitem(templates.env.globals, "url_for", lambda _name, path: path)
    run = UploadRun(id=7, filename="sample.csv", status="COMPLETED")

    html = templates.get_template("fetch_progress.html").render(
        run=run,
        progress=_progress(status="RUNNING"),
        terminal_statuses=["COMPLETED", "PARTIAL", "FAILED", "CANCELLED"],
        status_url="/runs/7/ib/fetches/11/status",
    )

    assert "Cancel fetch" in html
    assert "Auto-refreshing" in html


def test_failed_fetch_items_export_returns_only_failures(monkeypatch) -> None:
    monkeypatch.setattr(
        run_routes,
        "run_ib_fetch_progress",
        lambda run_id, fetch_run_id, db: _progress(status="FAILED"),
    )

    response = run_routes.export_failed_fetch_items(
        run_id=7,
        fetch_run_id=11,
        db=SimpleNamespace(),
    )

    assert response.media_type == "text/csv"
    assert response.body.decode().splitlines() == [
        "ticker,what_to_show,status,error_message",
        "AAPL,TRADES,FAILED,No contract",
    ]


def test_retry_failed_route_queues_resume_and_redirects_to_progress(monkeypatch) -> None:
    calls = {}
    queued_run = SimpleNamespace(id=42, run_id=7)
    plan = FetchPlan(
        run_id=7,
        requested_tickers=["AAPL"],
        symbols_including_benchmarks=["AAPL"],
        items=[],
        estimated_request_count=1,
        estimated_full_backfills=1,
        estimated_top_ups=0,
        estimated_refreshes=0,
        estimated_skips=0,
        warnings=[],
    )

    monkeypatch.setattr(run_routes, "_require_run", lambda _db, _run_id: None)
    monkeypatch.setattr(
        run_routes,
        "resume_fetch_job",
        lambda _db, _fetch_run_id: (queued_run, plan, FetchJobOptions()),
    )
    monkeypatch.setattr(
        run_routes,
        "submit_fetch_job",
        lambda fetch_run_id, _plan, _options: calls.setdefault("submitted", fetch_run_id),
    )
    db = RouteFakeDb()

    response = run_routes.retry_failed_run_ib_fetch_action(run_id=7, fetch_run_id=11, db=db)

    assert db.commits == 1
    assert calls["submitted"] == 42
    assert response.headers["location"].startswith("/runs/7/ib/fetches/42?")


def test_fetch_action_redirects_to_progress_page(monkeypatch) -> None:
    run = UploadRun(id=7, filename="sample.csv", status="COMPLETED")
    run.raw_company_rows = [RawCompanyRow(run_id=7, row_number=1, ticker="MSFT", raw_json={})]
    plan = FetchPlan(
        run_id=7,
        requested_tickers=["MSFT"],
        symbols_including_benchmarks=["MSFT"],
        items=[],
        estimated_request_count=1,
        estimated_full_backfills=1,
        estimated_top_ups=0,
        estimated_refreshes=0,
        estimated_skips=0,
        warnings=[],
    )

    monkeypatch.setattr(run_routes, "_load_run", lambda _db, _run_id: run)
    monkeypatch.setattr(run_routes, "build_fetch_plan", lambda **_kwargs: plan)
    monkeypatch.setattr(
        run_routes,
        "create_queued_fetch_run",
        lambda _db, _plan, _options: SimpleNamespace(id=99),
    )
    monkeypatch.setattr(run_routes, "submit_fetch_job", lambda *_args: None)
    db = RouteFakeDb()

    response = run_routes.fetch_run_ib_bars_action(run_id=7, db=db)

    assert db.commits == 1
    assert response.headers["location"].startswith("/runs/7/ib/fetches/99?")


class RouteFakeDb:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _progress(status: str) -> dict[str, object]:
    return {
        "fetch_run_id": 11,
        "run_id": 7,
        "status": status,
        "message": "Executed with one failure.",
        "cancel_requested": False,
        "started_at": datetime(2026, 7, 5, 12, 0, tzinfo=UTC),
        "completed_at": None,
        "current_ticker": "MSFT" if status == "RUNNING" else None,
        "percentage": 50.0,
        "completed_items": 1,
        "total_items": 2,
        "planned_request_count": 2,
        "executed_request_count": 1,
        "success_count": 1,
        "failure_count": 1,
        "skipped_count": 0,
        "fetched_count": 10,
        "inserted_count": 8,
        "updated_count": 1,
        "revised_count": 1,
        "unchanged_count": 0,
        "tickers": [],
        "items": [
            {
                "ticker": "MSFT",
                "what_to_show": "TRADES",
                "status": "SUCCESS",
                "action": "TOP_UP_RECENT",
                "fetched": 10,
                "inserted": 8,
                "updated": 1,
                "revised": 1,
                "unchanged": 0,
                "attempt_count": 1,
                "error_message": None,
            },
            {
                "ticker": "AAPL",
                "what_to_show": "TRADES",
                "status": "FAILED",
                "action": "FULL_BACKFILL",
                "fetched": 0,
                "inserted": 0,
                "updated": 0,
                "revised": 0,
                "unchanged": 0,
                "attempt_count": 1,
                "error_message": "No contract",
            },
        ],
    }
