from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest
from lifecycle_helpers import snapshot

from app.services.setup_lifecycle.config import load_setup_lifecycle_config
from app.services.setup_lifecycle.enums import (
    Actionability,
    DataQualityLabel,
    LifecycleState,
    SetupFamily,
)
from app.services.setup_lifecycle.lifecycle_engine import evaluate_lifecycle


def test_every_lifecycle_state_can_be_reached_through_breakout_sequence() -> None:
    discovered = evaluate_lifecycle(snapshot(setup_score=None, classification=None))
    developing = evaluate_lifecycle(snapshot(setup_score=6.2, classification="Breakout Base"))
    prior_contraction = replace(
        snapshot(setup_score=6.2, classification="Breakout Base", range_contraction=True),
        data_as_of_date=date(2026, 7, 31),
    )
    tightening = evaluate_lifecycle(
        snapshot(setup_score=6.8, classification="Breakout Base", range_contraction=True),
        previous_snapshots=(prior_contraction,),
    )
    ready = evaluate_lifecycle(
        snapshot(setup_score=7.8, classification="Breakout Base", distance_to_pivot_pct=1.0)
    )
    triggered = evaluate_lifecycle(
        snapshot(
            setup_score=7.8,
            classification="Breakout Base",
            distance_to_pivot_pct=1.0,
            close_trigger_cross=True,
        ),
        previous_state=LifecycleState.READY,
    )
    prior_trigger = replace(
        snapshot(
            setup_score=7.8,
            classification="Breakout Base",
            distance_to_pivot_pct=1.0,
            close_trigger_cross=True,
        ),
        data_as_of_date=date(2026, 7, 31),
    )
    confirmed = evaluate_lifecycle(
        snapshot(
            setup_score=7.8,
            classification="Breakout Base",
            distance_to_pivot_pct=1.0,
            close_trigger_cross=True,
        ),
        previous_snapshots=(prior_trigger,),
        previous_state=LifecycleState.TRIGGERED,
        persistence_sessions=2,
    )
    extended = evaluate_lifecycle(
        snapshot(
            setup_score=7.8,
            classification="Breakout Base",
            distance_to_pivot_pct=1.0,
            close_trigger_cross=True,
            close_price=110.0,
            trigger_price=100.0,
            atr_value=3.0,
        ),
        previous_state=LifecycleState.CONFIRMED,
    )
    expired = evaluate_lifecycle(
        snapshot(setup_score=6.0, classification="Breakout Base"),
        previous_state=LifecycleState.DEVELOPING,
        state_age_sessions=41,
    )

    assert discovered.proposed_state is LifecycleState.DISCOVERED
    assert developing.proposed_state is LifecycleState.DEVELOPING
    assert tightening.proposed_state is LifecycleState.TIGHTENING
    assert ready.proposed_state is LifecycleState.READY
    assert triggered.proposed_state is LifecycleState.TRIGGERED
    assert confirmed.proposed_state is LifecycleState.CONFIRMED
    assert extended.proposed_state is LifecycleState.EXTENDED
    assert expired.proposed_state is LifecycleState.EXPIRED


def test_ready_tightening_oscillation_does_not_flap_under_hysteresis() -> None:
    decision = evaluate_lifecycle(
        snapshot(
            setup_score=6.8,
            classification="Breakout Base",
            range_contraction=True,
        ),
        previous_state=LifecycleState.READY,
    )

    assert decision.proposed_state is LifecycleState.READY
    assert "NO_STATE_CHANGE" in decision.reason_codes


def test_failed_breakout_transitions_immediately_to_failed() -> None:
    decision = evaluate_lifecycle(
        snapshot(
            setup_score=8.0,
            classification="Breakout Base",
            distance_to_pivot_pct=1.0,
            close_trigger_cross=False,
            failed_breakout=True,
        ),
        previous_state=LifecycleState.TRIGGERED,
    )

    assert decision.proposed_state is LifecycleState.FAILED
    assert decision.actionability_candidate is Actionability.BLOCKED
    assert decision.immediate_transition is True
    assert decision.terminal_reason == "HARD_FAILURE"


def test_triggered_follow_through_requires_configured_persistence() -> None:
    prior_trigger = replace(
        snapshot(
            setup_score=8.0,
            classification="Breakout Base",
            distance_to_pivot_pct=1.0,
            close_trigger_cross=True,
        ),
        data_as_of_date=date(2026, 7, 31),
    )
    early = evaluate_lifecycle(
        snapshot(
            setup_score=8.0,
            classification="Breakout Base",
            distance_to_pivot_pct=1.0,
            close_trigger_cross=True,
        ),
        previous_snapshots=(prior_trigger,),
        previous_state=LifecycleState.TRIGGERED,
        persistence_sessions=1,
    )
    confirmed = evaluate_lifecycle(
        snapshot(
            setup_score=8.0,
            classification="Breakout Base",
            distance_to_pivot_pct=1.0,
            close_trigger_cross=True,
        ),
        previous_snapshots=(prior_trigger,),
        previous_state=LifecycleState.TRIGGERED,
        persistence_sessions=2,
    )

    assert early.proposed_state is LifecycleState.TRIGGERED
    assert confirmed.proposed_state is LifecycleState.CONFIRMED


