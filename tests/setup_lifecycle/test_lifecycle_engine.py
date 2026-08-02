from __future__ import annotations

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
    tightening = evaluate_lifecycle(
        snapshot(setup_score=6.8, classification="Breakout Base", range_contraction=True)
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
    confirmed = evaluate_lifecycle(
        snapshot(
            setup_score=7.8,
            classification="Breakout Base",
            distance_to_pivot_pct=1.0,
            close_trigger_cross=True,
            follow_through_sessions=2,
        ),
        previous_state=LifecycleState.TRIGGERED,
        persistence_sessions=2,
    )
    extended = evaluate_lifecycle(
        snapshot(
            setup_score=7.8,
            classification="Breakout Base",
            distance_to_pivot_pct=1.0,
            close_trigger_cross=True,
            extended_atr_from_trigger=3.0,
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
    early = evaluate_lifecycle(
        snapshot(
            setup_score=8.0,
            classification="Breakout Base",
            distance_to_pivot_pct=1.0,
            close_trigger_cross=True,
            follow_through_sessions=2,
        ),
        previous_state=LifecycleState.TRIGGERED,
        persistence_sessions=1,
    )
    confirmed = evaluate_lifecycle(
        snapshot(
            setup_score=8.0,
            classification="Breakout Base",
            distance_to_pivot_pct=1.0,
            close_trigger_cross=True,
            follow_through_sessions=2,
        ),
        previous_state=LifecycleState.TRIGGERED,
        persistence_sessions=2,
    )

    assert early.proposed_state is LifecycleState.TRIGGERED
    assert confirmed.proposed_state is LifecycleState.CONFIRMED


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
