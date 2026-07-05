import csv
from datetime import UTC, date, datetime
from decimal import Decimal
from io import StringIO

from app.models.tables import (
    CombinedResult,
    FundamentalScore,
    IBFetchItem,
    IBFetchRun,
    RawCompanyRow,
    TechnicalScore,
    UploadRun,
)
from app.services.export_service import (
    export_fetch_plan_csv,
    export_fetch_results_csv,
    export_filename,
    export_run_csv,
)
from app.services.history_service import recent_decisions, summarize_runs
from app.services.ib_fetch_plan_service import FetchAction, FetchPlan, FetchPlanItem
from app.services.ohlcv_coverage_service import OhlcvCoverageItem, OhlcvCoverageSummary


def test_combined_export_includes_ranked_results() -> None:
    run = _run()
    run.combined_results = [
        CombinedResult(
            run_id=7,
            ticker="MSFT",
            company_name="Microsoft",
            sector="Technology",
            final_rank=1,
            final_score=Decimal("8.75"),
            fundamental_score=Decimal("9.00"),
            fundamental_label="Clean compounder",
            technical_classification="Prime clean pullback",
            dual_score=Decimal("8.44"),
            combined_decision="Strong candidate",
            position_size_hint="Full starter",
            notes="aligned",
            warning_flags_json=["high_accrual_risk"],
            is_complete=True,
            has_warning=True,
            has_fundamental=True,
            has_technical=True,
            sort_bucket=10,
        )
    ]
    run.fundamental_scores = [_fundamental("MSFT")]
    run.technical_scores = [_technical("MSFT", confidence="normal")]

    csv_text = export_run_csv(run, "combined", coverage=_coverage())
    row = next(csv.DictReader(StringIO(csv_text)))

    assert "run_id,rank,ticker" in csv_text
    assert "fundamental_model_version" in csv_text
    assert "is_complete,has_warning,warning_flags,sort_bucket" in csv_text
    assert "technical_confidence" in csv_text
    assert "7,1,MSFT,Microsoft,Technology,8.75" in csv_text
    assert "fundamentals_v2.0,8.70,high_accrual_risk,7.80,8.00,6.50,5.80" in csv_text
    assert row["technical_confidence"] == "normal"
    assert row["is_complete"] == "True"
    assert row["has_warning"] == "True"
    assert row["warning_flags"] == "high_accrual_risk"
    assert row["sort_bucket"] == "10"
    assert row["has_fundamental"] == "True"
    assert row["has_technical"] == "True"
    assert row["ohlcv_status"] == "ready"
    assert row["adjusted_bars"] == "252"
    assert row["trades_bars"] == "252"
    assert row["first_adjusted_date"] == "2025-07-01"
    assert row["first_trades_date"] == "2025-07-01"
    assert row["latest_adjusted_date"] == "2026-07-02"
    assert row["latest_trades_date"] == "2026-07-02"
    assert row["latest_bar_current"] == "True"
    assert "Strong candidate" in csv_text


def test_combined_export_order_matches_cockpit_sorting() -> None:
    run = _run()
    run.combined_results = [
        _combined_result("ZZZ", rank=1, score=Decimal("9.9"), sort_bucket=50),
        _combined_result("AAA", rank=2, score=Decimal("8.0"), sort_bucket=10),
        _combined_result("MSFT", rank=3, score=Decimal("8.5"), sort_bucket=10),
    ]

    rows = list(csv.DictReader(StringIO(export_run_csv(run, "combined"))))

    assert [row["ticker"] for row in rows] == ["MSFT", "AAA", "ZZZ"]


def test_fundamentals_export_includes_v2_details() -> None:
    run = _run()
    run.fundamental_scores = [_fundamental("MSFT")]

    csv_text = export_run_csv(run, "fundamentals")

    assert "model_version,growth_quality_score" in csv_text
    assert "earnings_quality_score,capital_efficiency_score" in csv_text
    assert "v2_warning_flags,missing_critical_fields,missing_high_fields" in csv_text
    assert "fundamentals_v2.0,8.10,8.20,7.40,7.80,8.00" in csv_text
    assert "high_accrual_risk,fcf_ttm,quick_ratio_quarterly,1" in csv_text


