from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.tables import BackgroundJob, WinnerOutcomeDefinition
from app.services.background_job_service import enqueue_job
from app.services.scope_refresh_adoption import bind_semantic_authority
from app.services.winner_probability.cohort_generation_service import (
    CohortGenerationService,
    EvidenceWatermarkService,
    WatermarkAdvanceResult,
    contract_for,
)
from app.services.winner_probability.config import WinnerProbabilityConfig
from app.services.winner_probability.scope_refresh import admit_cohort_refresh


@dataclass(frozen=True)
class CohortRefreshRequestResult:
    watermark: WatermarkAdvanceResult
    job: BackgroundJob | None

    @property
    def requested(self) -> bool:
        return self.job is not None


class CohortRefreshPlanner:
    def __init__(
        self,
        *,
        watermark_service: EvidenceWatermarkService | None = None,
        generation_service: CohortGenerationService | None = None,
    ) -> None:
        self.watermark_service = watermark_service or EvidenceWatermarkService()
        self.generation_service = generation_service or CohortGenerationService()

    def request_for_current_evidence(
        self,
        db: Session,
        *,
        outcome_definition: WinnerOutcomeDefinition,
        config: WinnerProbabilityConfig,
        observed_at: datetime | None = None,
        priority: int = 100,
        enqueue_refresh: bool = True,
        maturation_counts: dict | None = None,
    ) -> CohortRefreshRequestResult:
        observed_at = observed_at or datetime.now(UTC)
        advance = self.watermark_service.advance_to_current_material_evidence(
            db,
            outcome_definition=outcome_definition,
            config=config,
            observed_at=observed_at,
            maturation_counts=maturation_counts,
        )
        state = advance.state
        caught_up = (
            state.published_generation_id is not None
            and state.published_watermark_hash == state.desired_watermark_hash
        )
        if not enqueue_refresh or (caught_up and not advance.advanced):
            return CohortRefreshRequestResult(watermark=advance, job=None)
        definition_key = outcome_definition.definition_id
        authority = admit_cohort_refresh(
            db,
            state=state,
            outcome_definition=outcome_definition,
            config=config,
            cycle_key=(f"winner:cohort-refresh:{definition_key}:{state.desired_watermark_hash}"),
        )
        generation = self.generation_service.capture_or_resume(
            db,
            state=state,
            contract=contract_for(outcome_definition, config),
            config=config,
            requested_at=max(observed_at, state.updated_at) + timedelta(microseconds=1),
            semantic_authority=authority,
        )
        job = enqueue_job(
            db,
            "WINNER_COHORT_REFRESH",
            {
                "outcome_definition_id": definition_key,
                "refresh_state_id": state.id,
                "desired_watermark_hash": state.desired_watermark_hash,
                "cohort_generation_id": generation.id,
                "operation_cutoff_at": observed_at.isoformat(),
            },
            request_key=(f"winner:cohort-refresh:{definition_key}:{authority.refresh_cycle_id}"),
            priority=priority,
        )
        bind_semantic_authority(job, authority)
        return CohortRefreshRequestResult(watermark=advance, job=job)
