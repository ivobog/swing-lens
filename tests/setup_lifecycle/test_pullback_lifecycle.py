from __future__ import annotations

from lifecycle_helpers import snapshot

from app.services.setup_lifecycle.enums import LifecycleState, SetupFamily
from app.services.setup_lifecycle.lifecycle_engine import evaluate_lifecycle
from app.services.setup_lifecycle.pullback_adapter import PullbackAdapter


def test_pullback_adapter_maps_retreat_support_ready_triggered_and_confirmed() -> None:
    adapter = PullbackAdapter()

    started = adapter.evaluate(snapshot(setup_score=5.8, classification="Pullback Uptrend"))
    declining = adapter.evaluate(
        snapshot(
            setup_score=6.4,
            classification="Pullback Uptrend",
            declining_volume=True,
        )
    )
    support_test = adapter.evaluate(
        snapshot(
            setup_score=6.8,
            classification="Pullback Uptrend",
            support_distance_atr=0.8,
        )
    )
    ready = adapter.evaluate(
        snapshot(
            setup_score=7.8,
            classification="Pullback Uptrend",
            support_distance_atr=0.8,
            reversal_ready=True,
        )
    )
    triggered = adapter.evaluate(
        snapshot(
            setup_score=7.8,
            classification="Pullback Uptrend",
            support_distance_atr=0.8,
            reversal_ready=True,
            close_trigger_cross=True,
        )
    )
    confirmed = adapter.evaluate(
        snapshot(
            setup_score=7.8,
            classification="Pullback Uptrend",
            support_distance_atr=0.8,
            reversal_ready=True,
            close_trigger_cross=True,
            follow_through_sessions=2,
        )
    )

    assert started.setup_family is SetupFamily.PULLBACK
    assert started.phase_code == "PULLBACK_STARTED"
    assert declining.phase_code == "SELLING_PRESSURE_DECLINING"
    assert support_test.phase_code == "SUPPORT_TEST"
    assert ready.phase_code == "REVERSAL_READY"
    assert triggered.phase_code == "REVERSAL_TRIGGER"
    assert confirmed.confirmed is True


def test_pullback_support_break_fails_immediately() -> None:
    decision = evaluate_lifecycle(
        snapshot(
            setup_score=7.8,
            classification="Pullback Uptrend",
            support_distance_atr=0.8,
            support_break=True,
        ),
        previous_state=LifecycleState.READY,
    )

    assert decision.setup_family is SetupFamily.PULLBACK
    assert decision.proposed_state is LifecycleState.FAILED
    assert "SUPPORT_BREAK" in decision.reason_codes
