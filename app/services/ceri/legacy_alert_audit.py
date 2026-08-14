from __future__ import annotations

from enum import StrEnum

from app.models.ceri_tables import CeriAlertEvent, CeriChangeEvent
from app.services.ceri.change_semantics import ComparisonState


class AlertValidity(StrEnum):
    VALID_CURRENT = "VALID_CURRENT"
    VALID_HISTORICAL = "VALID_HISTORICAL"
    INVALID_LEGACY = "INVALID_LEGACY"
    ORPHANED = "ORPHANED"
    DUPLICATE = "DUPLICATE"


def classify_legacy_alert(
    alert: CeriAlertEvent,
    *,
    change: CeriChangeEvent | None,
    duplicate: bool = False,
    latest_snapshot_ids: set[int] | None = None,
    current_catalyst_revision_ids: set[int] | None = None,
) -> AlertValidity:
    """Classify persisted alert history without deleting or rewriting evidence."""
    if change is None or alert.source_change_event_id is None:
        return AlertValidity.ORPHANED
    if duplicate:
        return AlertValidity.DUPLICATE
    if (
        change.comparison_state
        and change.comparison_state != ComparisonState.COMPARABLE.value
    ):
        return AlertValidity.INVALID_LEGACY
    if change.change_type.startswith("OPPORTUNITY_") and (
        change.from_snapshot_id is None or change.to_snapshot_id is None
    ):
        return AlertValidity.INVALID_LEGACY
    if (
        change.to_snapshot_id is not None
        and change.to_snapshot_id in (latest_snapshot_ids or set())
    ):
        return AlertValidity.VALID_CURRENT
    if (
        change.catalyst_revision_id is not None
        and change.catalyst_revision_id in (current_catalyst_revision_ids or set())
    ):
        return AlertValidity.VALID_CURRENT
    return AlertValidity.VALID_HISTORICAL


def invalidation_reason(validity: AlertValidity) -> str | None:
    return {
        AlertValidity.INVALID_LEGACY: "Underlying change is invalid under corrected semantics.",
        AlertValidity.ORPHANED: "Underlying change-event lineage is missing.",
        AlertValidity.DUPLICATE: "A deterministic alert identity already exists.",
    }.get(validity)
