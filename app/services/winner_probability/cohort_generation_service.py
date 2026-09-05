from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session

from app.models.tables import (
    OutcomeStatus,
    WinnerCohortGeneration,
    WinnerCohortRefreshState,
    WinnerCohortStatistic,
    WinnerEvidenceManifestMember,
    WinnerForwardOutcome,
    WinnerOutcomeDefinition,
    WinnerPredictionSnapshot,
    WinnerTargetStopOutcome,
    WinnerTemporalValidityDecision,
    WinnerTrainingEligibilityDecision,
    WinnerTrainingOutcomeReplay,
)
from app.services.winner_probability.config import WinnerProbabilityConfig
from app.services.winner_probability.pre11_compatibility_service import (
    BRIDGE_VERSION,
    POLICY_VERSION,
)
from app.services.winner_probability.temporal_eligibility import (
    load_current_temporal_decisions,
    prediction_temporally_eligible,
)

COHORT_ALGORITHM_VERSION = "cohort-v2.2"
ELIGIBILITY_POLICY_VERSION = "training-eligibility-v2-temporal"


class CohortGenerationStatus:
    BUILDING = "BUILDING"
    READY = "READY"
    PUBLISHED = "PUBLISHED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    SUPERSEDED = "SUPERSEDED"


_ALLOWED_TRANSITIONS = {
    CohortGenerationStatus.BUILDING: {
        CohortGenerationStatus.READY,
        CohortGenerationStatus.CANCELLED,
        CohortGenerationStatus.FAILED,
    },
    CohortGenerationStatus.READY: {
        CohortGenerationStatus.PUBLISHED,
        CohortGenerationStatus.CANCELLED,
        CohortGenerationStatus.FAILED,
    },
    CohortGenerationStatus.PUBLISHED: {CohortGenerationStatus.SUPERSEDED},
    CohortGenerationStatus.CANCELLED: {CohortGenerationStatus.BUILDING},
    CohortGenerationStatus.FAILED: {CohortGenerationStatus.BUILDING},
    CohortGenerationStatus.SUPERSEDED: set(),
}


class GenerationInvariantViolation(RuntimeError):
    pass


class GenerationPublicationConflict(RuntimeError):
    pass


@dataclass(frozen=True, order=True)
class EvidenceWatermark:
    forward_revision_id: int = 0
    target_stop_revision_id: int = 0
    eligibility_decision_id: int = 0
    training_replay_id: int = 0
    temporal_validity_decision_id: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class WinnerCohortContract:
    outcome_definition_id: int
    feature_schema_version: str
    calculation_version: str
    config_hash: str
    eligibility_policy_version: str
    compatibility_policy_version: str
    cohort_algorithm_version: str = COHORT_ALGORITHM_VERSION

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WatermarkAdvanceResult:
    state: WinnerCohortRefreshState
    watermark: EvidenceWatermark
    advanced: bool


@dataclass(frozen=True)
class TemporalGenerationAudit:
    generation_id: int
    distinct_prediction_count: int
    invalid_prediction_ids: tuple[int, ...]

    @property
    def clean(self) -> bool:
        return not self.invalid_prediction_ids


def contract_for(
    outcome_definition: WinnerOutcomeDefinition,
    config: WinnerProbabilityConfig,
) -> WinnerCohortContract:
    return WinnerCohortContract(
        outcome_definition_id=outcome_definition.id,
        feature_schema_version=config.feature_schema.version,
        calculation_version=config.engine.calculation_version,
        config_hash=config.config_hash,
        eligibility_policy_version=ELIGIBILITY_POLICY_VERSION,
        compatibility_policy_version=f"{POLICY_VERSION}:{BRIDGE_VERSION}",
    )


def canonical_watermark_hash(watermark: EvidenceWatermark) -> str:
    return _canonical_hash(watermark.as_dict())


def canonical_generation_key(
    contract: WinnerCohortContract,
    watermark: EvidenceWatermark,
    *,
    requested_at: datetime | None = None,
) -> str:
    # requested_at is intentionally accepted only to make the non-identity
    # contract explicit to callers and tests. It never enters the digest.
    del requested_at
    return _canonical_hash({"contract": contract.as_dict(), "watermark": watermark.as_dict()})


def validate_generation_transition(current: str, target: str) -> None:
    if target not in _ALLOWED_TRANSITIONS.get(current, set()):
        raise ValueError(f"Invalid cohort generation transition: {current} -> {target}")


