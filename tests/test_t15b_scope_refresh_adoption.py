from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from app.models.tables import BackgroundJob
from app.services.background_job_service import JobStatus
from app.services.ib_fetch_plan_service import (
    FetchAction,
    FetchPlan,
    FetchPlanItem,
    fetch_plan_from_dict,
    fetch_plan_to_dict,
)
from app.services.scope_refresh_adoption import (
    LegacySemanticAuthorityError,
    SemanticAuthorityError,
    SemanticWorkAuthority,
    bind_semantic_authority,
    ceri_cycle_key,
    continuation_remainder,
    record_zero_progress,
    require_children_terminal,
    require_semantic_authority,
    required_child_counts,
)
from app.services.work_scope_identity import ContinuationDecision, ScopeMember

SCOPE_ID = "1" * 64
REFRESH_ID = "2" * 64
PLAN_ID = "3" * 64


def _authority() -> SemanticWorkAuthority:
    return SemanticWorkAuthority(SCOPE_ID, REFRESH_ID, PLAN_ID)


def test_binding_is_single_assignment_and_payload_carries_references() -> None:
    owner = SimpleNamespace(
        scope_id=None,
        refresh_cycle_id=None,
        acquisition_plan_id=None,
        payload_json={"business": "unchanged"},
    )
    bind_semantic_authority(owner, _authority())
    bind_semantic_authority(owner, _authority())

    assert require_semantic_authority(owner) == _authority()
    assert owner.payload_json == {"business": "unchanged", **_authority().as_dict()}

    with pytest.raises(SemanticAuthorityError, match="REBIND_FORBIDDEN"):
        bind_semantic_authority(owner, SemanticWorkAuthority("4" * 64, REFRESH_ID, PLAN_ID))


def test_legacy_owner_fails_closed_without_scope_reconstruction() -> None:
    with pytest.raises(LegacySemanticAuthorityError, match="LEGACY_UNKNOWN"):
        require_semantic_authority(SimpleNamespace())


def test_ceri_retry_cycle_is_stable_but_later_refresh_is_distinct() -> None:
    r1 = ceri_cycle_key(
        provider="manual",
        dataset="estimates",
        ticker="msft",
        cutoff=date(2026, 9, 20),
    )
    retry = ceri_cycle_key(
        provider="manual",
        dataset="estimates",
        ticker="MSFT",
        cutoff=date(2026, 9, 20),
    )
    r2 = ceri_cycle_key(
        provider="manual",
        dataset="estimates",
        ticker="MSFT",
        cutoff=date(2026, 9, 21),
    )

    assert retry == r1
    assert r2 != r1


def test_continuation_uses_retained_remainder_and_stops_zero_progress() -> None:
    db = _RowsDb(
        [SimpleNamespace(subject_type="TICKER", subject_id=value) for value in ("A", "B", "C")]
    )
    remaining, decision = continuation_remainder(
        db,
        authority=_authority(),
        completed_members=(ScopeMember("TICKER", "A"),),
        processed_this_attempt=1,
    )
    assert [member.subject_id for member in remaining] == ["B", "C"]
    assert decision is ContinuationDecision.ENQUEUE_REMAINDER

    remaining, decision = continuation_remainder(
        db,
        authority=_authority(),
        completed_members=(ScopeMember("TICKER", "A"),),
        processed_this_attempt=0,
    )
    assert [member.subject_id for member in remaining] == ["B", "C"]
    assert decision is ContinuationDecision.STOP_ZERO_PROGRESS


def test_zero_progress_blocks_same_semantic_job_and_retains_reason() -> None:
    job = BackgroundJob(
        job_type="CERI_BACKFILL",
        status=JobStatus.RUNNING,
        payload_json={},
        operational_metadata_json={},
        scope_id=SCOPE_ID,
        refresh_cycle_id=REFRESH_ID,
        acquisition_plan_id=PLAN_ID,
    )
    record_zero_progress(
        job,
        remaining_members=(ScopeMember("TICKER", "A"), ScopeMember("TICKER", "B")),
        reason="WAITING_FOR_EXTERNAL_REQUIREMENT",
    )

    assert job.status == JobStatus.BLOCKED
    assert job.operational_metadata_json["semantic_zero_progress"] == {
        "reason": "WAITING_FOR_EXTERNAL_REQUIREMENT",
        "remaining": [
            {"subject_type": "TICKER", "subject_id": "A"},
            {"subject_type": "TICKER", "subject_id": "B"},
        ],
        "remaining_count": 2,
        "scope_id": SCOPE_ID,
    }


def test_required_children_prevent_parent_completion_until_terminal() -> None:
    queued = BackgroundJob(
        job_type="CHILD",
        status=JobStatus.QUEUED,
        scope_id=SCOPE_ID,
        required_for_parent_completion=True,
    )
    completed = BackgroundJob(
        job_type="CHILD",
        status=JobStatus.COMPLETED,
        scope_id=SCOPE_ID,
        required_for_parent_completion=True,
    )
    db = _RowsDb([queued, completed])

    assert required_child_counts(db, SCOPE_ID)["remaining"] == 1
    with pytest.raises(SemanticAuthorityError, match="CHILDREN_INCOMPLETE"):
        require_children_terminal(db, SCOPE_ID)

    queued.status = JobStatus.COMPLETED
    assert require_children_terminal(db, SCOPE_ID)["completed"] == 2


def test_exact_fetch_plan_round_trip_does_not_reresolve_current_coverage() -> None:
    item = FetchPlanItem(
        ticker="MSFT",
        contract_status="RESOLVED",
        what_to_show="TRADES",
        action=FetchAction.TOP_UP_RECENT,
        duration="5 D",
        bar_size="1 day",
        current_bar_count=200,
        first_bar_date=date(2025, 1, 1),
        latest_bar_date=date(2026, 9, 18),
        required_bars=252,
        reason="missing recent sessions",
        estimated_request_count=1,
        request_start_date=date(2026, 9, 19),
        request_end_date=date(2026, 9, 20),
        dependency_roles=("SECURITY",),
    )
    plan = FetchPlan(
        run_id=7,
        requested_tickers=["MSFT"],
        symbols_including_benchmarks=["MSFT"],
        items=[item],
        estimated_request_count=1,
        estimated_full_backfills=0,
        estimated_top_ups=1,
        estimated_refreshes=0,
        estimated_skips=0,
        warnings=[],
        decision_counts={"TOP_UP_RECENT": 1},
    )

    assert fetch_plan_from_dict(fetch_plan_to_dict(plan)) == plan


class _RowsDb:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self, _statement):
        return iter(self.rows)
