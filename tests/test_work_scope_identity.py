from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from app.services.calculation_identity import IdentityState
from app.services.work_scope_identity import (
    AcquisitionPlanRevisionReason,
    AcquisitionPlanSnapshot,
    AcquisitionRequirement,
    ChildWorkStatus,
    ContinuationDecision,
    DynamicMembershipPolicy,
    ParentChildAccounting,
    ParentCompletionStatus,
    RefreshCycleIdentity,
    ScopeContinuationState,
    ScopeMember,
    ScopeMembershipPolicy,
    WorkScopeIdentity,
    WorkScopeSnapshot,
    membership_fingerprint,
)

NOW = datetime(2026, 9, 20, 12, tzinfo=UTC)


def _scope(
    *members: str,
    policy: ScopeMembershipPolicy = ScopeMembershipPolicy.FROZEN,
) -> WorkScopeSnapshot:
    return WorkScopeSnapshot(
        scope_kind="winner-h5-maturation",
        subject_kind="winner-forward-outcome",
        definition_version="h5-next-open-v1",
        scope_definition={"horizon": 5, "entry_model": "NEXT_OPEN"},
        selection_policy_identity="winner-h5-due-v1",
        membership_policy=policy,
        members=tuple(ScopeMember("winner_forward_outcome", member) for member in members),
        selected_at=NOW,
        business_cutoff=date(2026, 9, 18),
        calculation_identity="a" * 64,
        configuration_identity="b" * 64,
        dynamic_policy=(
            DynamicMembershipPolicy(
                "winner-due-window", "v1", "reevaluate only in a separately recorded cycle"
            )
            if policy is ScopeMembershipPolicy.DECLARED_DYNAMIC
            else None
        ),
    )


def _plan(*subjects: str) -> AcquisitionPlanSnapshot:
    return AcquisitionPlanSnapshot(
        plan_kind="daily-price-bars",
        plan_version="v1",
        revision_key="initial",
        subjects=tuple(ScopeMember("ticker", subject) for subject in subjects),
        provider_source_class="IB-HISTORICAL",
        request_type="TRADES-1D",
        business_cutoff=date(2026, 9, 18),
        window_start=date(2025, 9, 18),
        window_end=date(2026, 9, 18),
        configuration_identity="c" * 64,
        policy_identity="ib-price-plan-v1",
        requirements=(
            AcquisitionRequirement("TRADES", required=True),
            AcquisitionRequirement("MIDPOINT", required=False),
        ),
    )


def test_membership_hash_is_order_independent_and_normalized() -> None:
    left = (ScopeMember("ticker", " msft "), ScopeMember("TICKER", "aapl"))
    right = tuple(reversed(left))

    assert membership_fingerprint(left) == membership_fingerprint(right)
    assert _scope("2", "1").identity == _scope("1", "2").identity


def test_added_or_removed_member_changes_scope_identity() -> None:
    base = _scope("1", "2")

    assert _scope("1", "2", "3").identity != base.identity
    assert _scope("1").identity != base.identity


def test_duplicate_normalized_member_is_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate normalized"):
        WorkScopeSnapshot(
            scope_kind="prices",
            subject_kind="ticker",
            definition_version="v1",
            scope_definition={},
            selection_policy_identity="v1",
            membership_policy=ScopeMembershipPolicy.FROZEN,
            members=(ScopeMember("ticker", "aapl"), ScopeMember("TICKER", " AAPL ")),
            selected_at=NOW,
        )


def test_operational_attempt_fields_cannot_enter_scope_identity() -> None:
    fields = WorkScopeSnapshot.__dataclass_fields__

    assert not {"execution_token", "worker_pid", "retry_count", "heartbeat"} & fields.keys()


def test_configuration_change_changes_scope_identity() -> None:
    base = _scope("1")

    assert replace(base, configuration_identity="d" * 64).identity != base.identity


def test_legacy_scope_is_explicit_and_never_certified() -> None:
    legacy = WorkScopeIdentity.legacy_unknown()

    assert legacy.state is IdentityState.LEGACY_UNKNOWN
    with pytest.raises(ValueError, match="LEGACY_UNKNOWN"):
        legacy.require_known()


