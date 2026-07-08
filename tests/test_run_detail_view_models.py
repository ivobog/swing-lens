from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from app.models.tables import (
    CombinedResult,
    FundamentalScore,
    RawCompanyRow,
    TechnicalScore,
    UploadRun,
)
from app.routers.run_routes import (
    _coverage_actions,
    _estimated_fetch_duration_label,
    _fetch_plan_action_counts,
    _fetch_plan_json_url,
    _fetch_request_options,
    _parse_bool_filter,
    _parse_date_filter,
    _parse_decimal_filter,
    _run_summary,
    _tickers_from_fetch_form,
    _warning_badges,
    _what_to_show_values,
    _workflow_steps,
    preview_run_ib_fetch_plan,
)
from app.services.ib_fetch_plan_service import FetchAction, FetchPlan, FetchPlanItem
from app.services.ohlcv_coverage_service import OhlcvCoverageItem, OhlcvCoverageSummary
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
    assert summary["earnings_blocked_count"] == 0
    assert summary["earnings_high_risk_count"] == 0
    assert summary["earnings_medium_risk_count"] == 0
    assert summary["earnings_unknown_count"] == 0
    assert summary["duplicate_ticker_count"] == 1
    assert summary["top_complete"].ticker == "MSFT"


def test_run_summary_counts_earnings_risk_levels() -> None:
    run = UploadRun(id=1, filename="sample.csv", row_count=4, status="COMPLETED")
    rows = [_row("MSFT", 1), _row("AAPL", 2), _row("NVDA", 3), _row("ADBE", 4)]
    results = [
        _combined("MSFT", "Blocked by earnings gate", True, True, earnings_risk="blocked"),
        _combined("AAPL", "Candidate", True, True, earnings_risk="high"),
        _combined("NVDA", "Strong candidate", True, True, earnings_risk="medium"),
        _combined("ADBE", "Candidate", True, True, earnings_risk="unknown"),
    ]

    summary = _run_summary(run, rows, results)

    assert summary["earnings_blocked_count"] == 1
    assert summary["earnings_high_risk_count"] == 1
    assert summary["earnings_medium_risk_count"] == 1
    assert summary["earnings_unknown_count"] == 1


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

    assert [step["label"] for step in steps] == [
        "Uploaded",
        "Fundamentals scored",
        "Column mapping checked",
        "IB fetch planned",
        "OHLCV ready",
        "Technicals scored",
        "Cockpit ready",
        "Export ready",
    ]
    assert steps[0]["status"] == "completed"
    assert steps[1]["status"] == "completed"
    assert steps[2]["status"] == "completed"
    assert steps[3]["status"] == "completed"
    assert steps[4]["status"] == "warning"
    assert steps[5]["status"] == "warning"
    assert steps[6]["status"] == "not-started"


def test_fetch_form_helpers_normalize_tickers_and_data_types() -> None:
    assert _tickers_from_fetch_form("msft, AAPL\nmsft NVDA") == ["MSFT", "AAPL", "NVDA"]
    assert _what_to_show_values(["TRADES", "BAD"]) == ("TRADES",)
    assert _what_to_show_values([]) == ("ADJUSTED_LAST", "TRADES")


def test_filter_parsers_ignore_blank_values() -> None:
    assert _parse_date_filter("") is None
    assert _parse_decimal_filter("") is None
    assert _parse_bool_filter("") is None
    assert str(_parse_date_filter("2026-07-07")) == "2026-07-07"
    assert _parse_decimal_filter("7.5") == Decimal("7.5")
    assert _parse_bool_filter("true") is True
    assert _parse_bool_filter("false") is False


