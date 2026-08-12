from __future__ import annotations

from dataclasses import replace
from datetime import date

from lifecycle_helpers import snapshot

from app.services.setup_lifecycle.enums import LifecycleState, SetupFamily
from app.services.setup_lifecycle.lifecycle_engine import evaluate_lifecycle
from app.services.setup_lifecycle.pullback_adapter import PullbackAdapter


def test_pullback_adapter_maps_retreat_support_ready_triggered_and_confirmed() -> None:
    adapter = PullbackAdapter()

    started = adapter.evaluate(snapshot(setup_score=5.8, classification="Pullback Uptrend"))
    prior_pullback = replace(
        snapshot(
            setup_score=6.0,
            classification="Pullback Uptrend",
            trend_score=7.0,
            volume_percentile_252=60,
            range_percentile_252=60,
        ),
        data_as_of_date=date(2026, 7, 31),
    )
    declining = adapter.evaluate(
        snapshot(
            setup_score=6.4,
            classification="Pullback Uptrend",
            trend_score=7.0,
            red_volume_declining=True,
            volume_percentile_252=40,
            range_percentile_252=40,
        ),
        history=(prior_pullback,),
    )
    support_test = adapter.evaluate(
        snapshot(
            setup_score=6.8,
            classification="Pullback Uptrend",
            held_near_support=True,
        )
    )
    ready = adapter.evaluate(
        snapshot(
            setup_score=7.8,
            classification="Pullback Uptrend",
            held_near_support=True,
        )
    )
    triggered = adapter.evaluate(
        snapshot(
            setup_score=7.8,
            classification="Pullback Uptrend",
            held_near_support=True,
            close_trigger_cross=True,
        )
    )
    prior_trigger = replace(
        snapshot(
            setup_score=7.8,
            classification="Pullback Uptrend",
            held_near_support=True,
            close_trigger_cross=True,
        ),
        data_as_of_date=date(2026, 7, 31),
    )
    confirmed = adapter.evaluate(
        snapshot(
            setup_score=7.8,
            classification="Pullback Uptrend",
            held_near_support=True,
            close_trigger_cross=True,
        ),
        history=(prior_trigger,),
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
            held_near_support=True,
            heavy_mid_ma_break=True,
        ),
        previous_state=LifecycleState.READY,
    )

    assert decision.setup_family is SetupFamily.PULLBACK
    assert decision.proposed_state is LifecycleState.FAILED
    assert "SUPPORT_BREAK" in decision.reason_codes
