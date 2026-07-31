from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.tables import (
    EstimateKind,
    EstimateSource,
    EvidenceGrade,
    WinnerForwardOutcome,
    WinnerOutcomeDefinition,
    WinnerPredictionSnapshot,
    WinnerProbabilityEstimate,
)
from app.services.winner_probability.config import WinnerProbabilityConfig
from app.services.winner_probability.repository import WinnerProbabilityRepository

PHASE_3_STUB_SOURCE_VERSION = "phase_3_decision_time_contract_stub"


class DecisionTimeEstimateContractError(ValueError):
    pass


@dataclass(frozen=True)
class DecisionTimeEstimateResult:
    estimate: WinnerProbabilityEstimate
    status: str


class DecisionTimeEstimateService:
    def __init__(self, repository: WinnerProbabilityRepository | None = None) -> None:
        self.repository = repository or WinnerProbabilityRepository()

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
            source_version=PHASE_3_STUB_SOURCE_VERSION,
            training_cutoff_at=prediction.source_data_cutoff_at,
        )
        if existing is not None:
            return DecisionTimeEstimateResult(estimate=existing, status="duplicate")

        estimate = WinnerProbabilityEstimate(
            prediction_id=prediction.id,
            outcome_definition_id=outcome_definition.id,
            estimate_kind=EstimateKind.DECISION_TIME,
            source=EstimateSource.INSUFFICIENT,
            source_version=PHASE_3_STUB_SOURCE_VERSION,
            training_cutoff_at=prediction.source_data_cutoff_at,
            evidence_grade=EvidenceGrade.INSUFFICIENT,
            insufficient_reasons_json=["phase_6_estimator_not_implemented"],
            config_hash=config.config_hash,
            feature_schema_version=config.feature_schema.version,
            metadata_json={
                "immutable_decision_time_contract": True,
                "stub_until_phase_6": True,
            },
        )
        self.repository.add(db, estimate)
        return DecisionTimeEstimateResult(estimate=estimate, status="insufficient")

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
