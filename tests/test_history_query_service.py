from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

from app.services.history_query_service import (
    DecisionFilters,
    RunFilters,
    _decisions_statement,
    _runs_statement,
    paged_decisions,
    paged_runs,
)


def test_paged_runs_maps_sql_rows_to_summary_dtos() -> None:
    db = FakeDb(
        total=1,
        rows=[
            SimpleNamespace(
                run_id=7,
                filename="sample.csv",
                uploaded_at=datetime(2026, 7, 1),
                processed_at=None,
                status="COMPLETED",
                row_count=10,
                combined_count=8,
                incomplete_count=1,
                warning_count=2,
                strong_count=3,
                top_complete_ticker="MSFT",
                top_complete_score=Decimal("8.75"),
            )
        ],
    )

    page = paged_runs(db, RunFilters(search="sample"), page=1, page_size=25)

    assert page.total_items == 1
    assert page.items[0].run_id == 7
    assert page.items[0].combined_count == 8
    assert page.items[0].top_complete_ticker == "MSFT"


def test_paged_decisions_maps_sql_rows_to_decision_dtos() -> None:
    db = FakeDb(
        total=1,
        rows=[
            SimpleNamespace(
                run_id=7,
                uploaded_at=datetime(2026, 7, 1),
                rank=1,
                ticker="MSFT",
                company_name="Microsoft",
                sector="Technology",
                final_score=Decimal("8.75"),
                combined_decision="Strong candidate",
                position_size_hint="Full starter",
                has_warning=False,
                is_complete=True,
            )
        ],
    )

    page = paged_decisions(db, DecisionFilters(ticker="MS"), page=1, page_size=50)

    assert page.items[0].ticker == "MSFT"
    assert page.items[0].combined_decision == "Strong candidate"
    assert page.items[0].is_complete is True


def test_runs_statement_applies_server_side_filters() -> None:
    statement = _runs_statement(
        RunFilters(
            status="COMPLETED",
            from_date=date(2026, 7, 1),
            to_date=date(2026, 7, 2),
            search="daily",
            sort="run_id",
            direction="asc",
        )
    )
    sql = str(statement)

    assert "upload_runs.status" in sql
    assert "upload_runs.filename" in sql
    assert "upload_runs.uploaded_at" in sql
    assert "ORDER BY upload_runs.id ASC" in sql


def test_decisions_statement_applies_server_side_filters() -> None:
    statement = _decisions_statement(
        DecisionFilters(
            decision="Strong candidate",
            ticker="MSFT",
            sector="Technology",
            min_score=Decimal("7.5"),
            has_warning=False,
            incomplete_only=True,
        )
    )
    sql = str(statement)

    assert "combined_results.combined_decision" in sql
    assert "lower(combined_results.ticker)" in sql
    assert "combined_results.sector" in sql
    assert "combined_results.final_score" in sql
    assert "combined_results.has_warning" in sql
    assert "combined_results.is_complete" in sql


class FakeExecuteResult:
    def __init__(self, rows) -> None:
        self.rows = rows

    def all(self):
        return self.rows


class FakeDb:
    def __init__(self, total: int, rows) -> None:
        self.total = total
        self.rows = rows

    def scalar(self, _statement):
        return self.total

    def execute(self, _statement):
        return FakeExecuteResult(self.rows)
