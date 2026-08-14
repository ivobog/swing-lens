from __future__ import annotations

from enum import StrEnum
from typing import Any

from app.services.ceri.enums import CeriChangeType

EVIDENCE_CONTRACT_VERSION = "ceri-evidence-contract-v2"


class ComparisonState(StrEnum):
    COMPARABLE = "COMPARABLE"
    MODEL_VERSION_TRANSITION = "MODEL_VERSION_TRANSITION"
    CONFIG_TRANSITION = "CONFIG_TRANSITION"
    EVIDENCE_CONTRACT_TRANSITION = "EVIDENCE_CONTRACT_TRANSITION"
    NO_PRIOR_COMPARABLE_SNAPSHOT = "NO_PRIOR_COMPARABLE_SNAPSHOT"


class ChangeGroup(StrEnum):
    UPWARD_REVISIONS = "Upward revisions"
    DOWNWARD_REVISIONS = "Downward revisions"
    GUIDANCE = "Guidance"
    CATALYSTS = "Catalysts"
    OPPORTUNITY = "Opportunity"
    RISK = "Risk"
    RESOLVED = "Resolved"
    OTHER_DATA_QUALITY = "Other/Data quality"


class Importance(StrEnum):
    INFO = "INFO"
    NOTABLE = "NOTABLE"
    IMPORTANT = "IMPORTANT"
    URGENT = "URGENT"


