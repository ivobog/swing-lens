"""Pipeline/CERI adoption of the shared T15A semantic authority identities."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import BackgroundJob, WorkScopeMember
from app.services.work_scope_identity import (
    AcquisitionPlanRevisionReason,
    AcquisitionPlanSnapshot,
    AcquisitionRequirement,
    ContinuationDecision,
    RefreshCycleIdentity,
    ScopeContinuationState,
    ScopeIdentityStore,
    ScopeMember,
    ScopeMembershipPolicy,
    WorkScopeSnapshot,
)


class SemanticAuthorityError(ValueError):
    """Raised when durable work is missing or conflicts with semantic authority."""


class LegacySemanticAuthorityError(SemanticAuthorityError):
    """Legacy work cannot be certified by reconstructing current state."""


@dataclass(frozen=True)
class SemanticWorkAuthority:
    scope_id: str
    refresh_cycle_id: str
    acquisition_plan_id: str

    def as_dict(self) -> dict[str, str]:
        return {
            "scope_id": self.scope_id,
            "refresh_cycle_id": self.refresh_cycle_id,
            "acquisition_plan_id": self.acquisition_plan_id,
        }


def admit_frozen_operation(
    db: Session,
    *,
    operation_kind: str,
    subject_kind: str,
    members: Iterable[ScopeMember],
    cycle_key: str,
    business_cutoff: date | datetime,
    provider_source_class: str,
    request_type: str,
    requirements: Iterable[AcquisitionRequirement],
    policy_identity: str,
    scope_definition: dict[str, Any],
    parent_scope_id: str | None = None,
    configuration_identity: str | None = None,
    previous_plan_id: str | None = None,
    revision_reason: AcquisitionPlanRevisionReason | None = None,
    prior_refresh_id: str | None = None,
    refresh_reason: str = "SCHEDULED_OR_EXPLICIT_ADMISSION",
) -> SemanticWorkAuthority:
    """Freeze plan, membership, and refresh before business work is enqueued."""

    frozen_members = tuple(members)
    plan = AcquisitionPlanSnapshot(
        plan_kind=f"{operation_kind}-acquisition",
        plan_version="t15b-v1",
        revision_key=cycle_key,
        subjects=frozen_members,
        provider_source_class=provider_source_class,
        request_type=request_type,
        business_cutoff=business_cutoff,
        window_start=_optional_date(scope_definition.get("window_start")),
        window_end=_optional_date(scope_definition.get("window_end")),
        configuration_identity=configuration_identity,
        policy_identity=policy_identity,
        requirements=tuple(requirements),
        previous_plan_id=previous_plan_id,
        revision_reason=revision_reason,
    )
    plan_id = ScopeIdentityStore.persist_acquisition_plan(db, plan)
    scope = WorkScopeSnapshot(
        scope_kind=operation_kind,
        subject_kind=subject_kind,
        definition_version="t15b-v1",
        scope_definition=scope_definition,
        selection_policy_identity=policy_identity,
        membership_policy=ScopeMembershipPolicy.FROZEN,
        members=frozen_members,
        selected_at=_as_datetime(business_cutoff),
        business_cutoff=business_cutoff,
        configuration_identity=configuration_identity,
        parent_scope_id=parent_scope_id,
        acquisition_plan_id=plan_id,
    )
    scope_id = ScopeIdentityStore.persist_scope(db, scope)
    refresh = RefreshCycleIdentity(
        refresh_kind=operation_kind,
        scope_id=scope_id,
        observation_cycle_key=cycle_key,
        business_observation_cutoff=business_cutoff,
        refresh_reason=refresh_reason,
        effective_configuration_identity=configuration_identity,
        provider_source_policy_identity=None,
        prior_refresh_id=prior_refresh_id,
    )
    refresh_id = ScopeIdentityStore.persist_refresh_cycle(db, refresh)
    return SemanticWorkAuthority(scope_id, refresh_id, plan_id)


def bind_semantic_authority(
    owner: Any,
    authority: SemanticWorkAuthority,
    *,
    required_for_parent_completion: bool | None = None,
) -> None:
    """Bind once; an existing binding can only be verified, never replaced."""

    for name, expected in authority.as_dict().items():
        current = getattr(owner, name, None)
        if current not in {None, expected}:
            raise SemanticAuthorityError(f"SEMANTIC_AUTHORITY_REBIND_FORBIDDEN:{name}")
        setattr(owner, name, expected)
    if required_for_parent_completion is not None and hasattr(
        owner, "required_for_parent_completion"
    ):
        current_required = bool(getattr(owner, "required_for_parent_completion", False))
        if current_required and not required_for_parent_completion:
            raise SemanticAuthorityError("REQUIRED_CHILD_CANNOT_BECOME_OPTIONAL")
        owner.required_for_parent_completion = required_for_parent_completion
    payload = getattr(owner, "payload_json", None)
    if isinstance(payload, dict):
        owner.payload_json = {**payload, **authority.as_dict()}


def require_semantic_authority(owner: Any) -> SemanticWorkAuthority:
    values = {
        name: getattr(owner, name, None)
        for name in ("scope_id", "refresh_cycle_id", "acquisition_plan_id")
    }
    if not all(values.values()):
        raise LegacySemanticAuthorityError("LEGACY_UNKNOWN_SEMANTIC_AUTHORITY")
    return SemanticWorkAuthority(**values)


def validate_same_authority(*owners: Any) -> SemanticWorkAuthority:
    authorities = [require_semantic_authority(owner) for owner in owners]
    first = authorities[0]
    if any(authority != first for authority in authorities[1:]):
        raise SemanticAuthorityError("SEMANTIC_AUTHORITY_OWNER_MISMATCH")
    return first


def retained_scope_members(db: Session, scope_id: str) -> tuple[ScopeMember, ...]:
    return tuple(
        ScopeMember(row.subject_type, row.subject_id)
        for row in db.scalars(
            select(WorkScopeMember)
            .where(WorkScopeMember.scope_id == scope_id)
            .order_by(WorkScopeMember.ordinal)
        )
    )


def continuation_remainder(
    db: Session,
    *,
    authority: SemanticWorkAuthority,
    completed_members: Iterable[ScopeMember],
    processed_this_attempt: int,
) -> tuple[tuple[ScopeMember, ...], ContinuationDecision]:
    members = retained_scope_members(db, authority.scope_id)
    scope = _scope_for_continuation(authority, members)
    state = ScopeContinuationState(scope=scope, completed_members=tuple(completed_members))
    return state.remaining_members, state.decision(processed_members=processed_this_attempt)


def record_zero_progress(
    job: BackgroundJob,
    *,
    remaining_members: Iterable[ScopeMember],
    reason: str,
) -> None:
    remaining = tuple(remaining_members)
    if not remaining:
        raise SemanticAuthorityError("ZERO_PROGRESS_REQUIRES_REMAINING_WORK")
    metadata = dict(job.operational_metadata_json or {})
    metadata["semantic_zero_progress"] = {
        "reason": reason,
        "remaining": [member.as_dict() for member in remaining],
        "remaining_count": len(remaining),
        "scope_id": require_semantic_authority(job).scope_id,
    }
    job.operational_metadata_json = metadata
    job.status = "BLOCKED"


def required_child_counts(db: Session, scope_id: str) -> dict[str, int]:
    rows = list(
        db.scalars(
            select(BackgroundJob).where(
                BackgroundJob.scope_id == scope_id,
                BackgroundJob.required_for_parent_completion.is_(True),
            )
        )
    )
    terminal = {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED", "BLOCKED"}
    return {
        "planned": len(rows),
        "completed": sum(row.status in {"COMPLETED", "PARTIAL"} for row in rows),
        "failed": sum(row.status in {"FAILED", "BLOCKED"} for row in rows),
        "cancelled": sum(row.status == "CANCELLED" for row in rows),
        "remaining": sum(row.status not in terminal for row in rows),
    }


def require_children_terminal(db: Session, scope_id: str) -> dict[str, int]:
    counts = required_child_counts(db, scope_id)
    if counts["remaining"]:
        raise SemanticAuthorityError("REQUIRED_SEMANTIC_CHILDREN_INCOMPLETE")
    return counts


def ceri_cycle_key(
    *,
    provider: str,
    dataset: str,
    ticker: str,
    cutoff: date | datetime,
    explicit_cycle_key: str | None = None,
) -> str:
    if explicit_cycle_key:
        return explicit_cycle_key
    cutoff_date = cutoff.date() if isinstance(cutoff, datetime) else cutoff
    return f"ceri:{provider}:{dataset}:{ticker.upper()}:session:{cutoff_date.isoformat()}"


def _scope_for_continuation(
    authority: SemanticWorkAuthority, members: tuple[ScopeMember, ...]
) -> WorkScopeSnapshot:
    # Only membership and identity-bound fields participate in remainder computation.
    # The retained scope ID is independently verified by the caller's authority.
    return WorkScopeSnapshot(
        scope_kind="retained-continuation",
        subject_kind="retained-member",
        definition_version="t15b-v1",
        scope_definition={"retained_scope_id": authority.scope_id},
        selection_policy_identity="t15b-retained-scope",
        membership_policy=ScopeMembershipPolicy.FROZEN,
        members=members,
        selected_at=datetime(1970, 1, 1, tzinfo=UTC),
        acquisition_plan_id=authority.acquisition_plan_id,
    )


def _as_datetime(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("semantic cutoff datetime must be timezone-aware")
        return value
    return datetime(value.year, value.month, value.day, tzinfo=UTC)


def _optional_date(value: Any) -> date | datetime | None:
    if value is None or isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise TypeError("acquisition window values must be dates, datetimes, ISO strings, or null")
