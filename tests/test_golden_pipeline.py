import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from readiness_helpers import seal_technical

from app.models.tables import RawCompanyRow, TechnicalScore
from app.services.combined_decision import refresh_combined_results
from app.services.combined_ranking_identity import (
    build_technical_score_identity,
    embed_calculation_identity,
)
from app.services.fundamental_score_service import recalculate_run_fundamentals
from app.services.market_clock_service import MarketCalculationCutoff

FIXTURE_PATH = Path("tests/fixtures/golden_pipeline.json")


def test_golden_pipeline_scoring_regression() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    db = GoldenFakeDb(fixture)
    cutoff = MarketCalculationCutoff(
        cutoff_at=datetime(2026, 7, 7, 20, tzinfo=UTC),
        exchange_timezone="America/New_York",
        latest_completed_session=date(2026, 7, 7),
        daily_bar_ready_at=datetime(2026, 7, 7, 20, tzinfo=UTC),
        calendar_version="XNYS-2026a",
        bar_readiness_version="daily-close-v1",
        cutoff_reason="GOLDEN_TEST",
        context_id=17,
    )

    fundamentals = recalculate_run_fundamentals(
        db,
        fixture["run_id"],
        market_cutoff=cutoff,
        pipeline_run_id=11,
    )
    from core_readiness_helpers import seal_core

    for fundamental in fundamentals:
        seal_core(fundamental)
    for technical in db.technicals:
        technical.calculation_context_id = cutoff.context_id
        technical.calculation_cutoff_at = cutoff.cutoff_at
        technical.input_as_of_session = cutoff.latest_completed_session
        technical.calendar_version = cutoff.calendar_version
        technical.technical_engine_version = "golden-v1"
        identity = build_technical_score_identity(
            technical,
            market_cutoff=cutoff,
            pipeline_run_id=11,
            effective_config={"golden": "technical-v1"},
        )
        technical.debug_json = embed_calculation_identity(
            technical.debug_json, identity, policy="GOLDEN_TEST"
        )
        seal_technical(technical)
    combined = refresh_combined_results(
        db,
        fixture["run_id"],
        market_cutoff=cutoff,
        pipeline_run_id=11,
    )
    expected = fixture["expected"]

    assert len(fundamentals) == 1
    assert len(combined) == 1
    assert combined[0].ticker == expected["top_ticker"]
    assert fundamentals[0].fundamental_score == Decimal(expected["fundamental_score"])
    assert fundamentals[0].fundamental_label == expected["fundamental_label"]
    # Native Fundamental warnings are DEGRADED; its retained score cannot enter
    # Combined. Apply the existing missing-source penalty once to Technical.
    assert (
        combined[0].debug_json["contextual_consumer_eligibility"]["fundamental"]["decision"][
            "status"
        ]
        == "POLICY_UNDECIDED"
    )
    assert combined[0].final_score == Decimal(expected["final_score"])
    assert combined[0].combined_decision == expected["combined_decision"]
    assert combined[0].position_size_hint == expected["position_size_hint"]
    assert combined[0].warning_flags_json == expected["warning_flags"]
    assert sum(not row.is_complete for row in combined) == expected["incomplete_count"]
    assert db.executed_statements.index("UPDATE winner_prediction_snapshots") < (
        db.executed_statements.index("DELETE FROM combined_results")
    )


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
        self.executed_statements = []

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
        if "UPDATE winner_prediction_snapshots" in text:
            self.executed_statements.append("UPDATE winner_prediction_snapshots")
        if "DELETE FROM fundamental_scores" in text:
            self.fundamentals = []
        elif "DELETE FROM combined_results" in text:
            self.executed_statements.append("DELETE FROM combined_results")
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
