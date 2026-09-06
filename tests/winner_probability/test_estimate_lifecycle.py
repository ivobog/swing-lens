from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.models.tables import WinnerProbabilityEstimate
from app.routers.run_routes import _winner_probability_context
from app.services.ib_market_intelligence.journal import _serving_winner_estimate
from app.services.winner_probability.api_service import WinnerProbabilityApiService
from app.services.winner_probability.dtos import WinnerProbabilityApiQuery
from app.services.winner_probability.estimate_lifecycle import estimate_is_serving


def test_serving_predicate_requires_published_estimate_and_generation() -> None:
    statement = select(WinnerProbabilityEstimate.id).where(estimate_is_serving())

    compiled = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "winner_probability_estimates.lifecycle_status = 'PUBLISHED'" in compiled
    assert "winner_cohort_generations.status = 'PUBLISHED'" in compiled
    assert "winner_probability_estimates.cohort_generation_id IS NULL" in compiled


def test_api_and_journal_use_the_authoritative_serving_predicate() -> None:
    from app.services.ib_market_intelligence import journal
    from app.services.winner_probability import api_service

    assert api_service.estimate_is_serving is estimate_is_serving
    assert journal.estimate_is_serving is estimate_is_serving


def test_api_live_lookup_compiles_the_serving_predicate() -> None:
    class Db:
        statement = None

        def scalar(self, statement):
            self.statement = statement
            return None

    db = Db()
    WinnerProbabilityApiService()._estimate_by_kind(
        db,
        prediction_id=1,
        outcome_definition_id=2,
        estimate_kind="DECISION_TIME",
        query=WinnerProbabilityApiQuery(),
    )

    compiled = str(db.statement.compile(dialect=postgresql.dialect()))
    assert "winner_probability_estimates.lifecycle_status" in compiled
    assert "winner_cohort_generations.status" in compiled


def test_journal_live_lookup_compiles_the_serving_predicate() -> None:
    class Db:
        statement = None

        def scalar(self, statement):
            self.statement = statement
            return None

    db = Db()
    _serving_winner_estimate(
        db,
        prediction_id=1,
        cutoff=datetime(2026, 9, 6, tzinfo=UTC),
    )

    compiled = str(db.statement.compile(dialect=postgresql.dialect()))
    assert "winner_probability_estimates.lifecycle_status" in compiled
    assert "winner_cohort_generations.status" in compiled
    assert "ORDER BY winner_probability_estimates.created_at DESC" in compiled


def test_run_summary_counts_only_serving_estimates() -> None:
    class Db:
        statements = []

        def scalars(self, _statement):
            return [SimpleNamespace(id=7, ticker="TEST")]

        def scalar(self, statement):
            self.statements.append(statement)
            return 0

    db = Db()
    _winner_probability_context(db, 1)

    assert len(db.statements) == 2
    for statement in db.statements:
        compiled = str(statement.compile(dialect=postgresql.dialect()))
        assert "winner_probability_estimates.lifecycle_status" in compiled
        assert "winner_cohort_generations.status" in compiled
