"""Identical READY scenarios also run against the T12A checkout for exact parity."""

from dataclasses import replace

import pytest
from readiness_helpers import seal_technical
from setup_lifecycle.test_snapshot_builder import _ticker_context
from test_combined_decision import _config
from test_ranking_profile_engine import _fundamental, _row, _technical

from app.models.tables import SetupSignalSnapshot
from app.services.combined_decision import combine_row_decision
from app.services.ranking_profile_config import load_ranking_profiles
from app.services.ranking_profile_engine import rank_profile
from app.services.setup_lifecycle.actionability_policy import SetupLifecycleActionabilityPolicy
from app.services.setup_lifecycle.enums import Actionability, LifecycleState
from app.services.setup_lifecycle.episode_service import normalized_snapshot_from_row
from app.services.setup_lifecycle.lifecycle_engine import (
    LifecycleEvaluationInput,
    SetupLifecycleEngine,
)
from app.services.setup_lifecycle.repository import SetupLifecycleRepository
from app.services.setup_lifecycle.snapshot_builder import SetupLifecycleSnapshotBuilder


def test_fully_ready_population_preserves_all_profile_calculations():
    rows = [_row(ticker) for ticker in ("A", "B", "C")]
    fundamentals = {
        ticker: _fundamental(ticker, score)
        for ticker, score in (
            ("A", 8.1),
            ("B", 7.2),
            ("C", 9.0),
        )
    }
    technicals = {
        ticker: seal_technical(
            _technical(
                ticker,
                trend=score,
                momentum=score,
                setup=score,
                risk=2,
                rs=score,
            )
        )
        for ticker, score in (("A", 8), ("B", 7), ("C", 9))
    }
    for profile in load_ranking_profiles():
        decisions = rank_profile(
            profile=profile,
            rows=rows,
            fundamentals=fundamentals,
            technicals=technicals,
            config=_config(),
        )
        assert {decision.profile_rank for decision in decisions} == {1, 2, 3}
        assert all(decision.has_technical for decision in decisions)


@pytest.mark.parametrize("previous", [None, LifecycleState.DEVELOPING, LifecycleState.READY])
def test_fully_ready_actual_setup_lifecycle_chain(previous):
    context = _ticker_context()
    seal_technical(context.technical_score)
    combined = combine_row_decision(
        context.raw_row, context.fundamental_score, context.technical_score, config=_config()
    )
    assert combined.has_technical
    built = SetupLifecycleSnapshotBuilder().build(context)
    snapshot = SetupSignalSnapshot(id=77)
    SetupLifecycleRepository()._apply_snapshot_fields(snapshot, built.dto)
    normalized = normalized_snapshot_from_row(snapshot)
    decision = SetupLifecycleEngine().evaluate(
        LifecycleEvaluationInput(
            snapshot=normalized,
            previous_state=previous,
        )
    )
    assert "TECHNICAL_CONSUMER_INELIGIBLE" not in decision.reason_codes
    # Repeat exact deterministic calculations under the same READY input.
    again = SetupLifecycleEngine().evaluate(
        LifecycleEvaluationInput(
            snapshot=replace(normalized),
            previous_state=previous,
        )
    )
    assert decision == again
    assert (
        SetupLifecycleActionabilityPolicy()
        .evaluate(
            decision,
            normalized,
        )
        .actionability
        is not Actionability.BLOCKED
    )
