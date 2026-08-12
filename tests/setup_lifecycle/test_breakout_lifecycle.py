from __future__ import annotations

from dataclasses import replace
from datetime import date

from lifecycle_helpers import snapshot

from app.services.setup_lifecycle.breakout_adapter import BreakoutAdapter
from app.services.setup_lifecycle.enums import LifecycleState, SetupFamily


def test_breakout_adapter_maps_base_contraction_ready_triggered_and_confirmed() -> None:
    adapter = BreakoutAdapter()

    base = adapter.evaluate(snapshot(setup_score=5.6, classification="Breakout Base"))
    contraction_history = replace(
        snapshot(setup_score=6.4, classification="Breakout Base", range_contraction=True),
        data_as_of_date=date(2026, 7, 31),
    )
    tightening = adapter.evaluate(
        snapshot(setup_score=6.8, classification="Breakout Base", range_contraction=True),
        history=(contraction_history,),
    )
    ready = adapter.evaluate(
        snapshot(setup_score=7.8, classification="Breakout Base", distance_to_pivot_pct=1.0)
    )
    triggered = adapter.evaluate(
        snapshot(
            setup_score=7.8,
            classification="Breakout Base",
            distance_to_pivot_pct=1.0,
            close_trigger_cross=True,
        )
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
    confirmed = adapter.evaluate(
        snapshot(
            setup_score=7.8,
            classification="Breakout Base",
            distance_to_pivot_pct=1.0,
            close_trigger_cross=True,
        ),
        history=(prior_trigger,),
    )

    assert base.setup_family is SetupFamily.BREAKOUT
    assert base.phase_code == "BASE_FORMING"
    assert tightening.phase_code == "RANGE_CONTRACTION"
    assert ready.phase_code == "PIVOT_READY"
    assert triggered.phase_code == "BREAKOUT"
    assert confirmed.confirmed is True
    assert "FOLLOW_THROUGH_CONFIRMED" in confirmed.reason_codes


def test_breakout_adapter_flags_hard_failure_and_expiry() -> None:
    adapter = BreakoutAdapter()

    failed = adapter.evaluate(
        snapshot(
            setup_score=8.0,
            classification="Breakout Base",
            failed_breakout=True,
        ),
        previous_state=LifecycleState.TRIGGERED,
    )
    expired = adapter.evaluate(
        snapshot(setup_score=6.0, classification="Breakout Base"),
        state_age_sessions=41,
    )

    assert failed.hard_failure is True
    assert "FAILED_BREAKOUT" in failed.reason_codes
    assert expired.phase_code == "EXPIRED"
