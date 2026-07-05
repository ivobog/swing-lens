from decimal import Decimal
from types import SimpleNamespace

from app.models.tables import (
    CombinedResult,
    FundamentalScore,
    RawCompanyRow,
    TechnicalScore,
    UploadRun,
)
from app.routers.run_routes import (
    _estimated_fetch_duration_label,
    _fetch_plan_action_counts,
    _fetch_plan_json_url,
    _fetch_request_options,
    _run_summary,
    _tickers_from_fetch_form,
    _warning_badges,
    _what_to_show_values,
    _workflow_steps,
    preview_run_ib_fetch_plan,
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
        ["incomplete_data", "value_trap_risk", "liquidity_warning", "high_accrual_risk"]
    )

    assert badges == [
        {"flag": "incomplete_data", "label": "Incomplete", "tone": "warning"},
        {"flag": "value_trap_risk", "label": "Value trap", "tone": "danger"},
        {"flag": "liquidity_warning", "label": "Liquidity", "tone": "muted"},
        {"flag": "high_accrual_risk", "label": "Accrual risk", "tone": "danger"},
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
    assert 'formaction="/runs/1/ib/plan"' in html
    assert 'formmethod="get"' in html
    assert 'name="include_benchmarks" value="true"' in html
    assert 'name="include_benchmarks" value="false"' not in html


def test_run_detail_template_renders_v2_fundamental_details(monkeypatch) -> None:
    monkeypatch.setitem(templates.env.globals, "url_for", lambda _name, path: path)
    run = UploadRun(id=1, filename="sample.csv", row_count=1, status="COMPLETED")
    fundamental = _fundamental("MSFT")
    combined = _combined("MSFT", "Candidate", is_complete=True, has_warning=True)
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
