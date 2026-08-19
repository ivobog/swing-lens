from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.tables import BackgroundJob, WinnerOutcomeDefinition
from app.services.background_job_service import enqueue_job
from app.services.winner_probability.cohort_generation_service import (
    EvidenceWatermarkService,
    WatermarkAdvanceResult,
)
from app.services.winner_probability.config import WinnerProbabilityConfig


@dataclass(frozen=True)
class CohortRefreshRequestResult:
    watermark: WatermarkAdvanceResult
    job: BackgroundJob | None

    @property
    def requested(self) -> bool:
        return self.job is not None


class CohortRefreshPlanner:
    def __init__(
        self, *, watermark_service: EvidenceWatermarkService | None = None
    ) -> None:
        self.watermark_service = watermark_service or EvidenceWatermarkService()

    def request_for_current_evidence(
        self,
        db: Session,
        *,
        outcome_definition: WinnerOutcomeDefinition,
        config: WinnerProbabilityConfig,
        observed_at: datetime | None = None,
        priority: int = 100,
        enqueue_refresh: bool = True,
    ) -> CohortRefreshRequestResult:
        advance = self.watermark_service.advance_to_current_material_evidence(
            db,
            outcome_definition=outcome_definition,
            config=config,
            observed_at=observed_at,
        )
        state = advance.state
        caught_up = (
            state.published_generation_id is not None
            and state.published_watermark_hash == state.desired_watermark_hash
        )
        if not enqueue_refresh or (caught_up and not advance.advanced):
            return CohortRefreshRequestResult(watermark=advance, job=None)
        definition_key = outcome_definition.definition_id
        job = enqueue_job(
            db,
            "WINNER_COHORT_REFRESH",
            {
                "outcome_definition_id": definition_key,
                "refresh_state_id": state.id,
                "desired_watermark_hash": state.desired_watermark_hash,
            },
            request_key=f"winner:cohort-refresh:{definition_key}",
            priority=priority,
        )
        return CohortRefreshRequestResult(watermark=advance, job=job)
