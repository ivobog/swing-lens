from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from _phase3_helpers import FakeWinnerRepository

from app.models.tables import (
    WinnerForwardOutcome,
    WinnerOutcomeDefinition,
    WinnerPredictionSnapshot,
)
from app.services.winner_probability.config import load_winner_probability_config
from app.services.winner_probability.decision_time_estimate_service import (
    DecisionTimeEstimateContractError,
    DecisionTimeEstimateService,
)


def test_decision_time_stub_persists_insufficient_record() -> None:
    config = load_winner_probability_config()
    repository = FakeWinnerRepository()
    service = DecisionTimeEstimateService(repository)
    prediction = _prediction()
    definition = _definition()
    repository.add(object(), prediction)
    repository.add(object(), definition)

    result = service.create_decision_time_estimate(
        object(),
        prediction=prediction,
        outcome_definition=definition,
        config=config,
    )

    assert result.status == "insufficient"
    assert result.estimate.estimate_kind == "DECISION_TIME"
    assert result.estimate.source == "INSUFFICIENT"
    assert result.estimate.training_cutoff_at == prediction.source_data_cutoff_at
    assert repository.estimates[0] is result.estimate


def test_decision_time_contract_rejects_evidence_maturing_at_or_after_cutoff() -> None:
    service = DecisionTimeEstimateService(FakeWinnerRepository())
    prediction = _prediction()
    cutoff = prediction.source_data_cutoff_at

    service.validate_evidence_cutoff(
        prediction,
        [_outcome(matured_at=cutoff - timedelta(seconds=1))],
    )

    with pytest.raises(DecisionTimeEstimateContractError, match="at or after"):
        service.validate_evidence_cutoff(prediction, [_outcome(matured_at=cutoff)])

    with pytest.raises(DecisionTimeEstimateContractError, match="must be mature"):
        service.validate_evidence_cutoff(prediction, [_outcome(matured_at=None)])


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


def _definition() -> WinnerOutcomeDefinition:
    return WinnerOutcomeDefinition(
        id=1,
        definition_id="T2_5_S2_0_H5_NEXT_OPEN",
        label="test",
        entry_model="NEXT_OPEN",
        horizon_sessions=5,
        target_pct=2.5,
        stop_pct=2.0,
        calculation_version="owpe-calc-1.0.0",
        config_hash="config-hash",
    )


def _outcome(datetime_at: datetime | None = None, *, matured_at: datetime | None):
    return WinnerForwardOutcome(
        id=1,
        prediction_id=2,
        entry_model="NEXT_OPEN",
        horizon_sessions=5,
        status="MATURED" if matured_at else "PENDING",
        matured_at=matured_at or datetime_at,
    )