class SignalClass(StrEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    RISK = "RISK"
    NEUTRAL = "NEUTRAL"
    DATA_QUALITY = "DATA_QUALITY"


CHANGE_GROUP_BY_TYPE: dict[CeriChangeType, ChangeGroup] = {
    CeriChangeType.REVISION_UP: ChangeGroup.UPWARD_REVISIONS,
    CeriChangeType.REVISION_ACCELERATED: ChangeGroup.UPWARD_REVISIONS,
    CeriChangeType.REVISION_DOWN: ChangeGroup.DOWNWARD_REVISIONS,
    CeriChangeType.REVISION_DECELERATED: ChangeGroup.DOWNWARD_REVISIONS,
    CeriChangeType.GUIDANCE_RAISED: ChangeGroup.GUIDANCE,
    CeriChangeType.GUIDANCE_LOWERED: ChangeGroup.GUIDANCE,
    CeriChangeType.GUIDANCE_WITHDRAWN: ChangeGroup.GUIDANCE,
    CeriChangeType.NEW_CATALYST: ChangeGroup.CATALYSTS,
    CeriChangeType.CATALYST_UPDATED: ChangeGroup.CATALYSTS,
    CeriChangeType.NEW_BINARY_EVENT: ChangeGroup.CATALYSTS,
    CeriChangeType.CATALYST_CONFIRMED: ChangeGroup.CATALYSTS,
    CeriChangeType.CATALYST_DELAYED: ChangeGroup.CATALYSTS,
    CeriChangeType.CATALYST_CANCELLED: ChangeGroup.RESOLVED,
    CeriChangeType.CATALYST_RESOLVED: ChangeGroup.RESOLVED,
    CeriChangeType.EVENT_COMPLETED: ChangeGroup.RESOLVED,
    CeriChangeType.EVENT_CANCELLED: ChangeGroup.RESOLVED,
    CeriChangeType.EVENT_RESOLVED: ChangeGroup.RESOLVED,
    CeriChangeType.RISK_RESOLVED: ChangeGroup.RESOLVED,
    CeriChangeType.OPPORTUNITY_CHANGED: ChangeGroup.OPPORTUNITY,
    CeriChangeType.OPPORTUNITY_UPGRADED: ChangeGroup.OPPORTUNITY,
    CeriChangeType.OPPORTUNITY_DOWNGRADED: ChangeGroup.OPPORTUNITY,
    CeriChangeType.POSTURE_CHANGED: ChangeGroup.OPPORTUNITY,
    CeriChangeType.BECAME_RATED: ChangeGroup.OPPORTUNITY,
    CeriChangeType.BECAME_UNRATED: ChangeGroup.OPPORTUNITY,
    CeriChangeType.RISK_ESCALATED: ChangeGroup.RISK,
    CeriChangeType.RISK_DEESCALATED: ChangeGroup.RISK,
    CeriChangeType.DATA_STALE: ChangeGroup.OTHER_DATA_QUALITY,
    CeriChangeType.DATA_REFRESHED: ChangeGroup.OTHER_DATA_QUALITY,
    CeriChangeType.CONFLICT_OPENED: ChangeGroup.OTHER_DATA_QUALITY,
    CeriChangeType.CONFLICT_RESOLVED: ChangeGroup.OTHER_DATA_QUALITY,
    CeriChangeType.MODEL_VERSION_TRANSITION: ChangeGroup.OTHER_DATA_QUALITY,
    CeriChangeType.CONFIG_TRANSITION: ChangeGroup.OTHER_DATA_QUALITY,
    CeriChangeType.EVIDENCE_CONTRACT_TRANSITION: ChangeGroup.OTHER_DATA_QUALITY,
    CeriChangeType.BASELINE_ESTABLISHED: ChangeGroup.OTHER_DATA_QUALITY,
}


POSITIVE_TYPES = {
    CeriChangeType.REVISION_UP,
    CeriChangeType.REVISION_ACCELERATED,
    CeriChangeType.GUIDANCE_RAISED,
    CeriChangeType.CATALYST_CONFIRMED,
    CeriChangeType.OPPORTUNITY_UPGRADED,
    CeriChangeType.BECAME_RATED,
}
NEGATIVE_TYPES = {
    CeriChangeType.REVISION_DOWN,
    CeriChangeType.REVISION_DECELERATED,
    CeriChangeType.GUIDANCE_LOWERED,
    CeriChangeType.GUIDANCE_WITHDRAWN,
    CeriChangeType.OPPORTUNITY_DOWNGRADED,
    CeriChangeType.BECAME_UNRATED,
}
RISK_TYPES = {
    CeriChangeType.NEW_BINARY_EVENT,
    CeriChangeType.RISK_ESCALATED,
    CeriChangeType.CATALYST_DELAYED,
}
DATA_QUALITY_TYPES = {
    CeriChangeType.DATA_STALE,
    CeriChangeType.DATA_REFRESHED,
    CeriChangeType.CONFLICT_OPENED,
    CeriChangeType.CONFLICT_RESOLVED,
    CeriChangeType.MODEL_VERSION_TRANSITION,
    CeriChangeType.CONFIG_TRANSITION,
    CeriChangeType.EVIDENCE_CONTRACT_TRANSITION,
    CeriChangeType.BASELINE_ESTABLISHED,
}


def classify_snapshot_comparison(prior: Any | None, current: Any) -> ComparisonState:
    if prior is None:
        return ComparisonState.NO_PRIOR_COMPARABLE_SNAPSHOT
    if getattr(prior, "calculation_version", None) != getattr(current, "calculation_version", None):
        return ComparisonState.MODEL_VERSION_TRANSITION
    if getattr(prior, "config_hash", None) != getattr(current, "config_hash", None):
        return ComparisonState.CONFIG_TRANSITION
    prior_contract = getattr(prior, "evidence_contract_version", None)
    current_contract = getattr(current, "evidence_contract_version", None)
    if prior_contract is None and current_contract is None:
        return ComparisonState.COMPARABLE
    if prior_contract != current_contract or not prior_contract or not current_contract:
        return ComparisonState.EVIDENCE_CONTRACT_TRANSITION
    return ComparisonState.COMPARABLE


def select_prior_comparison(
    current: Any,
    candidates: list[Any],
) -> tuple[Any | None, ComparisonState, int]:
    ordered = sorted(
        (
            candidate
            for candidate in candidates
            if candidate is not current
            and getattr(candidate, "id", None) != getattr(current, "id", None)
        ),
        key=lambda candidate: (
            getattr(candidate, "as_of_session", None),
            getattr(candidate, "cutoff_at", None),
            getattr(candidate, "id", 0) or 0,
        ),
        reverse=True,
    )
    excluded = 0
    first_state = ComparisonState.NO_PRIOR_COMPARABLE_SNAPSHOT
    first_candidate = None
    for candidate in ordered:
        state = classify_snapshot_comparison(candidate, current)
        if first_candidate is None:
            first_candidate = candidate
            first_state = state
        if state is ComparisonState.COMPARABLE:
            return candidate, state, excluded
        excluded += 1
    return first_candidate, first_state, excluded


def change_group(change_type: str | CeriChangeType) -> ChangeGroup:
    return CHANGE_GROUP_BY_TYPE[CeriChangeType(change_type)]


def change_dimensions(
    change_type: str | CeriChangeType,
    delta: dict[str, Any] | None = None,
) -> tuple[Importance, SignalClass]:
    kind = CeriChangeType(change_type)
    delta = delta or {}
    if kind in DATA_QUALITY_TYPES:
        return Importance.INFO, SignalClass.DATA_QUALITY
    if kind in RISK_TYPES:
        return (
            Importance.URGENT if kind is CeriChangeType.NEW_BINARY_EVENT else Importance.IMPORTANT,
            SignalClass.RISK,
        )
    if kind in POSITIVE_TYPES:
        return Importance.NOTABLE, SignalClass.POSITIVE
    if kind in NEGATIVE_TYPES:
        return Importance.NOTABLE, SignalClass.NEGATIVE
    if kind in {
        CeriChangeType.EVENT_COMPLETED,
        CeriChangeType.EVENT_CANCELLED,
        CeriChangeType.EVENT_RESOLVED,
        CeriChangeType.RISK_RESOLVED,
        CeriChangeType.CATALYST_CANCELLED,
        CeriChangeType.CATALYST_RESOLVED,
    }:
        return Importance.INFO, SignalClass.NEUTRAL
    raw = delta.get("delta")
    if raw is not None:
        if float(raw) > 0:
            return Importance.NOTABLE, SignalClass.POSITIVE
        if float(raw) < 0:
            return Importance.NOTABLE, SignalClass.NEGATIVE
    return Importance.NOTABLE, SignalClass.NEUTRAL
