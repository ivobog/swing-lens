"""Typed identities for Phase-6 work scope, refresh, and acquisition planning.

The types in this module are intentionally independent of worker/job ownership.
An execution attempt may change while these semantic identities remain fixed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import StrEnum
from functools import cached_property
from typing import Any
from unicodedata import normalize as normalize_unicode

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.models.tables import (
    AcquisitionPlanRecord,
    RefreshCycleRecord,
    WorkScopeMember,
    WorkScopeRecord,
)
from app.services.calculation_identity import IdentityState
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical

WORK_SCOPE_SCHEMA_VERSION = "work-scope-v1"
MEMBERSHIP_SCHEMA_VERSION = "work-scope-membership-v1"
REFRESH_CYCLE_SCHEMA_VERSION = "refresh-cycle-v1"
ACQUISITION_PLAN_SCHEMA_VERSION = "acquisition-plan-v1"


class ScopeMembershipPolicy(StrEnum):
    FROZEN = "FROZEN"
    DECLARED_DYNAMIC = "DECLARED_DYNAMIC"


class ContinuationDecision(StrEnum):
    COMPLETE = "COMPLETE"
    ENQUEUE_REMAINDER = "ENQUEUE_REMAINDER"
    STOP_ZERO_PROGRESS = "STOP_ZERO_PROGRESS"
    SCHEDULE_DYNAMIC_REEVALUATION = "SCHEDULE_DYNAMIC_REEVALUATION"


class ParentCompletionStatus(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ChildWorkStatus(StrEnum):
    PLANNED = "PLANNED"
    CREATED = "CREATED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class AcquisitionPlanRevisionReason(StrEnum):
    PROVIDER_LIMITATION = "PROVIDER_LIMITATION"
    MISSING_SOURCE = "MISSING_SOURCE"
    NEWLY_DISCOVERED_SOURCE = "NEWLY_DISCOVERED_SOURCE"
    EXPLICIT_OPERATOR_REPLAN = "EXPLICIT_OPERATOR_REPLAN"
    POLICY_CHANGE = "POLICY_CHANGE"


def _identifier(value: str, field: str, *, maximum: int = 160) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    normalized = normalize_unicode("NFKC", value).strip()
    if not normalized or len(normalized) > maximum or any(ord(char) < 32 for char in normalized):
        raise ValueError(f"{field} is not a valid identifier")
    return normalized


def _digest(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


@dataclass(frozen=True, order=True)
class ScopeMember:
    subject_type: str
    subject_id: str

    def __post_init__(self) -> None:
        subject_type = _identifier(self.subject_type, "subject_type", maximum=64).upper()
        raw_id = self.subject_id
        if isinstance(raw_id, bool) or raw_id is None:
            raise TypeError("subject_id must be text or an integer")
        if isinstance(raw_id, int):
            subject_id = str(raw_id)
        elif isinstance(raw_id, str):
            subject_id = _identifier(raw_id, "subject_id", maximum=512)
        else:
            raise TypeError("subject_id must be text or an integer")
        if subject_type in {"TICKER", "SYMBOL"}:
            subject_id = subject_id.upper()
        object.__setattr__(self, "subject_type", subject_type)
        object.__setattr__(self, "subject_id", subject_id)

    @property
    def key(self) -> str:
        return f"{self.subject_type}:{self.subject_id}"

    def as_dict(self) -> dict[str, str]:
        return {"subject_type": self.subject_type, "subject_id": self.subject_id}


def canonical_members(
    members: tuple[ScopeMember, ...] | list[ScopeMember],
) -> tuple[ScopeMember, ...]:
    if not isinstance(members, tuple | list) or any(
        not isinstance(member, ScopeMember) for member in members
    ):
        raise TypeError("scope membership must contain typed ScopeMember values")
    ordered = tuple(sorted(members))
    keys = [member.key for member in ordered]
    if len(keys) != len(set(keys)):
        raise ValueError("scope membership contains a duplicate normalized subject")
    return ordered


def membership_fingerprint(members: tuple[ScopeMember, ...] | list[ScopeMember]) -> str:
    ordered = canonical_members(members)
    return Canonical.fingerprint(
        {
            "schema_version": MEMBERSHIP_SCHEMA_VERSION,
            "members": [member.as_dict() for member in ordered],
        }
    )


@dataclass(frozen=True)
class DynamicMembershipPolicy:
    policy_id: str
    policy_version: str
    evaluation_rule: str

    def __post_init__(self) -> None:
        for field in ("policy_id", "policy_version", "evaluation_rule"):
            object.__setattr__(self, field, _identifier(getattr(self, field), field, maximum=512))

    def as_dict(self) -> dict[str, str]:
        return {
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "evaluation_rule": self.evaluation_rule,
        }


@dataclass(frozen=True)
class WorkScopeIdentity:
    state: IdentityState
    value: str | None = None
    schema_version: str = WORK_SCOPE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.state is IdentityState.KNOWN:
            _digest(self.value, "work scope identity")
        elif self.value is not None:
            raise ValueError("unknown work scope identity cannot carry a value")

    @classmethod
    def known(cls, value: str) -> WorkScopeIdentity:
        return cls(IdentityState.KNOWN, value)

    @classmethod
    def legacy_unknown(cls) -> WorkScopeIdentity:
        return cls(IdentityState.LEGACY_UNKNOWN)

    def require_known(self) -> str:
        if self.state is not IdentityState.KNOWN or self.value is None:
            raise ValueError("LEGACY_UNKNOWN work scope is not certified scope evidence")
        return self.value


@dataclass(frozen=True)
class WorkScopeSnapshot:
    scope_kind: str
    subject_kind: str
    definition_version: str
    scope_definition: dict[str, Any]
    selection_policy_identity: str
    membership_policy: ScopeMembershipPolicy
    members: tuple[ScopeMember, ...]
    selected_at: datetime
    business_cutoff: date | datetime | None = None
    calculation_identity: str | None = None
    configuration_identity: str | None = None
    parent_scope_id: str | None = None
    acquisition_plan_id: str | None = None
    dynamic_policy: DynamicMembershipPolicy | None = None
    schema_version: str = WORK_SCOPE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field in (
            "scope_kind",
            "subject_kind",
            "definition_version",
            "selection_policy_identity",
        ):
            object.__setattr__(self, field, _identifier(getattr(self, field), field))
        if not isinstance(self.membership_policy, ScopeMembershipPolicy):
            raise TypeError("membership_policy must be typed")
        if not isinstance(self.selected_at, datetime) or self.selected_at.tzinfo is None:
            raise ValueError("selected_at must be timezone-aware")
        if self.membership_policy is ScopeMembershipPolicy.DECLARED_DYNAMIC:
            if self.dynamic_policy is None:
                raise ValueError("declared dynamic scope requires a frozen dynamic policy")
        elif self.dynamic_policy is not None:
            raise ValueError("frozen scope cannot carry a dynamic membership policy")
        for field in (
            "calculation_identity",
            "configuration_identity",
            "parent_scope_id",
            "acquisition_plan_id",
        ):
            _digest(getattr(self, field), field)
        object.__setattr__(self, "members", canonical_members(self.members))
        object.__setattr__(self, "scope_definition", Canonical.canonicalize(self.scope_definition))

    @cached_property
    def membership_fingerprint(self) -> str:
        return membership_fingerprint(self.members)

    def semantic_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scope_kind": self.scope_kind,
            "subject_kind": self.subject_kind,
            "definition_version": self.definition_version,
            "scope_definition": self.scope_definition,
            "selection_policy_identity": self.selection_policy_identity,
            "membership_policy": self.membership_policy.value,
            "membership_fingerprint": self.membership_fingerprint,
            "members": [member.as_dict() for member in self.members],
            "selected_at": self.selected_at,
            "business_cutoff": self.business_cutoff,
            "calculation_identity": self.calculation_identity,
            "configuration_identity": self.configuration_identity,
            "parent_scope_id": self.parent_scope_id,
            "acquisition_plan_id": self.acquisition_plan_id,
            "dynamic_policy": self.dynamic_policy.as_dict() if self.dynamic_policy else None,
        }

    @cached_property
    def identity(self) -> WorkScopeIdentity:
        return WorkScopeIdentity.known(Canonical.fingerprint(self.semantic_payload()))

    def as_dict(self) -> dict[str, Any]:
        return Canonical.canonicalize(
            {**self.semantic_payload(), "scope_id": self.identity.require_known()}
        )


@dataclass(frozen=True)
class RefreshCycleIdentity:
    refresh_kind: str
    scope_id: str
    observation_cycle_key: str
    business_observation_cutoff: date | datetime
    refresh_reason: str
    effective_configuration_identity: str | None = None
    provider_source_policy_identity: str | None = None
    calculation_identity: str | None = None
    prior_refresh_id: str | None = None
    schema_version: str = REFRESH_CYCLE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field in ("refresh_kind", "observation_cycle_key", "refresh_reason"):
            object.__setattr__(self, field, _identifier(getattr(self, field), field, maximum=512))
        for field in (
            "scope_id",
            "effective_configuration_identity",
            "provider_source_policy_identity",
            "calculation_identity",
            "prior_refresh_id",
        ):
            _digest(getattr(self, field), field)
        if not isinstance(self.business_observation_cutoff, date):
            raise TypeError("business_observation_cutoff must be a date or datetime")
        if isinstance(self.business_observation_cutoff, datetime) and (
            self.business_observation_cutoff.tzinfo is None
        ):
            raise ValueError("business_observation_cutoff datetime must be timezone-aware")

    def semantic_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "refresh_kind": self.refresh_kind,
            "scope_id": self.scope_id,
            "observation_cycle_key": self.observation_cycle_key,
            "business_observation_cutoff": self.business_observation_cutoff,
            "refresh_reason": self.refresh_reason,
            "effective_configuration_identity": self.effective_configuration_identity,
            "provider_source_policy_identity": self.provider_source_policy_identity,
            "calculation_identity": self.calculation_identity,
            "prior_refresh_id": self.prior_refresh_id,
        }

    @cached_property
    def value(self) -> str:
        return Canonical.fingerprint(self.semantic_payload())

    def as_dict(self) -> dict[str, Any]:
        return Canonical.canonicalize({**self.semantic_payload(), "refresh_cycle_id": self.value})

    def retry(self) -> RefreshCycleIdentity:
        """A retry retains the exact semantic refresh identity."""
        return self

    def next_refresh(
        self,
        *,
        observation_cycle_key: str,
        business_observation_cutoff: date | datetime,
        refresh_reason: str,
        scope_id: str | None = None,
    ) -> RefreshCycleIdentity:
        candidate = replace(
            self,
            scope_id=scope_id or self.scope_id,
            observation_cycle_key=observation_cycle_key,
            business_observation_cutoff=business_observation_cutoff,
            refresh_reason=refresh_reason,
            prior_refresh_id=self.value,
        )
        if candidate.value == self.value:
            raise ValueError("new refresh must receive a distinct semantic identity")
        return candidate


@dataclass(frozen=True, order=True)
class AcquisitionRequirement:
    observation_type: str
    required: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "observation_type",
            _identifier(self.observation_type, "observation_type", maximum=160),
        )

    def as_dict(self) -> dict[str, Any]:
        return {"observation_type": self.observation_type, "required": self.required}


@dataclass(frozen=True)
class AcquisitionPlanIdentity:
    value: str
    schema_version: str = ACQUISITION_PLAN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _digest(self.value, "acquisition plan identity")


@dataclass(frozen=True)
class AcquisitionPlanSnapshot:
    plan_kind: str
    plan_version: str
    revision_key: str
    subjects: tuple[ScopeMember, ...]
    provider_source_class: str
    request_type: str
    business_cutoff: date | datetime
    window_start: date | datetime | None
    window_end: date | datetime | None
    configuration_identity: str | None
    policy_identity: str
    requirements: tuple[AcquisitionRequirement, ...]
    previous_plan_id: str | None = None
    revision_reason: AcquisitionPlanRevisionReason | None = None
    schema_version: str = ACQUISITION_PLAN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field in (
            "plan_kind",
            "plan_version",
            "revision_key",
            "provider_source_class",
            "request_type",
            "policy_identity",
        ):
            object.__setattr__(self, field, _identifier(getattr(self, field), field))
        object.__setattr__(self, "subjects", canonical_members(self.subjects))
        requirements = tuple(sorted(self.requirements))
        if len(requirements) != len({item.observation_type for item in requirements}):
            raise ValueError("acquisition requirements cannot repeat observation types")
        object.__setattr__(self, "requirements", requirements)
        _digest(self.configuration_identity, "configuration_identity")
        _digest(self.previous_plan_id, "previous_plan_id")
        if (self.previous_plan_id is None) != (self.revision_reason is None):
            raise ValueError("plan revision requires both predecessor and revision reason")
        for name in ("business_cutoff", "window_start", "window_end"):
            value = getattr(self, name)
            if isinstance(value, datetime) and value.tzinfo is None:
                raise ValueError(f"{name} datetime must be timezone-aware")
        if self.window_start is not None and self.window_end is not None:
            if Canonical.dumps(self.window_start) > Canonical.dumps(self.window_end):
                raise ValueError("acquisition temporal window is reversed")

    def semantic_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_kind": self.plan_kind,
            "plan_version": self.plan_version,
            "revision_key": self.revision_key,
            "subjects": [subject.as_dict() for subject in self.subjects],
            "provider_source_class": self.provider_source_class,
            "request_type": self.request_type,
            "business_cutoff": self.business_cutoff,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "configuration_identity": self.configuration_identity,
            "policy_identity": self.policy_identity,
            "requirements": [requirement.as_dict() for requirement in self.requirements],
            "previous_plan_id": self.previous_plan_id,
            "revision_reason": self.revision_reason.value if self.revision_reason else None,
        }

    @cached_property
    def identity(self) -> AcquisitionPlanIdentity:
        return AcquisitionPlanIdentity(Canonical.fingerprint(self.semantic_payload()))

    def as_dict(self) -> dict[str, Any]:
        return Canonical.canonicalize({**self.semantic_payload(), "plan_id": self.identity.value})

    def revised(
        self,
        *,
        revision_key: str,
        reason: AcquisitionPlanRevisionReason,
        **changes: Any,
    ) -> AcquisitionPlanSnapshot:
        if not isinstance(reason, AcquisitionPlanRevisionReason):
            raise TypeError("plan revision reason must be typed")
        revised = replace(
            self,
            revision_key=revision_key,
            previous_plan_id=self.identity.value,
            revision_reason=reason,
            **changes,
        )
        if revised.identity == self.identity:
            raise ValueError("replanning must create a new acquisition plan identity")
        return revised


@dataclass(frozen=True)
class ScopeContinuationState:
    scope: WorkScopeSnapshot
    completed_members: tuple[ScopeMember, ...] = ()

    def __post_init__(self) -> None:
        completed = canonical_members(self.completed_members)
        scope_keys = {member.key for member in self.scope.members}
        if any(member.key not in scope_keys for member in completed):
            raise ValueError("completed membership must be a subset of the scope snapshot")
        object.__setattr__(self, "completed_members", completed)

    @cached_property
    def remaining_members(self) -> tuple[ScopeMember, ...]:
        completed = {member.key for member in self.completed_members}
        return tuple(member for member in self.scope.members if member.key not in completed)

    def decision(
        self,
        *,
        processed_members: int,
        dynamic_reevaluation_reason: str | None = None,
    ) -> ContinuationDecision:
        if processed_members < 0:
            raise ValueError("processed_members cannot be negative")
        if processed_members == 0 and self.remaining_members:
            return ContinuationDecision.STOP_ZERO_PROGRESS
        if self.remaining_members:
            return ContinuationDecision.ENQUEUE_REMAINDER
        if self.scope.membership_policy is ScopeMembershipPolicy.DECLARED_DYNAMIC:
            if dynamic_reevaluation_reason:
                _identifier(dynamic_reevaluation_reason, "dynamic_reevaluation_reason", maximum=512)
                return ContinuationDecision.SCHEDULE_DYNAMIC_REEVALUATION
        return ContinuationDecision.COMPLETE


@dataclass(frozen=True)
class ParentChildAccounting:
    children: tuple[tuple[str, ChildWorkStatus], ...]

    def __post_init__(self) -> None:
        normalized = tuple(
            sorted((_identifier(key, "child target"), status) for key, status in self.children)
        )
        if any(not isinstance(status, ChildWorkStatus) for _, status in normalized):
            raise TypeError("child status must be typed")
        if len(normalized) != len({key for key, _ in normalized}):
            raise ValueError("child targets must be unique")
        object.__setattr__(self, "children", normalized)

    @property
    def counts(self) -> dict[str, int]:
        return {
            "planned": len(self.children),
            "created": sum(status is not ChildWorkStatus.PLANNED for _, status in self.children),
            "completed": sum(status is ChildWorkStatus.COMPLETED for _, status in self.children),
            "failed": sum(status is ChildWorkStatus.FAILED for _, status in self.children),
            "cancelled": sum(status is ChildWorkStatus.CANCELLED for _, status in self.children),
            "remaining": sum(
                status in {ChildWorkStatus.PLANNED, ChildWorkStatus.CREATED}
                for _, status in self.children
            ),
        }

    @property
    def completion_status(self) -> ParentCompletionStatus:
        statuses = {status for _, status in self.children}
        if ChildWorkStatus.FAILED in statuses:
            return ParentCompletionStatus.FAILED
        if ChildWorkStatus.CANCELLED in statuses:
            return ParentCompletionStatus.CANCELLED
        if statuses <= {ChildWorkStatus.COMPLETED}:
            return ParentCompletionStatus.COMPLETE
        return ParentCompletionStatus.IN_PROGRESS


class ScopeIdentityStore:
    """Constant-query persistence for immutable semantic identity records."""

    @staticmethod
    def persist_acquisition_plan(db, plan: AcquisitionPlanSnapshot) -> str:
        payload = plan.as_dict()
        plan_id = plan.identity.value
        db.execute(
            insert(AcquisitionPlanRecord)
            .values(
                plan_id=plan_id,
                plan_kind=plan.plan_kind,
                plan_version=plan.plan_version,
                previous_plan_id=plan.previous_plan_id,
                revision_reason=(plan.revision_reason.value if plan.revision_reason else None),
                payload_json=payload,
            )
            .on_conflict_do_nothing()
        )
        retained = db.get(AcquisitionPlanRecord, plan_id)
        if retained is None or retained.payload_json != payload:
            raise ValueError("ACQUISITION_PLAN_IDENTITY_COLLISION")
        return plan_id

    @staticmethod
    def persist_scope(db, scope: WorkScopeSnapshot) -> str:
        scope_id = scope.identity.require_known()
        payload = scope.as_dict()
        db.execute(
            insert(WorkScopeRecord)
            .values(
                scope_id=scope_id,
                scope_kind=scope.scope_kind,
                subject_kind=scope.subject_kind,
                membership_policy=scope.membership_policy.value,
                membership_fingerprint=scope.membership_fingerprint,
                parent_scope_id=scope.parent_scope_id,
                acquisition_plan_id=scope.acquisition_plan_id,
                payload_json=payload,
            )
            .on_conflict_do_nothing()
        )
        if scope.members:
            db.execute(
                insert(WorkScopeMember)
                .values(
                    [
                        {
                            "scope_id": scope_id,
                            "subject_type": member.subject_type,
                            "subject_id": member.subject_id,
                            "ordinal": ordinal,
                            "membership_fingerprint": scope.membership_fingerprint,
                        }
                        for ordinal, member in enumerate(scope.members)
                    ]
                )
                .on_conflict_do_nothing()
            )
        retained = db.get(WorkScopeRecord, scope_id)
        members = tuple(
            ScopeMember(row.subject_type, row.subject_id)
            for row in db.scalars(
                select(WorkScopeMember)
                .where(WorkScopeMember.scope_id == scope_id)
                .order_by(WorkScopeMember.ordinal)
            )
        )
        if (
            retained is None
            or retained.payload_json != payload
            or members != scope.members
            or membership_fingerprint(members) != scope.membership_fingerprint
        ):
            raise ValueError("WORK_SCOPE_IDENTITY_COLLISION_OR_MEMBERSHIP_MISMATCH")
        return scope_id

    @staticmethod
    def persist_refresh_cycle(db, refresh: RefreshCycleIdentity) -> str:
        payload = refresh.as_dict()
        db.execute(
            insert(RefreshCycleRecord)
            .values(
                refresh_cycle_id=refresh.value,
                refresh_kind=refresh.refresh_kind,
                scope_id=refresh.scope_id,
                prior_refresh_id=refresh.prior_refresh_id,
                observation_cycle_key=refresh.observation_cycle_key,
                payload_json=payload,
            )
            .on_conflict_do_nothing()
        )
        retained = db.get(RefreshCycleRecord, refresh.value)
        if retained is None or retained.payload_json != payload:
            raise ValueError("REFRESH_CYCLE_IDENTITY_COLLISION")
        return refresh.value
