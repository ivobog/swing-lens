from decimal import Decimal

from app.models.tables import (
    CombinedResult,
    FundamentalScore,
    RawCompanyRow,
    TechnicalScore,
    UploadRun,
)
from app.routers.run_routes import _run_summary, _warning_badges, _workflow_steps


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
        price_bar_ticker_count=1,
    )

    assert steps[0]["status"] == "completed"
    assert steps[1]["status"] == "completed"
    assert steps[2]["status"] == "warning"
    assert steps[3]["status"] == "warning"
    assert steps[4]["status"] == "not-started"


def test_warning_badges_map_flags_to_labels_and_tones() -> None:
    badges = _warning_badges(["incomplete_data", "value_trap_risk", "liquidity_warning"])

    assert badges == [
        {"flag": "incomplete_data", "label": "Incomplete", "tone": "warning"},
        {"flag": "value_trap_risk", "label": "Value trap", "tone": "danger"},
        {"flag": "liquidity_warning", "label": "Liquidity", "tone": "muted"},
    ]


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
