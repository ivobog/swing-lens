import json
from decimal import Decimal
from pathlib import Path

from app.models.tables import RawCompanyRow, TechnicalScore
from app.services.combined_decision import refresh_combined_results
from app.services.fundamental_score_service import recalculate_run_fundamentals

FIXTURE_PATH = Path("tests/fixtures/golden_pipeline.json")


def test_golden_pipeline_scoring_regression() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    db = GoldenFakeDb(fixture)

    fundamentals = recalculate_run_fundamentals(db, fixture["run_id"])
    combined = refresh_combined_results(db, fixture["run_id"])
    expected = fixture["expected"]

    assert len(fundamentals) == 1
    assert len(combined) == 1
    assert combined[0].ticker == expected["top_ticker"]
    assert fundamentals[0].fundamental_score == Decimal(expected["fundamental_score"])
    assert fundamentals[0].fundamental_label == expected["fundamental_label"]
    assert combined[0].final_score == Decimal(expected["final_score"])
    assert combined[0].combined_decision == expected["combined_decision"]
    assert combined[0].position_size_hint == expected["position_size_hint"]
    assert combined[0].warning_flags_json == expected["warning_flags"]
    assert sum(not row.is_complete for row in combined) == expected["incomplete_count"]


class FakeScalarResult:
    def __init__(self, rows) -> None:
        self.rows = rows

    def __iter__(self):
        return iter(self.rows)

    def all(self):
        return self.rows


class GoldenFakeDb:
    def __init__(self, fixture: dict) -> None:
        run_id = fixture["run_id"]
        self.raw_rows = [
            RawCompanyRow(
                run_id=run_id,
                row_number=row["row_number"],
                ticker=row["ticker"],
                company_name=row["company_name"],
                sector=row["sector"],
                raw_json=row["raw_json"],
            )
            for row in fixture["raw_rows"]
        ]
        self.fundamentals = []
        self.technicals = [
            TechnicalScore(
                run_id=run_id,
                ticker=row["ticker"],
                dual_score=Decimal(row["dual_score"]),
                classification=row["classification"],
                risk_score=Decimal(row["risk_score"]),
                technical_confidence=row["technical_confidence"],
                insufficient_data=row["insufficient_data"],
                debug_json={"derived": {"liquidity_warning": False}},
            )
            for row in fixture["technical_scores"]
        ]
        self.combined = []

    def scalars(self, statement):
        text = str(statement)
        if "raw_company_rows" in text:
            return FakeScalarResult(self.raw_rows)
        if "fundamental_scores" in text:
            return FakeScalarResult(self.fundamentals)
        if "technical_scores" in text:
            return FakeScalarResult(self.technicals)
        return FakeScalarResult([])

    def execute(self, statement):
        text = str(statement)
        if "DELETE FROM fundamental_scores" in text:
            self.fundamentals = []
        elif "DELETE FROM combined_results" in text:
            self.combined = []

    def add_all(self, rows) -> None:
        rows = list(rows)
        if not rows:
            return
        if rows[0].__class__.__name__ == "FundamentalScore":
            self.fundamentals.extend(rows)
        elif rows[0].__class__.__name__ == "CombinedResult":
            self.combined.extend(rows)

    def flush(self) -> None:
        pass
