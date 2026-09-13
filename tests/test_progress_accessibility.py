from types import SimpleNamespace

from app.templates import templates


def test_fetch_progress_template_has_live_progress_semantics(monkeypatch) -> None:
    monkeypatch.setitem(templates.env.globals, "url_for", lambda _name, path: path)

    html = templates.get_template("fetch_progress.html").render(
        active_nav="runs",
        run=SimpleNamespace(id=7, filename="hostile-&lt;script&gt;.csv"),
        progress={
            "fetch_run_id": 3,
            "status": "RUNNING",
            "started_at": "2026-08-02T10:00:00",
            "completed_at": None,
            "current_ticker": "MSFT",
            "message": "Fetching & checking",
            "percentage": 50.0,
            "completed_items": 1,
            "total_items": 2,
            "planned_request_count": 2,
            "executed_request_count": 1,
            "inserted_count": 1,
            "updated_count": 0,
            "revised_count": 0,
            "unchanged_count": 0,
            "failure_count": 0,
            "skipped_count": 0,
            "items": [
                {
                    "ticker": "MSFT",
                    "what_to_show": "TRADES",
                    "status": "RUNNING",
                    "action": "top_up",
                    "fetched": 0,
                    "inserted": 0,
                    "updated": 0,
                    "revised": 0,
                    "unchanged": 0,
                    "attempt_count": 1,
                    "error_message": "",
                }
            ],
        },
        terminal_statuses=["COMPLETED", "FAILED"],
        status_url="/runs/7/ib/fetches/3/status",
    )

    assert 'role="progressbar"' in html
    assert 'aria-valuenow="50"' in html
    assert 'role="status" aria-live="polite"' in html
    assert "Connection interrupted; retrying in 5s." in html
    assert "<caption>IB fetch items by ticker and data type.</caption>" in html


def test_pipeline_progress_template_has_live_progress_and_duplicate_state(monkeypatch) -> None:
    monkeypatch.setitem(templates.env.globals, "url_for", lambda _name, path: path)

    html = templates.get_template("pipeline_progress.html").render(
        active_nav="runs",
        run=SimpleNamespace(id=7, filename="run.csv"),
        pipeline={
            "pipeline_run_id": 11,
            "status": "RUNNING",
            "created_at": "2026-08-02T10:00:00",
            "started_at": "2026-08-02T10:01:00",
            "completed_at": None,
            "current_step_label": "Scoring",
            "job_status": "RUNNING",
            "job_cancel_requested": False,
            "message": "Working",
            "error_message": None,
            "percentage": 25.0,
            "completed_steps": 1,
            "total_steps": 4,
            "steps": [],
        },
        duplicate_action=True,
        terminal_statuses=["COMPLETED", "FAILED"],
        status_url="/runs/7/pipeline/11/status",
    )

    assert 'role="progressbar"' in html
    assert 'aria-valuenow="25"' in html
    assert "Already running: this request was coalesced into Pipeline 11." in html
    assert "<caption>Pipeline steps and durable job status.</caption>" in html


def test_pipeline_progress_template_tolerates_nullable_result_diagnostics(monkeypatch) -> None:
    monkeypatch.setitem(templates.env.globals, "url_for", lambda _name, path: path)
    nullable_results = (
        None,
        {},
        {
            "blocked_diagnostics": None,
            "sec_repair": None,
            "blocked_reason": None,
        },
        {
            "blocked_diagnostics": {},
            "sec_repair": {},
        },
        {
            "blocked_diagnostics": {
                "readiness": {
                    "processor_signature": None,
                    "requested_tickers": None,
                    "ready_tickers": None,
                    "blocking_tickers": None,
                },
                "processor": {"deployed_signature": None},
            },
            "sec_repair": {
                "repair_stage": None,
                "ready_tickers": None,
                "total_tickers": None,
                "repaired_tickers": None,
                "unresolved_tickers": None,
            },
        },
    )

    for result in nullable_results:
        html = templates.get_template("pipeline_progress.html").render(
            active_nav="runs",
            run=SimpleNamespace(id=158, filename="run.csv"),
            pipeline={
                "pipeline_run_id": 148,
                "status": "FETCHING_MARKET_DATA",
                "current_step": "FETCHING_MARKET_DATA",
                "current_step_label": "Fetching Market Data",
                "created_at": "2026-09-13T12:14:36",
                "started_at": "2026-09-13T12:14:37",
                "completed_at": None,
                "job_status": "RUNNING",
                "job_cancel_requested": False,
                "message": "Full pipeline is running.",
                "error_message": None,
                "percentage": 16.7,
                "completed_steps": 2,
                "total_steps": 12,
                "steps": [],
                "result": result,
            },
            duplicate_action=False,
            terminal_statuses=["COMPLETED", "PARTIAL", "FAILED", "BLOCKED", "CANCELLED"],
            status_url="/runs/158/pipeline/148/status",
        )

        assert "Pipeline 148" in html
        assert "Prerequisite Required" in html
        assert "Preparing Run" in html
        assert "data-pipeline-blocked-processor>Unknown</span>" in html
