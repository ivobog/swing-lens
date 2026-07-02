from datetime import UTC, datetime
from decimal import Decimal

from app.models.tables import CombinedResult, RawCompanyRow, UploadRun
from app.services.export_service import export_filename, export_run_csv
from app.services.history_service import recent_decisions, summarize_runs


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
        )
    ]

    csv_text = export_run_csv(run, "combined")

    assert "run_id,rank,ticker" in csv_text
    assert "7,1,MSFT,Microsoft,Technology,8.75" in csv_text
    assert "Strong candidate" in csv_text


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