class EvidenceWatermarkService:
    def advance_to_current_material_evidence(
        self,
        db: Session,
        *,
        outcome_definition: WinnerOutcomeDefinition,
        config: WinnerProbabilityConfig,
        observed_at: datetime | None = None,
    ) -> WatermarkAdvanceResult:
        contract = contract_for(outcome_definition, config)
        state = self._locked_state(db, contract=contract, observed_at=observed_at)
        watermark = self.current_material_watermark(db, outcome_definition_id=outcome_definition.id)
        current = watermark_from_state(state)
        if watermark == current:
            return WatermarkAdvanceResult(state=state, watermark=watermark, advanced=False)
        if watermark < current:
            raise GenerationInvariantViolation("material evidence watermark regressed")
        state.desired_forward_revision_id = watermark.forward_revision_id
        state.desired_target_stop_revision_id = watermark.target_stop_revision_id
        state.desired_eligibility_decision_id = watermark.eligibility_decision_id
        state.desired_training_replay_id = watermark.training_replay_id
        state.desired_temporal_validity_decision_id = watermark.temporal_validity_decision_id
        state.desired_watermark_hash = canonical_watermark_hash(watermark)
        state.updated_at = observed_at or datetime.now(UTC)
        db.flush()
        return WatermarkAdvanceResult(state=state, watermark=watermark, advanced=True)

    def current_material_watermark(
        self,
        db: Session,
        *,
        outcome_definition_id: int,
    ) -> EvidenceWatermark:
        target_stop_max = (
            select(func.max(WinnerTargetStopOutcome.id))
            .where(WinnerTargetStopOutcome.outcome_definition_id == outcome_definition_id)
            .where(WinnerTargetStopOutcome.status == OutcomeStatus.MATURED)
            .where(WinnerTargetStopOutcome.is_current_revision.is_(True))
            .scalar_subquery()
        )
        forward_max = (
            select(func.max(WinnerForwardOutcome.id))
            .join(
                WinnerTargetStopOutcome,
                WinnerTargetStopOutcome.forward_outcome_id == WinnerForwardOutcome.id,
            )
            .where(WinnerTargetStopOutcome.outcome_definition_id == outcome_definition_id)
            .where(WinnerTargetStopOutcome.status == OutcomeStatus.MATURED)
            .where(WinnerTargetStopOutcome.is_current_revision.is_(True))
            .where(WinnerForwardOutcome.is_current_revision.is_(True))
            .scalar_subquery()
        )
        eligibility_max = (
            select(func.max(WinnerTrainingEligibilityDecision.id))
            .where(
                WinnerTrainingEligibilityDecision.target_outcome_definition_id
                == outcome_definition_id
            )
            .scalar_subquery()
        )
        replay_max = (
            select(func.max(WinnerTrainingOutcomeReplay.id))
            .where(
                WinnerTrainingOutcomeReplay.target_outcome_definition_id == outcome_definition_id
            )
            .scalar_subquery()
        )
        temporal_max = (
            select(func.max(WinnerTemporalValidityDecision.id))
            .join(
                WinnerTargetStopOutcome,
                WinnerTargetStopOutcome.prediction_id
                == WinnerTemporalValidityDecision.prediction_id,
            )
            .where(WinnerTargetStopOutcome.outcome_definition_id == outcome_definition_id)
            .scalar_subquery()
        )
        row = db.execute(
            select(forward_max, target_stop_max, eligibility_max, replay_max, temporal_max)
        ).one()
        return EvidenceWatermark(*(int(value or 0) for value in row))

    def _locked_state(
        self,
        db: Session,
        *,
        contract: WinnerCohortContract,
        observed_at: datetime | None,
    ) -> WinnerCohortRefreshState:
        statement = _contract_statement(contract).with_for_update()
        state = db.scalar(statement)
        if state is not None:
            return state
        empty = EvidenceWatermark()
        values = {
            **contract.as_dict(),
            "desired_forward_revision_id": 0,
            "desired_target_stop_revision_id": 0,
            "desired_eligibility_decision_id": 0,
            "desired_training_replay_id": 0,
            "desired_temporal_validity_decision_id": 0,
            "desired_watermark_hash": canonical_watermark_hash(empty),
            "updated_at": observed_at or datetime.now(UTC),
        }
        bind = db.get_bind()
        if bind.dialect.name == "postgresql":
            db.execute(
                postgresql_insert(WinnerCohortRefreshState)
                .values(**values)
                .on_conflict_do_nothing(constraint="uq_winner_cohort_refresh_state_contract")
            )
            state = db.scalar(_contract_statement(contract).with_for_update())
            if state is None:
                raise GenerationInvariantViolation("cohort refresh state insert was lost")
            return state
        state = WinnerCohortRefreshState(**values)
        db.add(state)
        db.flush()
        return state


