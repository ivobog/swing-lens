from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.services.background_queue import (
    BACKGROUND,
    BROKER,
    INTERACTIVE,
    WorkerClaimState,
    build_worker_claim_groups,
    job_queue_class,
    normalize_worker_queues,
)


def test_worker_queue_names_are_normalized_in_stable_order() -> None:
    assert normalize_worker_queues("background,interactive") == (
        INTERACTIVE,
        BACKGROUND,
    )


def test_worker_queue_names_reject_unknown_or_empty_values() -> None:
    with pytest.raises(ValueError, match="unknown worker queues"):
        normalize_worker_queues("interactive,cpu")
    with pytest.raises(ValueError, match="at least one"):
        normalize_worker_queues(())


@pytest.mark.parametrize(
    ("job_type", "expected"),
    [
        ("FULL_PIPELINE", INTERACTIVE),
        ("IB_HISTOGRAM_FETCH", BROKER),
        ("MARKET_DATA_PREWARM", BROKER),
        ("CERI_FEATURE_BATCH", BACKGROUND),
        ("WINNER_COHORT_REFRESH", BACKGROUND),
    ],
)
def test_job_queue_class_uses_simple_three_class_model(
    job_type: str,
    expected: str,
) -> None:
    assert job_queue_class(job_type) == expected


def test_fair_claim_groups_prefer_interactive_then_aged_noninteractive() -> None:
    now = datetime(2026, 8, 12, 12, tzinfo=UTC)

    groups = build_worker_claim_groups(
        (INTERACTIVE, BROKER, BACKGROUND),
        fairness_enabled=True,
        claim_state=WorkerClaimState(),
        max_consecutive_interactive=4,
        age_promotion_seconds=300,
        now=now,
    )

    assert [group.queues for group in groups] == [
        (INTERACTIVE,),
        (BROKER, BACKGROUND),
        (BROKER,),
        (BACKGROUND,),
    ]
    assert groups[1].created_before == now - timedelta(seconds=300)


def test_consecutive_interactive_limit_forces_noninteractive_claim_opportunity() -> None:
    state = WorkerClaimState(consecutive_interactive_claims=4)

    groups = build_worker_claim_groups(
        (INTERACTIVE, BROKER, BACKGROUND),
        fairness_enabled=True,
        claim_state=state,
        max_consecutive_interactive=4,
        age_promotion_seconds=300,
    )

    assert groups[-1].queues == (INTERACTIVE,)
    assert groups[0].queues == (BROKER, BACKGROUND)
    state.record("CERI_FEATURE_BATCH")
    assert state.consecutive_interactive_claims == 0