def test_fetch_request_options_and_duration_label() -> None:
    plan = FetchPlan(
        run_id=7,
        requested_tickers=["MSFT"],
        symbols_including_benchmarks=["MSFT"],
        items=[],
        estimated_request_count=20,
        estimated_full_backfills=0,
        estimated_top_ups=20,
        estimated_refreshes=0,
        estimated_skips=0,
        warnings=[],
    )

    options = _fetch_request_options(
        ticker_subset="MSFT",
        include_benchmarks=False,
        force_refresh=True,
        force_full_backfill=False,
        what_to_show=("TRADES",),
    )
    label = _estimated_fetch_duration_label(
        plan,
        SimpleNamespace(ib_min_seconds_between_requests=3.0, ib_requests_per_minute=20),
    )

    assert options == {
        "ticker_subset": "MSFT",
        "include_benchmarks": False,
        "force_refresh": True,
        "force_full_backfill": False,
        "what_to_show": ["TRADES"],
        "include_adjusted": False,
        "include_trades": True,
    }
    assert label == "About 1.0 minutes minimum."


def test_warning_badges_map_flags_to_labels_and_tones() -> None:
    badges = _warning_badges(
        [
            "incomplete_data",
            "value_trap_risk",
            "liquidity_warning",
            "high_accrual_risk",
            "earnings_blocked",
            "earnings_date_missing",
        ]
    )

    assert badges == [
        {"flag": "incomplete_data", "label": "Incomplete", "tone": "warning"},
        {"flag": "value_trap_risk", "label": "Value trap", "tone": "danger"},
        {"flag": "liquidity_warning", "label": "Liquidity", "tone": "muted"},
        {"flag": "high_accrual_risk", "label": "Accrual risk", "tone": "danger"},
        {"flag": "earnings_blocked", "label": "Earnings block", "tone": "danger"},
        {"flag": "earnings_date_missing", "label": "No earnings date", "tone": "warning"},
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
    assert 'action="/runs/1/pipeline"' in html
    assert "Run full pipeline" in html
    assert 'action="/runs/1/fundamentals/recalculate"' in html
    assert "Recalculate fundamentals" in html
    assert 'action="/runs/1/technicals/refresh"' in html
    assert "Refresh technicals" in html
    assert "Refresh combined" in html
    assert 'href="/runs/1/coverage"' in html
    assert 'href="/runs/1/mapping"' in html
    assert 'href="/runs/1/exports/coverage.csv"' in html
    assert 'href="/runs/1/exports/mapping.csv"' in html
    assert 'formaction="/runs/1/ib/plan"' in html
    assert 'formmethod="get"' in html
    assert 'name="include_benchmarks" value="true"' in html
    assert 'name="include_benchmarks" value="false"' not in html


def test_pipeline_progress_template_renders_steps(monkeypatch) -> None:
    monkeypatch.setitem(templates.env.globals, "url_for", lambda _name, path: path)
    run = UploadRun(id=7, filename="sample.csv", row_count=1, status="COMPLETED")
    pipeline = {
        "pipeline_run_id": 99,
        "status": "RUNNING",
        "current_step_label": "Fetching Market Data",
        "created_at": "",
        "started_at": "",
        "completed_at": "",
        "message": "working",
        "error_message": None,
        "job_status": "RUNNING",
        "job_cancel_requested": False,
        "completed_steps": 1,
        "total_steps": 2,
        "percentage": 50.0,
        "steps": [
            {
                "step_name": "VALIDATING_RUN",
                "label": "Validating Run",
                "step_order": 1,
                "status": "COMPLETED",
                "started_at": "",
                "completed_at": "",
                "message": None,
                "error_message": None,
            },
            {
                "step_name": "FETCHING_MARKET_DATA",
                "label": "Fetching Market Data",
                "step_order": 2,
                "status": "RUNNING",
                "started_at": "",
                "completed_at": "",
                "message": None,
                "error_message": None,
            },
        ],
    }

    html = templates.get_template("pipeline_progress.html").render(
        run=run,
        pipeline=pipeline,
        terminal_statuses=["COMPLETED", "PARTIAL", "FAILED", "CANCELLED"],
        status_url="/runs/7/pipeline/99/status",
    )

    assert "Pipeline 99" in html
    assert 'data-pipeline-progress' in html
    assert 'data-status-url="/runs/7/pipeline/99/status"' in html
    assert "Fetching Market Data" in html
    assert "Cancel pipeline" in html


def test_run_detail_collapses_secondary_tables_by_default(monkeypatch) -> None:
    monkeypatch.setitem(templates.env.globals, "url_for", lambda _name, path: path)
    run = UploadRun(id=1, filename="sample.csv", row_count=1, status="COMPLETED")
    combined = _combined("MSFT", "Candidate", is_complete=True, has_warning=False)
    latest_fetch = SimpleNamespace(
        id=9,
        status="COMPLETED",
        fetched_count=1,
        executed_request_count=1,
        planned_request_count=1,
        inserted_count=1,
        revised_count=0,
        unchanged_count=0,
        failure_count=0,
        message="",
        items=[
            SimpleNamespace(
                ticker="MSFT",
                what_to_show="TRADES",
                status="SUCCESS",
                action="TOP_UP_RECENT",
                duration="1 D",
                fetched=1,
                inserted=1,
                updated=0,
                revised=0,
                unchanged=0,
                attempt_count=1,
                error_message="",
            )
        ],
    )

    html = templates.get_template("run_detail.html").render(
        run=run,
        raw_preview=[_row("MSFT", 1)],
        latest_fetch=latest_fetch,
        combined_results=[combined],
        decision_counts={"Candidate": 1},
        warning_badges_by_ticker={"MSFT": []},
    )

    assert "<summary>Latest Fetch rows</summary>" in html
    assert "<summary>Raw CSV Preview rows</summary>" in html
    assert '<details class="collapsible-table" open>' not in html
    assert 'data-cockpit-table' in html
    assert "<h2>Decision Cockpit</h2>" in html


def test_coverage_actions_and_template_support_targeted_fetches(monkeypatch) -> None:
    monkeypatch.setitem(templates.env.globals, "url_for", lambda _name, path: path)
    run = UploadRun(id=7, filename="sample.csv", row_count=3, status="COMPLETED")
    coverage = OhlcvCoverageSummary(
        total_tickers=3,
        ready_count=1,
        insufficient_count=1,
        missing_count=1,
        benchmark_spy_ready=True,
        benchmark_qqq_ready=False,
        required_rows=252,
        stale_count=0,
        missing_volume_count=0,
        failed_contract_count=0,
        items=[
            _coverage_item("MSFT", "ready"),
            _coverage_item("AAPL", "missing"),
            _coverage_item("NVDA", "insufficient"),
        ],
    )
    actions = _coverage_actions(coverage)

    html = templates.get_template("coverage.html").render(
        run=run,
        coverage=coverage,
        coverage_actions=actions,
    )

    assert actions["not_ready"] == "AAPL, NVDA"
    assert 'data-coverage-page' in html
    assert 'data-copy-coverage="not-ready"' in html
    assert 'data-coverage-status="missing"' in html
    assert 'value="AAPL, NVDA"' in html
    assert 'href="/runs/7/exports/coverage.csv"' in html


def test_mapping_template_shows_summary_and_export_link(monkeypatch) -> None:
    monkeypatch.setitem(templates.env.globals, "url_for", lambda _name, path: path)
    run = UploadRun(id=7, filename="sample.csv", row_count=1, status="COMPLETED")
    mapping = SimpleNamespace(
        raw_column_count=3,
        recognized_count=2,
        unrecognized_count=1,
        scoring_count=1,
        stored_only_count=1,
        missing_critical_fields=["net_income_ttm"],
        missing_high_fields=["quick_ratio_quarterly"],
        unrecognized_columns=["Mystery Column"],
        items=[
            SimpleNamespace(
                raw_header="Market capitalization",
                canonical_field="market_cap",
                priority="critical",
                component="valuation_quality_score",
                used_in_scoring=True,
                sample_value="3000000000000",
            )
        ],
    )

    html = templates.get_template("mapping.html").render(run=run, mapping=mapping)

    assert "Column Mapping" in html
    assert "Market capitalization" in html
    assert "market_cap" in html
    assert "Mystery Column" in html
    assert "net_income_ttm" in html
    assert 'href="/runs/7/exports/mapping.csv"' in html


def test_run_detail_template_renders_v2_fundamental_details(monkeypatch) -> None:
    monkeypatch.setitem(templates.env.globals, "url_for", lambda _name, path: path)
    run = UploadRun(id=1, filename="sample.csv", row_count=1, status="COMPLETED")
    fundamental = _fundamental("MSFT")
    combined = _combined("MSFT", "Candidate", is_complete=True, has_warning=True)
    combined.company_name = "Microsoft Corporation"
    combined.fundamental_score = Decimal("7.4")
    combined.fundamental_label = "High-quality quant"
    combined.warning_flags_json = ["high_accrual_risk"]
    run.fundamental_scores = [fundamental]

    html = templates.get_template("run_detail.html").render(
        run=run,
        combined_results=[combined],
        fundamental_by_ticker={"MSFT": fundamental},
        technical_by_ticker={},
        warning_badges_by_ticker={"MSFT": _warning_badges(["high_accrual_risk"])},
    )

    assert "fundamentals_v2.0" in html
    assert "Coverage" in html
    assert "Earnings" in html
    assert "Capital" in html
    assert "quick_ratio_quarterly" in html
    assert "high_accrual_risk" in html
    assert 'data-quick-filter="top10"' in html
    assert 'data-quick-filter="hide-earnings-blocked"' in html
    assert 'data-quick-filter="earnings-risk"' in html
    assert 'data-quick-filter="earnings-clear"' in html
    assert 'data-filter-clear' in html
    assert 'data-copy-tickers="visible"' in html
    assert 'data-copy-tickers="candidates"' in html
    assert 'data-sort-key="final-score"' in html
    assert 'data-sort-key="warning-count"' in html
    assert 'data-sort-key="earnings-date"' in html
    assert 'data-sort-key="days-until-earnings"' in html
    assert 'data-sort-key="earnings-risk"' not in html
    assert "<th>Company</th>" not in html
    assert 'data-sort-key="position-size"' not in html
    assert 'class="ticker-with-company" title="Microsoft Corporation">MSFT</strong>' in html
    assert 'data-warning-count="1"' in html
    assert 'class="cockpit-row clickable-row"' in html
    assert 'data-href="/runs/1/tickers/MSFT/chart"' in html
    assert 'data-no-row-nav="true" aria-expanded="false">Details</button>' in html
    assert '<a data-no-row-nav="true" href="https://www.tradingview.com/chart/?symbol=MSFT"' in html
    assert 'data-candidate-plus="true"' in html
    assert 'data-clean="false"' in html
    assert 'data-copy-single="MSFT"' not in html
    assert "https://www.tradingview.com/chart/?symbol=MSFT" in html


def test_run_detail_template_renders_earnings_risk_context(monkeypatch) -> None:
    monkeypatch.setitem(templates.env.globals, "url_for", lambda _name, path: path)
    run = UploadRun(id=1, filename="sample.csv", row_count=1, status="COMPLETED")
    combined = _combined(
        "MSFT",
        "Blocked by earnings gate",
        is_complete=True,
        has_warning=True,
        earnings_risk="blocked",
    )
    combined.upcoming_earnings_date = date(2026, 7, 14)
    combined.days_until_earnings = 1
    combined.earnings_warning_flags_json = ["earnings_blocked"]
    combined.warning_flags_json = ["earnings_blocked"]

    html = templates.get_template("run_detail.html").render(
        run=run,
        combined_results=[combined],
        decision_counts={"Blocked by earnings gate": 1},
        run_summary={
            "row_count": 1,
            "combined_count": 1,
            "incomplete_count": 0,
            "warning_count": 1,
            "strong_count": 0,
            "earnings_blocked_count": 1,
            "earnings_high_risk_count": 0,
            "earnings_medium_risk_count": 0,
            "earnings_unknown_count": 0,
            "duplicate_ticker_count": 0,
            "raw_column_count": 4,
            "top_complete": combined,
        },
        warning_badges_by_ticker={"MSFT": _warning_badges(["earnings_blocked"])},
    )

    assert "Earnings Blocked" in html
    assert 'data-earnings-date="2026-07-14"' in html
    assert 'data-days-until-earnings="1"' in html
    assert 'data-earnings-risk="blocked"' in html
    assert 'data-sort-key="earnings-risk"' not in html
    assert 'data-avoid="true"' in html
    assert "Blocked by earnings gate" in html
    assert "Earnings Date" in html
    assert "Earnings Flags" in html
    assert "earnings_blocked" in html


def test_run_detail_template_renders_v4_technical_details(monkeypatch) -> None:
    monkeypatch.setitem(templates.env.globals, "url_for", lambda _name, path: path)
    run = UploadRun(id=1, filename="sample.csv", row_count=1, status="COMPLETED")
    technical = _technical("MSFT")
    combined = _combined("MSFT", "Candidate", is_complete=True, has_warning=False)
    combined.dual_score = Decimal("8.44")
    combined.technical_classification = "Prime clean pullback"

    html = templates.get_template("run_detail.html").render(
        run=run,
        combined_results=[combined],
        fundamental_by_ticker={},
        technical_by_ticker={"MSFT": technical},
        technical_details_by_ticker={
            "MSFT": {
                "technical_version": "4.0.0",
                "stage": "Stage 2",
                "market_regime": "Bull trend",
                "leadership_score": 9.2,
                "vcp_score": 7.4,
                "box_breakout": True,
                "breakout_quality_score": 8.8,
                "climax_risk_score": 2.2,
                "sub_tags": "VCP; Stage 2",
                "warning_flags": "missing_benchmark_data",
            }
        },
        warning_badges_by_ticker={"MSFT": []},
    )

    assert "4.0.0" in html
    assert "Stage 2" in html
    assert "Bull trend" in html
    assert "9.20" in html
    assert "7.40" in html
    assert "Breakout" in html
    assert "8.80" in html
    assert "2.20" in html
    assert "VCP; Stage 2" in html
    assert "missing_benchmark_data" in html


def test_ib_fetch_plan_template_preserves_options_for_execution(monkeypatch) -> None:
    monkeypatch.setitem(templates.env.globals, "url_for", lambda _name, path: path)
    run = UploadRun(id=7, filename="sample.csv", row_count=1, status="COMPLETED")
    plan = FetchPlan(
        run_id=7,
        requested_tickers=["MSFT"],
        symbols_including_benchmarks=["MSFT"],
        items=[_plan_item("MSFT", FetchAction.TOP_UP_RECENT)],
        estimated_request_count=1,
        estimated_full_backfills=0,
        estimated_top_ups=1,
        estimated_refreshes=0,
        estimated_skips=0,
        warnings=[],
    )

    html = templates.get_template("ib_fetch_plan.html").render(
        run=run,
        plan=plan,
        action_counts=_fetch_plan_action_counts(plan),
        request_options={
            "ticker_subset": "MSFT",
            "include_benchmarks": False,
            "force_refresh": True,
            "force_full_backfill": False,
            "what_to_show": ["TRADES"],
        },
        estimated_duration_label="About 3 seconds minimum.",
        json_url="/runs/7/ib/plan?format=json",
    )

    assert "Execute this plan" in html
    assert 'action="/runs/7/ib/fetch"' in html
    assert 'name="ticker_subset" value="MSFT"' in html
    assert 'name="include_benchmarks" value="false"' in html
    assert 'name="force_refresh" value="true"' in html
    assert 'name="what_to_show" value="TRADES"' in html
    assert "About 3 seconds minimum." in html


def test_preview_fetch_plan_uses_current_request_options(monkeypatch) -> None:
    calls = {}
    run = UploadRun(id=7, filename="sample.csv", row_count=1, status="COMPLETED")
    run.raw_company_rows = [_row("SHOULD_NOT_USE", 1)]

    def fake_build_fetch_plan(*args, **kwargs):
        calls["args"] = args
        calls["kwargs"] = kwargs
        tickers = args[1]
        return FetchPlan(
            run_id=7,
            requested_tickers=tickers,
            symbols_including_benchmarks=tickers,
            items=[],
            estimated_request_count=0,
            estimated_full_backfills=0,
            estimated_top_ups=0,
            estimated_refreshes=0,
            estimated_skips=0,
            warnings=[],
        )

    monkeypatch.setattr("app.routers.run_routes._load_run", lambda _db, _run_id: run)
    monkeypatch.setattr("app.routers.run_routes.build_fetch_plan", fake_build_fetch_plan)

    payload = preview_run_ib_fetch_plan(
        run_id=7,
        request=SimpleNamespace(),
        db=SimpleNamespace(),
        ticker_subset="aapl, msft",
        include_benchmarks=False,
        force_refresh=True,
        force_full_backfill=True,
        what_to_show=["TRADES"],
        format="json",
    )

    assert payload["requested_tickers"] == ["AAPL", "MSFT"]
    assert calls["args"][1] == ["AAPL", "MSFT"]
    assert calls["kwargs"]["include_benchmarks"] is False
    assert calls["kwargs"]["force_refresh"] is True
    assert calls["kwargs"]["force_full_backfill"] is True
    assert calls["kwargs"]["what_to_show_values"] == ("TRADES",)


def test_app_js_binds_cockpit_row_navigation() -> None:
    script = Path("app/static/app.js").read_text(encoding="utf-8")

    assert "shouldIgnoreRowNavigation" in script
    assert "window.location.href = row.dataset.href" in script
    assert "[data-no-row-nav], a, button, input, select, textarea, label, summary" in script


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
    earnings_risk: str | None = None,
) -> CombinedResult:
    return CombinedResult(
        run_id=1,
        ticker=ticker,
        final_rank=1,
        final_score=Decimal("8.0"),
        combined_decision=decision,
        is_complete=is_complete,
        has_warning=has_warning,
        earnings_risk_level=earnings_risk,
        earnings_warning_flags_json=[],
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


def _coverage_item(ticker: str, status: str) -> OhlcvCoverageItem:
    return OhlcvCoverageItem(
        ticker=ticker,
        adjusted_bars=252 if status == "ready" else 0,
        trades_bars=252 if status == "ready" else 0,
        has_price=status == "ready",
        has_volume=status == "ready",
        sufficient_history=status == "ready",
        status=status,
        latest_bar_current=status == "ready",
        reason=f"{ticker} {status}",
    )


def _fundamental(ticker: str) -> FundamentalScore:
    return FundamentalScore(
        run_id=1,
        ticker=ticker,
        fundamental_score=Decimal("7.40"),
        fundamental_label="High-quality quant",
        missing_data_penalty=Decimal("0.20"),
        scoring_model_version="fundamentals_v2.0",
        growth_quality_score=Decimal("8.10"),
        profitability_quality_score=Decimal("8.20"),
        fcf_quality_score=Decimal("7.40"),
        earnings_quality_score=Decimal("7.80"),
        capital_efficiency_score=Decimal("8.00"),
        balance_sheet_quality_score=Decimal("7.10"),
        valuation_quality_score=Decimal("5.90"),
        forward_quality_score=Decimal("6.50"),
        shareholder_quality_score=Decimal("5.80"),
        liquidity_risk_score=Decimal("7.70"),
        data_coverage_score=Decimal("8.70"),
        trap_flags_json={"flags": ["high_accrual_risk"]},
        v2_warning_flags_json={"flags": ["high_accrual_risk"]},
        debug_json={
            "model_version": "fundamentals_v2.0",
            "coverage": {
                "missing_core_fields": ["fcf_ttm"],
                "missing_high_fields": ["quick_ratio_quarterly"],
            },
        },
    )


def _technical(ticker: str) -> TechnicalScore:
    return TechnicalScore(
        run_id=1,
        ticker=ticker,
        trend_score=Decimal("8.10"),
        momentum_score=Decimal("7.90"),
        setup_score=Decimal("7.80"),
        risk_score=Decimal("2.10"),
        market_score=Decimal("8.20"),
        combined_relative_strength_score=Decimal("8.10"),
        htf_score=Decimal("7.60"),
        dual_score=Decimal("8.44"),
        technical_confidence="normal",
        action_bias="Best buyable, R/R ok",
    )
