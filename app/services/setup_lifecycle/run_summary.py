from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import (
    SetupLifecycleEpisode,
    SetupLifecycleEvent,
    SetupSignalSnapshot,
    SignalChangeEvent,
)

MEANINGFUL_LIFECYCLE_EVENT_TYPES = (
    "EPISODE_OPENED",
    "STATE_TRANSITION",
    "PHASE_TRANSITION",
)


def setup_lifecycle_run_summary(db: Session, run_id: int) -> dict[str, int]:
    """Return run-scoped counts used by parent and historical change views."""

    lifecycle_base = (
        select(func.count(SetupLifecycleEvent.id))
        .join(
            SetupSignalSnapshot,
            SetupLifecycleEvent.snapshot_id == SetupSignalSnapshot.id,
        )
        .where(SetupSignalSnapshot.run_id == run_id)
    )
    canonical_revision_count = int(
        db.scalar(lifecycle_base.where(SetupLifecycleEvent.event_type == "CANONICAL_REVISION"))
        or 0
    )
    lifecycle_change_count = int(
        db.scalar(
            lifecycle_base.where(
                SetupLifecycleEvent.event_type.in_(MEANINGFUL_LIFECYCLE_EVENT_TYPES)
            )
        )
        or 0
    )
    signal_change_count = int(
        db.scalar(
            select(func.count(SignalChangeEvent.id))
            .join(
                SetupSignalSnapshot,
                SignalChangeEvent.current_snapshot_id == SetupSignalSnapshot.id,
            )
            .where(SetupSignalSnapshot.run_id == run_id)
        )
        or 0
    )
    active_episode_count = int(
        db.scalar(
            select(func.count(SetupLifecycleEpisode.id))
            .join(
                SetupSignalSnapshot,
                SetupLifecycleEpisode.current_snapshot_id == SetupSignalSnapshot.id,
            )
            .where(
                SetupLifecycleEpisode.status == "ACTIVE",
                SetupSignalSnapshot.run_id == run_id,
            )
        )
        or 0
    )
    return {
        "active_episode_count": active_episode_count,
        "canonical_revision_count": canonical_revision_count,
        "lifecycle_change_count": lifecycle_change_count,
        "signal_change_count": signal_change_count,
        "meaningful_change_count": lifecycle_change_count + signal_change_count,
    }