def test_confirmed_follow_through_does_not_reapply_entry_persistence() -> None:
    prior_trigger = replace(
        snapshot(
            setup_score=8.0,
            classification="Breakout Base",
            distance_to_pivot_pct=1.0,
            close_trigger_cross=True,
        ),
        data_as_of_date=date(2026, 7, 31),
    )

    retained = evaluate_lifecycle(
        snapshot(
            setup_score=8.0,
            classification="Breakout Base",
            distance_to_pivot_pct=1.0,
            close_trigger_cross=True,
        ),
        previous_snapshots=(prior_trigger,),
        previous_state=LifecycleState.CONFIRMED,
        persistence_sessions=0,
    )

    assert retained.proposed_state is LifecycleState.CONFIRMED
    assert "NO_STATE_CHANGE" in retained.reason_codes


def test_missing_required_evidence_keeps_state_and_lowers_confidence() -> None:
    decision = evaluate_lifecycle(
        snapshot(setup_score=None, classification=None, data_quality=DataQualityLabel.LOW),
        previous_state=LifecycleState.DEVELOPING,
        previous_phase="BASE_FORMING",
    )

    assert decision.proposed_state is LifecycleState.DEVELOPING
    assert decision.confidence_score == 0
    assert "INSUFFICIENT_FAMILY_EVIDENCE" in decision.reason_codes


def test_terminal_states_never_reopen() -> None:
    decision = evaluate_lifecycle(
        snapshot(
            setup_score=9.0,
            classification="Breakout Base",
            distance_to_pivot_pct=0.5,
            close_trigger_cross=True,
        ),
        previous_state=LifecycleState.FAILED,
        previous_phase="BREAKOUT_FAILED",
    )

    assert decision.proposed_state is LifecycleState.FAILED
    assert decision.reason_codes == ("TERMINAL_STATE_LOCKED",)
    assert decision.actionability_candidate is Actionability.BLOCKED
    assert decision.confidence_score == 0


def test_terminal_expired_preserves_confidence_and_is_watch_only() -> None:
    decision = evaluate_lifecycle(
        snapshot(setup_score=9.0, classification="Breakout Base"),
        previous_state=LifecycleState.EXPIRED,
        previous_phase="EXPIRED",
        previous_confidence_score=73,
    )

    assert decision.proposed_state is LifecycleState.EXPIRED
    assert decision.actionability_candidate is Actionability.WATCH_ONLY
    assert decision.confidence_score == 73
    assert decision.evidence["terminal_locked"] is True
    assert decision.evidence["confidence_preserved"] is True


def test_prior_snapshot_history_is_ordered_bounded_and_point_in_time_safe() -> None:
    current = replace(
        snapshot(setup_score=8.0, classification="Breakout Base"),
        data_as_of_date=date(2026, 8, 5),
    )
    first = replace(current, data_as_of_date=date(2026, 8, 3))
    second = replace(current, data_as_of_date=date(2026, 8, 4))

    decision = evaluate_lifecycle(current, previous_snapshots=(first, second))

    assert decision.evidence["prior_snapshot_count"] == 2
    assert decision.evidence["prior_snapshot_dates"] == ["2026-08-03", "2026-08-04"]

    with pytest.raises(ValueError, match="trading-date ordered"):
        evaluate_lifecycle(current, previous_snapshots=(second, first))
    with pytest.raises(ValueError, match="current/future"):
        evaluate_lifecycle(current, previous_snapshots=(first, current))
    with pytest.raises(ValueError, match="ticker/timeframe"):
        evaluate_lifecycle(
            current,
            previous_snapshots=(replace(first, ticker="AAPL"),),
        )


def test_generated_transition_invariants_cover_terminal_and_state_age_boundaries() -> None:
    bullish_snapshots = (
        snapshot(
            setup_score=9.0,
            classification="Breakout Base",
            distance_to_pivot_pct=0.5,
            close_trigger_cross=True,
        ),
        snapshot(
            setup_score=8.0,
            classification="Pullback Uptrend",
            support_distance_atr=0.8,
            reversal_ready=True,
        ),
        snapshot(
            setup_score=7.8,
            classification="VCP",
            contraction_count=2,
            volume_percentile_252=30,
        ),
    )

    for terminal_state in (LifecycleState.FAILED, LifecycleState.EXPIRED):
        for candidate in bullish_snapshots:
            decision = evaluate_lifecycle(
                candidate,
                previous_state=terminal_state,
                previous_phase=terminal_state.value,
            )
            assert decision.proposed_state is terminal_state
            assert decision.reason_codes == ("TERMINAL_STATE_LOCKED",)

    max_age = load_setup_lifecycle_config().families.policies[
        SetupFamily.BREAKOUT
    ].max_age_sessions
    for age in range(0, max_age):
        decision = evaluate_lifecycle(
            snapshot(setup_score=6.0, classification="Breakout Base"),
            previous_state=LifecycleState.DEVELOPING,
            state_age_sessions=age,
        )
        assert decision.proposed_state is LifecycleState.DEVELOPING

    expired = evaluate_lifecycle(
        snapshot(setup_score=6.0, classification="Breakout Base"),
        previous_state=LifecycleState.DEVELOPING,
        state_age_sessions=max_age,
    )
    assert expired.proposed_state is LifecycleState.EXPIRED