def test_raw_export_preserves_raw_json() -> None:
    run = _run()
    run.raw_company_rows = [
        RawCompanyRow(
            run_id=7,
            row_number=1,
            ticker="MSFT",
            company_name="Microsoft",
            sector="Technology",
            raw_json={"Symbol": "MSFT", "Price": "410.50"},
        )
    ]

    csv_text = export_run_csv(run, "raw")

    assert "raw_column_count,raw_json" in csv_text
    assert "MSFT" in csv_text
    assert "\"\"Price\"\": \"\"410.50\"\"" in csv_text


def test_export_filename_is_stable_and_safe() -> None:
    run = _run(filename="Money Money 2026-07-02.csv")

    assert export_filename(run, "combined") == (
        "swinglens_run_7_money-money-2026-07-02_combined.csv"
    )


def test_fetch_plan_export_includes_actions_and_coverage_context() -> None:
    plan = FetchPlan(
        run_id=7,
        requested_tickers=["MSFT"],
        symbols_including_benchmarks=["MSFT", "SPY"],
        items=[
            FetchPlanItem(
                ticker="MSFT",
                contract_status="RESOLVED",
                what_to_show="ADJUSTED_LAST",
                action=FetchAction.TOP_UP_RECENT,
                duration="1 M",
                bar_size="1 day",
                current_bar_count=250,
                first_bar_date=date(2025, 7, 1),
                latest_bar_date=date(2026, 6, 30),
                required_bars=252,
                reason="MSFT latest ADJUSTED_LAST bar is stale.",
                estimated_request_count=1,
            )
        ],
        estimated_request_count=1,
        estimated_full_backfills=0,
        estimated_top_ups=1,
        estimated_refreshes=0,
        estimated_skips=0,
        warnings=[],
    )

    csv_text = export_fetch_plan_csv(plan)

    assert "run_id,ticker,what_to_show,action,duration,bar_size" in csv_text
    assert "7,MSFT,ADJUSTED_LAST,TOP_UP_RECENT,1 M,1 day,RESOLVED" in csv_text
    assert "MSFT latest ADJUSTED_LAST bar is stale." in csv_text


def test_fetch_results_export_includes_latest_item_counts() -> None:
    fetch_run = IBFetchRun(
        id=11,
        run_id=7,
        requested_tickers=["MSFT"],
        symbols_including_benchmarks=["MSFT", "SPY"],
        include_benchmarks=True,
        force_refresh=False,
        force_full_backfill=False,
        planned_request_count=1,
        executed_request_count=1,
        skipped_count=0,
        success_count=1,
        started_at=datetime(2026, 7, 2, 9, 30, tzinfo=UTC),
        completed_at=datetime(2026, 7, 2, 9, 31, tzinfo=UTC),
        status="COMPLETED",
        fetched_count=5,
        inserted_count=2,
        updated_count=1,
        revised_count=1,
        unchanged_count=1,
        failure_count=0,
        message="done",
    )
    fetch_run.items = [
        IBFetchItem(
            fetch_run_id=11,
            ticker="MSFT",
            what_to_show="TRADES",
            action="TOP_UP_RECENT",
            duration="1 M",
            bar_size="1 day",
            status="SUCCESS",
            reason="top-up",
            current_bar_count=250,
            fetched=5,
            inserted=2,
            updated=1,
            revised=1,
            unchanged=1,
            attempt_count=1,
            started_at=datetime(2026, 7, 2, 9, 30, tzinfo=UTC),
            completed_at=datetime(2026, 7, 2, 9, 31, tzinfo=UTC),
            error_message=None,
        )
    ]

    csv_text = export_fetch_results_csv(fetch_run)

    assert "fetch_run_id,run_id,fetch_status,ticker" in csv_text
    assert "11,7,COMPLETED,MSFT,TRADES,TOP_UP_RECENT,1 M,1 day,SUCCESS,250,5,2,1,1,1,1" in csv_text


def test_fetch_results_export_handles_missing_fetch_run() -> None:
    csv_text = export_fetch_results_csv(None)

    assert csv_text.startswith("fetch_run_id,run_id,fetch_status,ticker")
    assert csv_text.count("\n") == 1


