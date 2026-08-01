from __future__ import annotations

from dataclasses import replace

from lifecycle_helpers import snapshot

from app.services.setup_lifecycle.actionability_policy import SetupLifecycleActionabilityPolicy
from app.services.setup_lifecycle.enums import Actionability, DataQualityLabel, LifecycleState
from app.services.setup_lifecycle.lifecycle_engine import evaluate_lifecycle


def test_ready_with_imminent_earnings_remains_ready_but_actionability_is_blocked() -> None:
    normalized = snapshot(
        setup_score=7.8,
        classification="Breakout Base",
        distance_to_pivot_pct=1.0,
        earnings_risk="IMMINENT",
    )
    lifecycle = evaluate_lifecycle(normalized)

    actionability = SetupLifecycleActionabilityPolicy().evaluate(lifecycle, normalized)

    assert lifecycle.proposed_state is LifecycleState.READY
    assert actionability.actionability is Actionability.BLOCKED
    assert actionability.blockers == ("IMMINENT_EARNINGS",)


def test_stale_source_ready_state_becomes_low_confidence_not_blocked() -> None:
    normalized = replace(
        snapshot(
            setup_score=7.8,
            classification="Breakout Base",
            distance_to_pivot_pct=1.0,
            data_quality=DataQualityLabel.LOW,
        ),
        warning_flags=("STALE_PRICE_BAR",),
    )
    lifecycle = evaluate_lifecycle(normalized)

    actionability = SetupLifecycleActionabilityPolicy().evaluate(lifecycle, normalized)

    assert lifecycle.proposed_state is LifecycleState.READY
    assert actionability.actionability is Actionability.LOW_CONFIDENCE
    assert "LOW_CONFIDENCE_SOURCE" in actionability.reason_codes


def test_failed_and_extended_are_not_actionable() -> None:
    failed_snapshot = snapshot(
        setup_score=7.8,
        classification="Breakout Base",
        distance_to_pivot_pct=1.0,
        failed_breakout=True,
    )
    extended_snapshot = snapshot(
        setup_score=7.8,
        classification="Breakout Base",
        distance_to_pivot_pct=1.0,
        close_trigger_cross=True,
        extended_atr_from_trigger=3.0,
    )

    failed = SetupLifecycleActionabilityPolicy().evaluate(
        evaluate_lifecycle(failed_snapshot),
        failed_snapshot,
    )
    extended = SetupLifecycleActionabilityPolicy().evaluate(
        evaluate_lifecycle(extended_snapshot),
        extended_snapshot,
    )

    assert failed.actionability is Actionability.BLOCKED
    assert extended.actionability is Actionability.WATCH_ONLY
