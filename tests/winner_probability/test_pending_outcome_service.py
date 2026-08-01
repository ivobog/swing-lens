from __future__ import annotations

from datetime import UTC, date, datetime

from _phase3_helpers import FakeWinnerRepository

from app.models.tables import WinnerPredictionSnapshot
from app.services.winner_probability.config import load_winner_probability_config
from app.services.winner_probability.pending_outcome_service import PendingOutcomeService


def test_pending_outcomes_materialize_all_configured_entry_models_and_horizons() -> None:
    config = load_winner_probability_config()
    repository = FakeWinnerRepository()
    service = PendingOutcomeService(repository)
    prediction = _prediction()
    repository.add(object(), prediction)

    result = service.materialize_pending_outcomes(object(), prediction, config)

    assert result.forward_outcome_count == 10
    assert result.target_stop_outcome_count == 2
    assert len(repository.outcome_definitions) == 2
    next_open_h5 = next(
        outcome
        for outcome in repository.forward_outcomes
        if outcome.entry_model == "NEXT_OPEN" and outcome.horizon_sessions == 5
    )
    assert next_open_h5.entry_session == date(2026, 8, 3)
    assert next_open_h5.due_session == date(2026, 8, 7)


def test_pending_outcome_materialization_is_idempotent() -> None:
    config = load_winner_probability_config()
    repository = FakeWinnerRepository()
    service = PendingOutcomeService(repository)
    prediction = _prediction()
    repository.add(object(), prediction)

    service.materialize_pending_outcomes(object(), prediction, config)
    second = service.materialize_pending_outcomes(object(), prediction, config)

    assert second.forward_outcome_count == 0
    assert second.target_stop_outcome_count == 0
    assert len(repository.forward_outcomes) == 10
    assert len(repository.target_stop_outcomes) == 2
    assert len(repository.outcome_definitions) == 2


def _prediction() -> WinnerPredictionSnapshot:
    return WinnerPredictionSnapshot(
        id=1,
        run_id=7,
        ticker="MSFT",
        prediction_as_of_date=date(2026, 7, 31),
        source_data_cutoff_at=datetime(2026, 7, 31, 21, 0, tzinfo=UTC),
        planned_entry_session=date(2026, 8, 3),
        entry_schedule_status="RESOLVED",
        entry_data_status="NOT_DUE",
        eligibility_status="ELIGIBLE",
        feature_schema_version="owpe-features-1.0.0",
        feature_vector_hash="hash",
        config_hash="config-hash",
        calculation_version="owpe-calc-1.0.0",
        feature_json={"ticker": "MSFT"},
    )
