from decimal import Decimal

from app.models.tables import (
    CombinedResult,
    FundamentalScore,
    RawCompanyRow,
    TechnicalScore,
    UploadRun,
)
from app.routers.run_routes import (
    _fetch_plan_action_counts,
    _fetch_plan_json_url,
    _run_summary,
    _tickers_from_fetch_form,
    _warning_badges,
    _what_to_show_values,
    _workflow_steps,
)
from app.services.ib_fetch_plan_service import FetchAction, FetchPlan, FetchPlanItem
from app.services.ohlcv_coverage_service import OhlcvCoverageSummary
from app.templates import templates


def test_run_summary_counts_cockpit_state() -> None:
    run = UploadRun(id=1, filename="sample.csv", row_count=3, status="COMPLETED")
    rows = [_row("MSFT", 1), _row("AAPL", 2), _row("MSFT", 3)]
    results = [
        _combined("MSFT", "Strong candidate", is_complete=True, has_warning=False),
        _combined("AAPL", "Incomplete data", is_complete=False, has_warning=True),
    ]

    summary = _run_summary(run, rows, results)

    assert summary["row_count"] == 3
    assert summary["combined_count"] == 2
    assert summary["incomplete_count"] == 1
    assert summary["warning_count"] == 1
    assert summary["strong_count"] == 1
    assert summary["duplicate_ticker_count"] == 1
    assert summary["top_complete"].ticker == "MSFT"


def test_workflow_steps_show_warning_for_partial_coverage_and_low_confidence() -> None:
    run = UploadRun(id=1, filename="sample.csv", status="COMPLETED")
    run.fundamental_scores = [FundamentalScore(run_id=1, ticker="MSFT")]
    rows = [_row("MSFT", 1), _row("AAPL", 2)]
    technicals = [
        TechnicalScore(
            run_id=1,
            ticker="MSFT",
            technical_confidence="low",
            insufficient_data=True,
        )
    ]

    steps = _workflow_steps(
        run=run,
        rows=rows,
        technical_scores=technicals,
        combined_results=[],
        coverage=OhlcvCoverageSummary(
            total_tickers=2,
            ready_count=1,
            insufficient_count=0,
            missing_count=1,
            benchmark_spy_ready=False,
            benchmark_qqq_ready=False,
            required_rows=252,
            items=[],
        ),
    )

    assert steps[0]["status"] == "completed"
    assert steps[1]["status"] == "completed"
    assert steps[2]["status"] == "warning"
    assert steps[3]["status"] == "warning"
    assert steps[4]["status"] == "not-started"


def test_fetch_form_helpers_normalize_tickers_and_data_types() -> None:
    assert _tickers_from_fetch_form("msft, AAPL\nmsft NVDA") == ["MSFT", "AAPL", "NVDA"]
    assert _what_to_show_values(["TRADES", "BAD"]) == ("TRADES",)
    assert _what_to_show_values([]) == ("ADJUSTED_LAST", "TRADES")


def test_warning_badges_map_flags_to_labels_and_tones() -> None:
    badges = _warning_badges(["incomplete_data", "value_trap_risk", "liquidity_warning"])

    assert badges == [
        {"flag": "incomplete_data", "label": "Incomplete", "tone": "warning"},
        {"flag": "value_trap_risk", "label": "Value trap", "tone": "danger"},
        {"flag": "liquidity_warning", "label": "Liquidity", "tone": "muted"},
    ]


def test_fetch_plan_helpers_count_actions_and_build_json_url() -> None:
    plan = FetchPlan(
        run_id=7,
        requested_tickers=["MSFT"],
        symbols_including_benchmarks=["MSFT", "SPY"],
        items=[
            _plan_item("MSFT", FetchAction.SKIP),
            _plan_item("SPY", FetchAction.TOP_UP_RECENT),
            _plan_item("AAPL", FetchAction.TOP_UP_RECENT),
        ],
        estimated_request_count=2,
        estimated_full_backfills=0,
        estimated_top_ups=2,
        estimated_refreshes=0,
        estimated_skips=1,
        warnings=[],
    )

    counts = _fetch_plan_action_counts(plan)
    url = _fetch_plan_json_url(
        run_id=7,
        ticker_subset="MSFT,AAPL",
        include_benchmarks=True,
        force_refresh=False,
        force_full_backfill=True,
        what_to_show=["TRADES"],
    )

    assert counts == [
        {"action": "SKIP", "label": "Skip", "count": 1},
        {"action": "TOP_UP_RECENT", "label": "Top-up", "count": 2},
    ]
    assert url.startswith("/runs/7/ib/plan?")
    assert "format=json" in url
    assert "ticker_subset=MSFT%2CAAPL" in url
    assert "what_to_show=TRADES" in url


def test_run_detail_template_handles_missing_summary_context(monkeypatch) -> None:
    monkeypatch.setitem(templates.env.globals, "url_for", lambda _name, path: path)
    run = UploadRun(id=1, filename="sample.csv", row_count=3, status="COMPLETED")

    html = templates.get_template("run_detail.html").render(run=run)

    assert "Run 1" in html
    assert "Raw CSV Preview" in html
    assert "No combined decisions yet." in html


def _row(ticker: str, row_number: int) -> RawCompanyRow:
    return RawCompanyRow(
        run_id=1,
        row_number=row_number,
        ticker=ticker,
        company_name=f"{ticker} Corp",
        sector="Technology",
        raw_json={"Symbol": ticker, "Price": "1"},
    )


def _combined(
    ticker: str,
    decision: str,
    is_complete: bool,
    has_warning: bool,
) -> CombinedResult:
    return CombinedResult(
        run_id=1,
        ticker=ticker,
        final_rank=1,
        final_score=Decimal("8.0"),
        combined_decision=decision,
        is_complete=is_complete,
        has_warning=has_warning,
    )


def _plan_item(ticker: str, action: FetchAction) -> FetchPlanItem:
    return FetchPlanItem(
        ticker=ticker,
        contract_status="RESOLVED",
        what_to_show="TRADES",
        action=action,
        duration=None,
        bar_size="1 day",
        current_bar_count=300,
        first_bar_date=None,
        latest_bar_date=None,
        required_bars=252,
        reason="test",
        estimated_request_count=0 if action == FetchAction.SKIP else 1,
    )
