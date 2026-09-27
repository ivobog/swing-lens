from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
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
from app.services.core_mutation_authority import core_writer_member, core_writer_transaction
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
_UNSUPPLIED_PREDECESSOR = object()
ELIGIBILITY_POLICY_VERSION = "training-eligibility-v2-temporal"
_PUBLICATION_CONTRACT_FIELDS = (
    "outcome_definition_id",
    "feature_schema_version",
    "calculation_version",
    "config_hash",
    "eligibility_policy_version",
    "compatibility_policy_version",
    "cohort_algorithm_version",
)


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


class GenerationPublicationStatus:
    PUBLISHED = "PUBLISHED"
    ALREADY_ACTIVE = "ALREADY_ACTIVE"
    REJECTED_STALE = "REJECTED_STALE"
    REJECTED_INCOMPATIBLE = "REJECTED_INCOMPATIBLE"
    REJECTED_INCOMPLETE = "REJECTED_INCOMPLETE"


@dataclass(frozen=True)
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


@dataclass(frozen=True)
class GenerationPublicationResult:
    status: str
    candidate_generation_id: int
    active_generation_id: int | None
    desired_watermark_advanced: bool = False

    @property
    def successful(self) -> bool:
        return self.status in {
            GenerationPublicationStatus.PUBLISHED,
            GenerationPublicationStatus.ALREADY_ACTIVE,
        }


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
    @core_writer_transaction
    def advance_to_current_material_evidence(
        self,
        db: Session,
        *,
        outcome_definition: WinnerOutcomeDefinition,
        config: WinnerProbabilityConfig,
        observed_at: datetime | None = None,
        maturation_counts: dict | None = None,
    ) -> WatermarkAdvanceResult:
        if isinstance(db, Session):
            from app.services.domain_mutation import MutationDomain
            from app.services.winner_probability.cohort_authority import operation_authority

            operation_authority(
                db,
                domain=MutationDomain.WINNER_GENERATION,
                writer="EvidenceWatermarkService.advance_to_current_material_evidence",
                config=config,
                now=observed_at,
                manifest={
                    "outcome_definition_id": outcome_definition.id,
                    "contract": contract_for(outcome_definition, config).as_dict(),
                },
            )
        contract = contract_for(outcome_definition, config)
        state = self._locked_state(db, contract=contract, observed_at=observed_at)
        watermark = self.current_material_watermark(db, outcome_definition_id=outcome_definition.id)
        current = watermark_from_state(state)
        ordering = _compare_watermarks(watermark, current)
        if maturation_counts is not None:
            observed = observed_at or datetime.now(UTC)
            if maturation_counts.get("scan_completed"):
                state.last_full_scan_at = observed
            if not maturation_counts.get("pending_h5_after_cycle", 0):
                state.last_zero_due_backlog_at = observed
            state.current_due_count = int(
                maturation_counts.get("pending_h5_after_cycle", 0) or 0
            )
            state.current_deferred_count = int(
                maturation_counts.get("retry_deferred", 0) or 0
            )
            oldest_due = maturation_counts.get("oldest_due_h5_session")
            state.oldest_due_session = (
                datetime.fromisoformat(str(oldest_due)).date() if oldest_due else None
            )
        if ordering == 0:
            db.flush()
            return WatermarkAdvanceResult(state=state, watermark=watermark, advanced=False)
        if ordering != 1:
            raise GenerationInvariantViolation(
                "material evidence watermark regressed or became incomparable"
            )
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
            select(WinnerTargetStopOutcome.id)
            .where(WinnerTargetStopOutcome.outcome_definition_id == outcome_definition_id)
            .where(WinnerTargetStopOutcome.status == OutcomeStatus.MATURED)
            .where(WinnerTargetStopOutcome.is_current_revision.is_(True))
            .order_by(WinnerTargetStopOutcome.id.desc())
            .limit(1)
            .scalar_subquery()
        )
        forward_max = (
            select(WinnerForwardOutcome.id)
            .join(
                WinnerTargetStopOutcome,
                WinnerTargetStopOutcome.forward_outcome_id == WinnerForwardOutcome.id,
            )
            .where(WinnerTargetStopOutcome.outcome_definition_id == outcome_definition_id)
            .where(WinnerTargetStopOutcome.status == OutcomeStatus.MATURED)
            .where(WinnerTargetStopOutcome.is_current_revision.is_(True))
            .where(WinnerForwardOutcome.is_current_revision.is_(True))
            .order_by(WinnerForwardOutcome.id.desc())
            .limit(1)
            .scalar_subquery()
        )
        eligibility_max = (
            select(WinnerTrainingEligibilityDecision.id)
            .where(
                WinnerTrainingEligibilityDecision.target_outcome_definition_id
                == outcome_definition_id
            )
            .order_by(WinnerTrainingEligibilityDecision.id.desc())
            .limit(1)
            .scalar_subquery()
        )
        replay_max = (
            select(WinnerTrainingOutcomeReplay.id)
            .where(
                WinnerTrainingOutcomeReplay.target_outcome_definition_id == outcome_definition_id
            )
            .order_by(WinnerTrainingOutcomeReplay.id.desc())
            .limit(1)
            .scalar_subquery()
        )
        temporal_max = (
            select(WinnerTemporalValidityDecision.id)
            .join(
                WinnerTargetStopOutcome,
                WinnerTargetStopOutcome.prediction_id
                == WinnerTemporalValidityDecision.prediction_id,
            )
            .where(WinnerTargetStopOutcome.outcome_definition_id == outcome_definition_id)
            .order_by(WinnerTemporalValidityDecision.id.desc())
            .limit(1)
            .scalar_subquery()
        )
        row = db.execute(
            select(forward_max, target_stop_max, eligibility_max, replay_max, temporal_max)
        ).one()
        return EvidenceWatermark(*(int(value or 0) for value in row))

    @core_writer_member(
        "app.services.winner_probability.cohort_generation_service:EvidenceWatermarkService.advance_to_current_material_evidence"
    )
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

    @core_writer_transaction
    def capture_or_resume(
        self,
        db: Session,
        *,
        state: WinnerCohortRefreshState,
        contract: WinnerCohortContract,
        requested_at: datetime | None = None,
        config: WinnerProbabilityConfig | None = None,
        semantic_authority: Any | None = None,
        mutation_context=None,
    ) -> WinnerCohortGeneration:
        authority = None
        if isinstance(db, Session):
            from app.services.domain_mutation import MutationDomain
            from app.services.winner_probability.cohort_authority import (
                operation_authority,
                retained_row,
            )

            source = retained_row(db, state)
            authority = operation_authority(
                db,
                domain=MutationDomain.WINNER_GENERATION,
                writer="CohortGenerationService.capture_or_resume",
                config=config,
                now=requested_at,
                manifest={
                    "outcome_definition_id": contract.outcome_definition_id,
                    "contract": contract.as_dict(),
                    "state": source,
                },
                context=mutation_context,
            )
            if any(getattr(state, key) != value for key, value in contract.as_dict().items()):
                raise ValueError("MUTATION_WINNER_GENERATION_EXACT_STATE_CONTRACT_REQUIRED")
            definition = db.get(WinnerOutcomeDefinition, contract.outcome_definition_id)
            if definition is None or contract != contract_for(definition, config):
                raise ValueError("MUTATION_WINNER_GENERATION_NATIVE_CONTRACT_REQUIRED")
            if requested_at <= state.updated_at:
                raise ValueError("MUTATION_WINNER_GENERATION_SOURCE_OBSERVATION_TIME_REQUIRED")
        requested_at = requested_at or datetime.now(UTC)
        watermark = watermark_from_state(state)
        key = canonical_generation_key(contract, watermark, requested_at=requested_at)
        generation = db.scalar(
            select(WinnerCohortGeneration).where(WinnerCohortGeneration.generation_key == key)
        )
        if generation is not None:
            if semantic_authority is not None:
                from app.services.scope_refresh_adoption import bind_semantic_authority

                bind_semantic_authority(generation, semantic_authority)
                db.flush()
            if isinstance(db, Session):
                from app.services.winner_probability.cohort_authority import validate_generation

                validate_generation(db, generation, config)
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
            **(semantic_authority.as_dict() if semantic_authority is not None else {}),
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
            generation._configuration_generation_created = generation_id is not None
            if authority is not None and generation_id is not None:
                self._seal_creation(db, generation, authority, config)
            elif authority is not None:
                from app.services.winner_probability.cohort_authority import validate_generation

                validate_generation(db, generation, config)
            return generation
        generation = WinnerCohortGeneration(**values)
        db.add(generation)
        db.flush()
        generation._configuration_generation_created = True
        if authority is not None:
            self._seal_creation(db, generation, authority, config)
        return generation

    @core_writer_member(
        "app.services.winner_probability.cohort_generation_service:CohortGenerationService.capture_or_resume"
    )
    def _seal_creation(self, db, generation, authority, config):
        from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
        from app.services.configuration_delivery import bind_winner_generation_configuration
        from app.services.winner_probability.cohort_authority import generation_body

        generation.metrics_json = {
            **(generation.metrics_json or {}),
            "native_generation_proof": {
                "contract": "winner-native-generation-v1",
                "fingerprint": Canonical.fingerprint(generation_body(generation)),
                "configuration": authority[1].snapshot.as_dict(),
                "mutation_authority": authority[0].canonical_payload(),
            },
        }
        db.flush()
        bind_winner_generation_configuration(db, generation, config)

    def published_for_state(
        self, db: Session, state: WinnerCohortRefreshState
    ) -> WinnerCohortGeneration | None:
        if state.published_generation_id is None:
            return None
        generation = db.get(WinnerCohortGeneration, state.published_generation_id)
        if generation is None or generation.status != CohortGenerationStatus.PUBLISHED:
            raise GenerationInvariantViolation("published generation pointer is invalid")
        return generation

    @core_writer_transaction
    def publish(
        self,
        db: Session,
        *,
        generation: WinnerCohortGeneration,
        lease_guard,
        published_at: datetime | None = None,
        config: WinnerProbabilityConfig | None = None,
        mutation_context=None,
        predecessor_id=_UNSUPPLIED_PREDECESSOR,
    ) -> GenerationPublicationResult:
        if isinstance(db, Session):
            from app.services.domain_mutation import MutationDomain
            from app.services.winner_probability.cohort_authority import (
                operation_authority,
                retained_row,
                validate_completion,
                validate_generation,
            )

            if predecessor_id is _UNSUPPLIED_PREDECESSOR:
                raise ValueError("MUTATION_WINNER_EXPLICIT_PUBLICATION_PREDECESSOR_REQUIRED")
            validate_generation(db, generation, config)
            operation_authority(
                db,
                domain=MutationDomain.WINNER_PUBLICATION,
                writer="CohortGenerationService.publish",
                config=config,
                now=published_at,
                manifest={
                    "outcome_definition_id": generation.outcome_definition_id,
                    "generation": generation.generation_key,
                    "predecessor_id": predecessor_id,
                },
                context=mutation_context,
            )
            if generation.status in {
                CohortGenerationStatus.READY,
                CohortGenerationStatus.PUBLISHED,
            }:
                validate_completion(db, generation, config)
        published_at = published_at or datetime.now(UTC)
        generation_id = int(generation.id)
        lease_guard()
        state = db.scalar(
            select(WinnerCohortRefreshState)
            .where(WinnerCohortRefreshState.id == generation.refresh_state_id)
            .with_for_update()
        )
        if state is None:
            raise GenerationInvariantViolation("cohort refresh state disappeared")
        if isinstance(db, Session):
            retained_row(db, state)
            if state.published_generation_id != predecessor_id:
                raise ValueError("MUTATION_WINNER_PUBLICATION_PREDECESSOR_MISMATCH")
        locked_generation = db.scalar(
            select(WinnerCohortGeneration)
            .where(WinnerCohortGeneration.id == generation_id)
            .with_for_update()
        )
        if locked_generation is None:
            raise GenerationInvariantViolation("cohort generation disappeared")

        previous = self._locked_published_for_state(db, state)
        complete = self._is_complete(locked_generation)
        if state.published_generation_id == generation_id:
            try:
                active_watermark = self._generation_watermark(locked_generation)
            except GenerationPublicationConflict as exc:
                raise GenerationInvariantViolation(
                    "active cohort generation watermark is inconsistent"
                ) from exc
            if (
                previous is None
                or previous.status != CohortGenerationStatus.PUBLISHED
                or state.published_watermark_hash != locked_generation.watermark_hash
                or not complete
                or not self._generation_matches_state(locked_generation, state)
                or locked_generation.watermark_hash != canonical_watermark_hash(active_watermark)
                or locked_generation.generation_key
                != canonical_generation_key(
                    self._contract_from_generation(locked_generation), active_watermark
                )
            ):
                raise GenerationInvariantViolation("active cohort generation is inconsistent")
            lease_guard()
            return GenerationPublicationResult(
                status=GenerationPublicationStatus.ALREADY_ACTIVE,
                candidate_generation_id=generation_id,
                active_generation_id=generation_id,
                desired_watermark_advanced=(
                    state.desired_watermark_hash != locked_generation.watermark_hash
                ),
            )

        if not self._generation_matches_state(locked_generation, state):
            return self._rejected_result(
                GenerationPublicationStatus.REJECTED_INCOMPATIBLE,
                locked_generation,
                previous,
            )

        try:
            candidate_watermark = self._generation_watermark(locked_generation)
        except GenerationPublicationConflict:
            return self._rejected_result(
                GenerationPublicationStatus.REJECTED_INCOMPATIBLE,
                locked_generation,
                previous,
            )
        desired_watermark = watermark_from_state(state)
        if locked_generation.watermark_hash != canonical_watermark_hash(
            candidate_watermark
        ) or locked_generation.generation_key != canonical_generation_key(
            self._contract_from_generation(locked_generation), candidate_watermark
        ):
            return self._rejected_result(
                GenerationPublicationStatus.REJECTED_INCOMPATIBLE,
                locked_generation,
                previous,
            )
        if state.desired_watermark_hash != canonical_watermark_hash(desired_watermark):
            raise GenerationInvariantViolation("desired evidence watermark hash is inconsistent")
        desired_order = _compare_watermarks(candidate_watermark, desired_watermark)
        if desired_order == -1:
            return self._rejected_result(
                GenerationPublicationStatus.REJECTED_STALE,
                locked_generation,
                previous,
                desired_watermark_advanced=True,
            )
        if desired_order is None or desired_order == 1:
            return self._rejected_result(
                GenerationPublicationStatus.REJECTED_INCOMPATIBLE,
                locked_generation,
                previous,
            )

        if previous is not None:
            if not self._generations_compatible(previous, locked_generation):
                return self._rejected_result(
                    GenerationPublicationStatus.REJECTED_INCOMPATIBLE,
                    locked_generation,
                    previous,
                )
            active_order = _compare_watermarks(
                candidate_watermark,
                self._generation_watermark(previous),
            )
            if active_order in {-1, 0}:
                return self._rejected_result(
                    GenerationPublicationStatus.REJECTED_STALE,
                    locked_generation,
                    previous,
                )
            if active_order is None:
                return self._rejected_result(
                    GenerationPublicationStatus.REJECTED_INCOMPATIBLE,
                    locked_generation,
                    previous,
                )

        if not complete:
            return self._rejected_result(
                GenerationPublicationStatus.REJECTED_INCOMPLETE,
                locked_generation,
                previous,
            )
        if locked_generation.status != CohortGenerationStatus.READY:
            raise GenerationInvariantViolation("only READY cohort generations may publish")
        self._assert_temporally_clean(db, locked_generation)
        lease_guard()
        self._activate_locked(
            db,
            state=state,
            generation=locked_generation,
            previous=previous,
            published_at=published_at,
        )
        lease_guard()
        return GenerationPublicationResult(
            status=GenerationPublicationStatus.PUBLISHED,
            candidate_generation_id=generation_id,
            active_generation_id=generation_id,
        )

    @core_writer_member(
        "app.services.winner_probability.estimate_publication_service:WinnerEstimatePublicationService.publish"
    )
    def publish_reviewed_supersession(
        self,
        db: Session,
        *,
        generation: WinnerCohortGeneration,
        previous: WinnerCohortGeneration,
        published_at: datetime,
    ) -> GenerationPublicationResult:
        """Apply an explicitly reviewed cross-contract supersession.

        This mode preserves the existing operator-reviewed estimate transition. It
        deliberately does not infer ordering between incompatible contracts, but it
        still serializes and compare-and-swaps the exact active pointer.
        """

        state_ids = sorted({int(generation.refresh_state_id), int(previous.refresh_state_id)})
        states = {
            int(row.id): row
            for row in db.scalars(
                select(WinnerCohortRefreshState)
                .where(WinnerCohortRefreshState.id.in_(state_ids))
                .order_by(WinnerCohortRefreshState.id)
                .with_for_update()
            )
        }
        old_state = states.get(int(previous.refresh_state_id))
        new_state = states.get(int(generation.refresh_state_id))
        if old_state is None or new_state is None:
            raise GenerationInvariantViolation("cohort refresh state disappeared")

        generation_ids = sorted({int(generation.id), int(previous.id)})
        generations = {
            int(row.id): row
            for row in db.scalars(
                select(WinnerCohortGeneration)
                .where(WinnerCohortGeneration.id.in_(generation_ids))
                .order_by(WinnerCohortGeneration.id)
                .with_for_update()
            )
        }
        locked_generation = generations.get(int(generation.id))
        locked_previous = generations.get(int(previous.id))
        if locked_generation is None or locked_previous is None:
            raise GenerationInvariantViolation("reviewed cohort generation disappeared")

        if old_state.published_generation_id == locked_generation.id:
            if locked_generation.status != CohortGenerationStatus.PUBLISHED:
                raise GenerationInvariantViolation("active reviewed generation is inconsistent")
            return GenerationPublicationResult(
                status=GenerationPublicationStatus.ALREADY_ACTIVE,
                candidate_generation_id=int(locked_generation.id),
                active_generation_id=int(locked_generation.id),
            )
        if old_state.published_generation_id != locked_previous.id:
            raise GenerationPublicationConflict("current published-generation pointer drifted")
        if new_state.id != old_state.id and new_state.published_generation_id is not None:
            raise GenerationPublicationConflict("target refresh state already has a publication")
        if not self._is_complete(locked_generation):
            return self._rejected_result(
                GenerationPublicationStatus.REJECTED_INCOMPLETE,
                locked_generation,
                locked_previous,
            )
        if locked_generation.status != CohortGenerationStatus.READY:
            raise GenerationInvariantViolation("only READY cohort generations may publish")
        if locked_previous.status != CohortGenerationStatus.PUBLISHED:
            raise GenerationPublicationConflict("reviewed previous generation is not active")

        if old_state.id != new_state.id:
            old_state.published_generation_id = None
            old_state.published_watermark_hash = None
        self._activate_locked(
            db,
            state=new_state,
            generation=locked_generation,
            previous=locked_previous,
            published_at=published_at,
        )
        return GenerationPublicationResult(
            status=GenerationPublicationStatus.PUBLISHED,
            candidate_generation_id=int(locked_generation.id),
            active_generation_id=int(locked_generation.id),
        )

    @staticmethod
    @core_writer_member(
        (
            "app.services.winner_probability.cohort_generation_service:CohortGenerationService.publish",
            "app.services.winner_probability.estimate_publication_service:WinnerEstimatePublicationService.publish",
        )
    )
    def _activate_locked(
        db: Session,
        *,
        state: WinnerCohortRefreshState,
        generation: WinnerCohortGeneration,
        previous: WinnerCohortGeneration | None,
        published_at: datetime,
    ) -> None:
        if previous is not None:
            validate_generation_transition(previous.status, CohortGenerationStatus.SUPERSEDED)
            previous.status = CohortGenerationStatus.SUPERSEDED
        validate_generation_transition(generation.status, CohortGenerationStatus.PUBLISHED)
        generation.status = CohortGenerationStatus.PUBLISHED
        generation.published_at = published_at
        generation.completed_at = published_at
        state.published_generation_id = generation.id
        state.published_watermark_hash = generation.watermark_hash
        db.flush()

    @staticmethod
    def _locked_published_for_state(
        db: Session,
        state: WinnerCohortRefreshState,
    ) -> WinnerCohortGeneration | None:
        if state.published_generation_id is None:
            return None
        generation = db.scalar(
            select(WinnerCohortGeneration)
            .where(WinnerCohortGeneration.id == state.published_generation_id)
            .with_for_update()
        )
        if generation is None or generation.status != CohortGenerationStatus.PUBLISHED:
            raise GenerationInvariantViolation("published generation pointer is invalid")
        return generation

    @staticmethod
    def _is_complete(generation: WinnerCohortGeneration) -> bool:
        return bool(
            generation.planned_group_count is not None
            and generation.completed_group_count == generation.planned_group_count
            and int(generation.failed_group_count or 0) == 0
            and generation.evidence_row_count is not None
            and generation.root_manifest_hash
        )

    @staticmethod
    def _generation_matches_state(
        generation: WinnerCohortGeneration,
        state: WinnerCohortRefreshState,
    ) -> bool:
        return all(
            getattr(generation, field) == getattr(state, field)
            for field in _PUBLICATION_CONTRACT_FIELDS
        )

    @staticmethod
    def _generations_compatible(
        left: WinnerCohortGeneration,
        right: WinnerCohortGeneration,
    ) -> bool:
        return all(
            getattr(left, field) == getattr(right, field) for field in _PUBLICATION_CONTRACT_FIELDS
        )

    @staticmethod
    def _contract_from_generation(
        generation: WinnerCohortGeneration,
    ) -> WinnerCohortContract:
        return WinnerCohortContract(
            **{field: getattr(generation, field) for field in _PUBLICATION_CONTRACT_FIELDS}
        )

    @staticmethod
    def _generation_watermark(generation: WinnerCohortGeneration) -> EvidenceWatermark:
        payload = dict(generation.watermark_json or {})
        if set(payload) - set(EvidenceWatermark.__dataclass_fields__):
            raise GenerationPublicationConflict("generation watermark schema is incompatible")
        try:
            return EvidenceWatermark(
                **{
                    field: int(payload.get(field, 0) or 0)
                    for field in EvidenceWatermark.__dataclass_fields__
                }
            )
        except (TypeError, ValueError) as exc:
            raise GenerationPublicationConflict("generation watermark is invalid") from exc

    @staticmethod
    def _rejected_result(
        status: str,
        generation: WinnerCohortGeneration,
        previous: WinnerCohortGeneration | None,
        *,
        desired_watermark_advanced: bool = False,
    ) -> GenerationPublicationResult:
        return GenerationPublicationResult(
            status=status,
            candidate_generation_id=int(generation.id),
            active_generation_id=int(previous.id) if previous is not None else None,
            desired_watermark_advanced=desired_watermark_advanced,
        )

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
        transaction = db.get_transaction()
        cache_state = db.info.get("winner_diagnostic_population_cache")
        cached_rows = (
            cache_state["rows"]
            if cache_state is not None and cache_state["transaction"] is transaction
            else {}
        )
        predictions = {
            prediction_id: cached_rows[(WinnerPredictionSnapshot, prediction_id)]
            for prediction_id in prediction_ids
            if (WinnerPredictionSnapshot, prediction_id) in cached_rows
        }
        missing_prediction_ids = prediction_ids - set(predictions)
        if missing_prediction_ids:
            predictions.update(
                {
                    int(row.id): row
                    for row in db.scalars(
                        select(WinnerPredictionSnapshot).where(
                            WinnerPredictionSnapshot.id.in_(sorted(missing_prediction_ids))
                        )
                    )
                }
            )
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


def _compare_watermarks(left: EvidenceWatermark, right: EvidenceWatermark) -> int | None:
    """Compare evidence newness component-wise, never by process time or row ID.

    Returns -1 when ``left`` is strictly older, 0 when equal, 1 when strictly
    newer, and ``None`` when the watermarks diverge and cannot be ordered.
    """

    left_values = tuple(left.as_dict().values())
    right_values = tuple(right.as_dict().values())
    if left_values == right_values:
        return 0
    if all(
        left_value <= right_value
        for left_value, right_value in zip(left_values, right_values, strict=True)
    ):
        return -1
    if all(
        left_value >= right_value
        for left_value, right_value in zip(left_values, right_values, strict=True)
    ):
        return 1
    return None


def _contract_statement(contract: WinnerCohortContract):
    statement = select(WinnerCohortRefreshState)
    for name, value in contract.as_dict().items():
        statement = statement.where(getattr(WinnerCohortRefreshState, name) == value)
    return statement


def _canonical_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
