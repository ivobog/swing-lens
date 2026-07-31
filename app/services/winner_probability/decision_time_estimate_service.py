from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.tables import (
    WinnerForwardOutcome,
    WinnerOutcomeDefinition,
    WinnerPredictionSnapshot,
    WinnerProbabilityEstimate,
)
from app.services.winner_probability.config import WinnerProbabilityConfig
from app.services.winner_probability.probability_estimator import (
    COHORT_BASELINE_SOURCE_VERSION,
    ProbabilityEstimator,
)
from app.services.winner_probability.repository import WinnerProbabilityRepository

PHASE_3_STUB_SOURCE_VERSION = "phase_3_decision_time_contract_stub"


class DecisionTimeEstimateContractError(ValueError):
    pass


@dataclass(frozen=True)
class DecisionTimeEstimateResult:
    estimate: WinnerProbabilityEstimate
    status: str


class DecisionTimeEstimateService:
    def __init__(
        self,
        repository: WinnerProbabilityRepository | None = None,
        probability_estimator: ProbabilityEstimator | None = None,
    ) -> None:
        self.repository = repository or WinnerProbabilityRepository()
        self.probability_estimator = probability_estimator or ProbabilityEstimator()

    def create_decision_time_estimate(
        self,
        db: Session,
        *,
        prediction: WinnerPredictionSnapshot,
        outcome_definition: WinnerOutcomeDefinition,
        config: WinnerProbabilityConfig,
    ) -> DecisionTimeEstimateResult:
        existing = self.repository.get_decision_time_estimate(
            db,
            prediction_id=prediction.id,
            outcome_definition_id=outcome_definition.id,
            source_version=COHORT_BASELINE_SOURCE_VERSION,
            training_cutoff_at=prediction.source_data_cutoff_at,
        )
        if existing is not None:
            return DecisionTimeEstimateResult(estimate=existing, status="duplicate")
        result = self.probability_estimator.create_decision_time_estimate(
            db,
            prediction=prediction,
            outcome_definition=outcome_definition,
            config=config,
        )
        return DecisionTimeEstimateResult(estimate=result.estimate, status=result.status)

    def validate_evidence_cutoff(
        self,
        prediction: WinnerPredictionSnapshot,
        outcomes: list[WinnerForwardOutcome],
    ) -> None:
        for outcome in outcomes:
            if outcome.matured_at is None:
                raise DecisionTimeEstimateContractError(
                    "decision-time evidence must be mature before prediction cutoff"
                )
            if outcome.matured_at >= prediction.source_data_cutoff_at:
                raise DecisionTimeEstimateContractError(
                    "decision-time evidence cannot mature at or after prediction cutoff"
                )