def test_history_summarizes_runs_and_recent_decisions() -> None:
    older = _run(run_id=6, uploaded_at=datetime(2026, 7, 1, tzinfo=UTC))
    newer = _run(run_id=7, uploaded_at=datetime(2026, 7, 2, tzinfo=UTC))
    older.combined_results = [
        CombinedResult(
            run_id=6,
            ticker="ADBE",
            final_rank=1,
            final_score=Decimal("7.1"),
            combined_decision="Candidate",
            position_size_hint="Half starter",
        )
    ]
    newer.combined_results = [
        CombinedResult(
            run_id=7,
            ticker="MSFT",
            final_rank=1,
            final_score=Decimal("8.7"),
            combined_decision="Strong candidate",
            position_size_hint="Full starter",
        )
    ]

    summaries = summarize_runs([newer, older])
    decisions = recent_decisions([older, newer])

    assert summaries[0].run_id == 7
    assert summaries[0].combined_count == 1
    assert summaries[0].top_ticker == "MSFT"
    assert decisions[0].run_id == 7
    assert decisions[0].ticker == "MSFT"


def _run(
    run_id: int = 7,
    filename: str = "sample.csv",
    uploaded_at: datetime | None = None,
) -> UploadRun:
    return UploadRun(
        id=run_id,
        filename=filename,
        uploaded_at=uploaded_at or datetime(2026, 7, 2, tzinfo=UTC),
        processed_at=uploaded_at or datetime(2026, 7, 2, tzinfo=UTC),
        row_count=1,
        status="completed",
    )


def _fundamental(ticker: str) -> FundamentalScore:
    return FundamentalScore(
        run_id=7,
        ticker=ticker,
        growth_score=Decimal("8.10"),
        profitability_score=Decimal("8.20"),
        fcf_score=Decimal("7.40"),
        balance_sheet_score=Decimal("7.10"),
        valuation_score=Decimal("5.90"),
        dilution_score=Decimal("5.80"),
        risk_score=Decimal("7.70"),
        missing_data_penalty=Decimal("0.20"),
        fundamental_score=Decimal("7.40"),
        fundamental_label="High-quality quant",
        trap_flags_json={"flags": ["high_accrual_risk"]},
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
        v2_warning_flags_json={"flags": ["high_accrual_risk"]},
        explanation="High-quality quant.",
        debug_json={
            "model_version": "fundamentals_v2.0",
            "coverage": {
                "missing_core_fields": ["fcf_ttm"],
                "missing_high_fields": ["quick_ratio_quarterly"],
            },
            "parse_diagnostics": {"failed_field_count": 1},
        },
    )


def _technical(ticker: str, confidence: str) -> TechnicalScore:
    return TechnicalScore(
        run_id=7,
        ticker=ticker,
        technical_confidence=confidence,
    )


def _combined_result(
    ticker: str,
    rank: int,
    score: Decimal,
    sort_bucket: int,
) -> CombinedResult:
    return CombinedResult(
        run_id=7,
        ticker=ticker,
        final_rank=rank,
        final_score=score,
        combined_decision="Candidate",
        position_size_hint="Half starter",
        warning_flags_json=[],
        is_complete=sort_bucket < 50,
        has_warning=False,
        has_fundamental=True,
        has_technical=sort_bucket < 50,
        sort_bucket=sort_bucket,
    )


def _coverage() -> OhlcvCoverageSummary:
    return OhlcvCoverageSummary(
        total_tickers=1,
        ready_count=1,
        insufficient_count=0,
        missing_count=0,
        benchmark_spy_ready=True,
        benchmark_qqq_ready=True,
        required_rows=252,
        items=[
            OhlcvCoverageItem(
                ticker="MSFT",
                adjusted_bars=252,
                trades_bars=252,
                has_price=True,
                has_volume=True,
                sufficient_history=True,
                status="ready",
                first_adjusted_date=date(2025, 7, 1),
                latest_adjusted_date=date(2026, 7, 2),
                first_trades_date=date(2025, 7, 1),
                latest_trades_date=date(2026, 7, 2),
                has_adjusted_price=True,
                has_trades_volume=True,
                latest_bar_current=True,
                reason="Adjusted price and trades volume coverage are ready.",
            )
        ],
    )
