from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from _phase3_helpers import FakeWinnerRepository

from app.models.tables import (
    WinnerForwardOutcome,
    WinnerOutcomeDefinition,
    WinnerPredictionSnapshot,
    WinnerProbabilityEstimate,
)
from app.services.winner_probability.config import load_winner_probability_config
from app.services.winner_probability.decision_time_estimate_service import (
    DecisionTimeEstimateContractError,
    DecisionTimeEstimateService,
)


def test_decision_time_service_persists_real_estimator_contract() -> None:
    config = load_winner_probability_config()
    repository = FakeWinnerRepository()
    prediction = _prediction()
    definition = _definition()
    estimate = WinnerProbabilityEstimate(
        id=9,
        prediction_id=prediction.id,
        outcome_definition_id=definition.id,
        estimate_kind="DECISION_TIME",
        source="COHORT",
        source_version="cohort_baseline_v1",
        training_cutoff_at=prediction.source_data_cutoff_at,
        evidence_grade="Low",
        config_hash=config.config_hash,
        feature_schema_version=config.feature_schema.version,
    )
    service = DecisionTimeEstimateService(
        repository,
        probability_estimator=FakeProbabilityEstimator(estimate),
    )

    result = service.create_decision_time_estimate(
        object(),
        prediction=prediction,
        outcome_definition=definition,
        config=config,
    )

    assert result.status == "estimated"
    assert result.estimate.estimate_kind == "DECISION_TIME"
    assert result.estimate.source == "COHORT"
    assert result.estimate.training_cutoff_at == prediction.source_data_cutoff_at


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


class FakeProbabilityEstimator:
    def __init__(self, estimate: WinnerProbabilityEstimate) -> None:
        self.estimate = estimate

    def create_decision_time_estimate(self, _db, **_kwargs):
        return type(
            "Result",
            (),
            {
                "estimate": self.estimate,
                "status": "estimated",
            },
        )()
