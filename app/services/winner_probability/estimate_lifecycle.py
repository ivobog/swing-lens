"""Authoritative Winner estimate lifecycle and serving policy."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import and_, exists, or_, select

from app.models.tables import (
    EstimateKind,
    EstimateLifecycleStatus,
    WinnerCohortGeneration,
    WinnerProbabilityEstimate,
)


def estimate_is_serving(estimate=WinnerProbabilityEstimate):
    """Return the SQL predicate shared by every live estimate consumer."""

    published_generation = exists(
        select(WinnerCohortGeneration.id)
        .where(WinnerCohortGeneration.id == estimate.cohort_generation_id)
        .where(WinnerCohortGeneration.status == "PUBLISHED")
    )
    return and_(
        estimate.lifecycle_status == EstimateLifecycleStatus.PUBLISHED,
        estimate.estimate_kind.in_(
            (
                EstimateKind.DECISION_TIME,
                EstimateKind.LATEST_RESCORE,
                EstimateKind.AS_OF_REPLAY,
            )
        ),
        or_(estimate.cohort_generation_id.is_(None), published_generation),
    )


def published_lifecycle_fields(*, at: datetime | None = None) -> dict[str, object]:
    """Fields for a newly produced estimate that is immediately live."""

    return {
        "lifecycle_status": EstimateLifecycleStatus.PUBLISHED,
        "published_at": at or datetime.now(UTC),
        "superseded_at": None,
    }


def candidate_lifecycle_fields() -> dict[str, object]:
    """Fields for a stored estimate that must remain structurally non-serving."""

    return {
        "lifecycle_status": EstimateLifecycleStatus.CANDIDATE,
        "published_at": None,
        "superseded_at": None,
    }