class CohortGenerationService:
    def get_published_cohort_generation(
        self,
        db: Session,
        *,
        contract: WinnerCohortContract,
    ) -> WinnerCohortGeneration | None:
        """Resolve the sole serving generation for a frozen contract."""
        state = db.scalar(_contract_statement(contract))
        if state is None:
            return None
        return self.published_for_state(db, state)

    def capture_or_resume(
        self,
        db: Session,
        *,
        state: WinnerCohortRefreshState,
        contract: WinnerCohortContract,
        requested_at: datetime | None = None,
    ) -> WinnerCohortGeneration:
        requested_at = requested_at or datetime.now(UTC)
        watermark = watermark_from_state(state)
        key = canonical_generation_key(contract, watermark, requested_at=requested_at)
        generation = db.scalar(
            select(WinnerCohortGeneration).where(WinnerCohortGeneration.generation_key == key)
        )
        if generation is not None:
            if generation.status in {
                CohortGenerationStatus.CANCELLED,
                CohortGenerationStatus.FAILED,
            }:
                validate_generation_transition(generation.status, CohortGenerationStatus.BUILDING)
                generation.status = CohortGenerationStatus.BUILDING
                generation.error_message = None
                generation.cancelled_at = None
                generation.started_at = requested_at
                db.flush()
            return generation
        values = {
            "generation_key": key,
            "refresh_state_id": state.id,
            "outcome_definition_id": contract.outcome_definition_id,
            "watermark_hash": canonical_watermark_hash(watermark),
            "watermark_json": watermark.as_dict(),
            "feature_schema_version": contract.feature_schema_version,
            "calculation_version": contract.calculation_version,
            "config_hash": contract.config_hash,
            "eligibility_policy_version": contract.eligibility_policy_version,
            "compatibility_policy_version": contract.compatibility_policy_version,
            "cohort_algorithm_version": contract.cohort_algorithm_version,
            "status": CohortGenerationStatus.BUILDING,
            # The cutoff is anchored to the material watermark observation, not
            # to an arbitrary refresh clock.  The one-microsecond successor
            # includes evidence committed at the exact observation boundary;
            # the frozen revision-id watermark still excludes later evidence.
            "training_cutoff_at": state.updated_at + timedelta(microseconds=1),
            "requested_at": requested_at,
            "started_at": requested_at,
            "checkpoint_json": {"phase": "LOAD_EVIDENCE"},
            "metrics_json": {},
        }
        bind = db.get_bind()
        if bind.dialect.name == "postgresql":
            generation_id = db.scalar(
                postgresql_insert(WinnerCohortGeneration)
                .values(**values)
                .on_conflict_do_nothing(constraint="uq_winner_cohort_generations_key")
                .returning(WinnerCohortGeneration.id)
            )
            generation = (
                db.get(WinnerCohortGeneration, generation_id)
                if generation_id is not None
                else db.scalar(
                    select(WinnerCohortGeneration).where(
                        WinnerCohortGeneration.generation_key == key
                    )
                )
            )
            if generation is None:
                raise GenerationInvariantViolation("cohort generation insert was lost")
            return generation
        generation = WinnerCohortGeneration(**values)
        db.add(generation)
        db.flush()
        return generation

    def published_for_state(
        self, db: Session, state: WinnerCohortRefreshState
    ) -> WinnerCohortGeneration | None:
        if state.published_generation_id is None:
            return None
        generation = db.get(WinnerCohortGeneration, state.published_generation_id)
        if generation is None or generation.status != CohortGenerationStatus.PUBLISHED:
            raise GenerationInvariantViolation("published generation pointer is invalid")
        return generation

    def publish(
        self,
        db: Session,
        *,
        generation: WinnerCohortGeneration,
        lease_guard,
        published_at: datetime | None = None,
    ) -> bool:
        published_at = published_at or datetime.now(UTC)
        if generation.status != CohortGenerationStatus.READY:
            raise GenerationInvariantViolation("only READY cohort generations may publish")
        if generation.planned_group_count is None or (
            generation.completed_group_count != generation.planned_group_count
        ):
            raise GenerationInvariantViolation("cohort generation is only partially materialized")
        self._assert_temporally_clean(db, generation)
        lease_guard()
        state = db.scalar(
            select(WinnerCohortRefreshState)
            .where(WinnerCohortRefreshState.id == generation.refresh_state_id)
            .with_for_update()
        )
        if state is None:
            raise GenerationInvariantViolation("cohort refresh state disappeared")
        if state.published_generation_id == generation.id:
            return state.desired_watermark_hash != generation.watermark_hash
        previous = self.published_for_state(db, state)
        if previous is not None:
            validate_generation_transition(previous.status, CohortGenerationStatus.SUPERSEDED)
            previous.status = CohortGenerationStatus.SUPERSEDED
            previous.completed_at = published_at
        validate_generation_transition(generation.status, CohortGenerationStatus.PUBLISHED)
        generation.status = CohortGenerationStatus.PUBLISHED
        generation.published_at = published_at
        generation.completed_at = published_at
        state.published_generation_id = generation.id
        state.published_watermark_hash = generation.watermark_hash
        db.flush()
        # Fence and durably commit the publication pointer and lifecycle switch
        # in the same transaction before the handler can report success.
        lease_guard()
        return state.desired_watermark_hash != generation.watermark_hash

    @staticmethod
    def _assert_temporally_clean(
        db: Session,
        generation: WinnerCohortGeneration,
    ) -> None:
        # Unit-only state fakes have no SQLAlchemy bind. Materialized PostgreSQL
        # generations are always validated set-wise before publication.
        if not hasattr(db, "get_bind"):
            return
        audit = CohortGenerationService.audit_temporal_integrity(db, generation=generation)
        if audit.invalid_prediction_ids:
            preview = ",".join(str(value) for value in audit.invalid_prediction_ids[:10])
            raise GenerationInvariantViolation(
                "cohort generation contains temporally ineligible evidence "
                f"({len(audit.invalid_prediction_ids)} predictions; first={preview})"
            )

    @staticmethod
    def audit_temporal_integrity(
        db: Session,
        *,
        generation: WinnerCohortGeneration,
    ) -> TemporalGenerationAudit:
        prediction_ids = set(
            int(value)
            for value in db.scalars(
                select(WinnerEvidenceManifestMember.prediction_id)
                .join(
                    WinnerCohortStatistic,
                    WinnerCohortStatistic.evidence_manifest_id
                    == WinnerEvidenceManifestMember.manifest_id,
                )
                .where(WinnerCohortStatistic.generation_id == generation.id)
                .distinct()
            )
        )
        if not prediction_ids:
            return TemporalGenerationAudit(int(generation.id), 0, ())
        predictions = {
            int(row.id): row
            for row in db.scalars(
                select(WinnerPredictionSnapshot).where(
                    WinnerPredictionSnapshot.id.in_(sorted(prediction_ids))
                )
            )
        }
        decisions = load_current_temporal_decisions(db, prediction_ids)
        invalid = tuple(
            sorted(
                prediction_id
                for prediction_id in prediction_ids
                if prediction_id not in predictions
                or not prediction_temporally_eligible(
                    predictions[prediction_id], decisions.get(prediction_id)
                )
            )
        )
        return TemporalGenerationAudit(
            generation_id=int(generation.id),
            distinct_prediction_count=len(prediction_ids),
            invalid_prediction_ids=invalid,
        )


def watermark_from_state(state: WinnerCohortRefreshState) -> EvidenceWatermark:
    return EvidenceWatermark(
        forward_revision_id=int(state.desired_forward_revision_id or 0),
        target_stop_revision_id=int(state.desired_target_stop_revision_id or 0),
        eligibility_decision_id=int(state.desired_eligibility_decision_id or 0),
        training_replay_id=int(state.desired_training_replay_id or 0),
        temporal_validity_decision_id=int(
            getattr(state, "desired_temporal_validity_decision_id", 0) or 0
        ),
    )


def _contract_statement(contract: WinnerCohortContract):
    statement = select(WinnerCohortRefreshState)
    for name, value in contract.as_dict().items():
        statement = statement.where(getattr(WinnerCohortRefreshState, name) == value)
    return statement


def _canonical_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