def test_dynamic_scope_requires_a_versioned_policy() -> None:
    with pytest.raises(ValueError, match="frozen dynamic policy"):
        replace(
            _scope("1"),
            membership_policy=ScopeMembershipPolicy.DECLARED_DYNAMIC,
        )

    dynamic = _scope("1", policy=ScopeMembershipPolicy.DECLARED_DYNAMIC)
    changed_evaluation = replace(dynamic, members=(*dynamic.members, ScopeMember("x", "2")))
    assert dynamic.dynamic_policy == changed_evaluation.dynamic_policy
    assert dynamic.identity != changed_evaluation.identity


def test_retry_keeps_refresh_identity_and_new_refresh_is_distinct() -> None:
    scope = _scope("1")
    first = RefreshCycleIdentity(
        refresh_kind="CERI_PROVIDER_REFRESH",
        scope_id=scope.identity.require_known(),
        observation_cycle_key="2026-09-20-manual-1",
        business_observation_cutoff=NOW,
        refresh_reason="SCHEDULED",
        effective_configuration_identity="b" * 64,
    )

    assert first.retry().value == first.value
    second = first.next_refresh(
        observation_cycle_key="2026-09-20-manual-2",
        business_observation_cutoff=NOW,
        refresh_reason="OPERATOR_REFRESH",
    )
    assert second.value != first.value
    assert second.prior_refresh_id == first.value


def test_execution_attempt_metadata_cannot_change_refresh_identity() -> None:
    assert (
        not {
            "execution_token",
            "worker_attempt",
            "retry_count",
            "queue_timestamp",
        }
        & RefreshCycleIdentity.__dataclass_fields__.keys()
    )


def test_frozen_continuation_uses_snapshot_remainder_not_new_eligible_row() -> None:
    scope = _scope("A", "B", "C")
    state = ScopeContinuationState(scope, (ScopeMember("winner_forward_outcome", "A"),))
    newly_eligible = ScopeMember("winner_forward_outcome", "D")

    assert [member.subject_id for member in state.remaining_members] == ["B", "C"]
    assert newly_eligible not in state.remaining_members
    assert state.decision(processed_members=1) is ContinuationDecision.ENQUEUE_REMAINDER


def test_zero_progress_never_enqueues_equivalent_continuation() -> None:
    state = ScopeContinuationState(_scope("A"))

    assert state.decision(processed_members=0) is ContinuationDecision.STOP_ZERO_PROGRESS
    assert (
        ScopeContinuationState(_scope()).decision(processed_members=0)
        is ContinuationDecision.COMPLETE
    )


def test_dynamic_reevaluation_requires_explicit_reason() -> None:
    state = ScopeContinuationState(_scope(policy=ScopeMembershipPolicy.DECLARED_DYNAMIC))

    assert state.decision(processed_members=0) is ContinuationDecision.COMPLETE
    assert (
        state.decision(processed_members=0, dynamic_reevaluation_reason="next market session")
        is ContinuationDecision.SCHEDULE_DYNAMIC_REEVALUATION
    )


def test_acquisition_retry_preserves_plan_and_replan_creates_lineage() -> None:
    first = _plan("MSFT", "AAPL")
    retry = first

    assert retry.identity == first.identity
    second = first.revised(
        revision_key="provider-fallback-1",
        reason=AcquisitionPlanRevisionReason.PROVIDER_LIMITATION,
        provider_source_class="SECONDARY-HISTORICAL",
    )
    assert second.identity != first.identity
    assert second.previous_plan_id == first.identity.value
    assert second.revision_reason is AcquisitionPlanRevisionReason.PROVIDER_LIMITATION


def test_parent_completion_accounts_for_all_semantic_children() -> None:
    active = ParentChildAccounting(
        (
            ("A", ChildWorkStatus.COMPLETED),
            ("B", ChildWorkStatus.CREATED),
            ("C", ChildWorkStatus.PLANNED),
        )
    )
    assert active.completion_status is ParentCompletionStatus.IN_PROGRESS
    assert active.counts == {
        "planned": 3,
        "created": 2,
        "completed": 1,
        "failed": 0,
        "cancelled": 0,
        "remaining": 2,
    }
    assert (
        ParentChildAccounting(
            (("A", ChildWorkStatus.COMPLETED), ("B", ChildWorkStatus.COMPLETED))
        ).completion_status
        is ParentCompletionStatus.COMPLETE
    )


@pytest.mark.parametrize("size", (1, 50, 200))
def test_membership_hash_scales_without_order_dependency(size: int) -> None:
    members = tuple(ScopeMember("ticker", f"T{index:04d}") for index in range(size))

    assert membership_fingerprint(members) == membership_fingerprint(tuple(reversed(members)))
