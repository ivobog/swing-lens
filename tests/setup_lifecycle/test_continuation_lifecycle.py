from __future__ import annotations

from dataclasses import replace
from datetime import date

from lifecycle_helpers import snapshot

from app.services.setup_lifecycle.continuation_adapter import ContinuationAdapter
from app.services.setup_lifecycle.enums import LifecycleState, SetupFamily
from app.services.setup_lifecycle.lifecycle_engine import evaluate_lifecycle


def test_continuation_adapter_maps_pause_flag_tight_ready_triggered_and_extended() -> None:
    adapter = ContinuationAdapter()

    pause = adapter.evaluate(snapshot(setup_score=5.8, classification="Continuation Pause"))
    flag = adapter.evaluate(snapshot(setup_score=6.2, classification="Bull Flag"))
    tight = adapter.evaluate(
        snapshot(
            setup_score=6.8,
            classification="Continuation Pause",
            range_percentile_252=30,
        )
    )
    prior_tight = replace(
        snapshot(
            setup_score=6.8,
            classification="Continuation Pause",
            range_percentile_252=35,
        ),
        data_as_of_date=date(2026, 7, 31),
    )
    ready = adapter.evaluate(
        snapshot(
            setup_score=7.8,
            classification="Continuation Pause",
            range_percentile_252=30,
        ),
        history=(prior_tight,),
    )
    triggered = adapter.evaluate(
        snapshot(
            setup_score=7.8,
            classification="Continuation Pause",
            range_percentile_252=30,
            close_trigger_cross=True,
        ),
        history=(prior_tight,),
    )
    extended = adapter.evaluate(
        snapshot(
            setup_score=7.8,
            classification="Continuation Pause",
            range_percentile_252=30,
            close_trigger_cross=True,
            close_price=110.0,
            trigger_price=100.0,
            atr_value=3.0,
        ),
        history=(prior_tight,),
    )

    assert pause.setup_family is SetupFamily.CONTINUATION
    assert pause.phase_code == "PAUSE"
    assert flag.phase_code == "FLAG_FORMING"
    assert tight.phase_code == "TIGHT_RANGE"
    assert ready.phase_code == "CONTINUATION_READY"
    assert triggered.phase_code == "CONTINUATION_TRIGGER"
    assert extended.extended is True


def test_continuation_failure_and_expiry_map_to_terminal_states() -> None:
    failed = evaluate_lifecycle(
        snapshot(
            setup_score=7.8,
            classification="Continuation Pause",
            range_percentile_252=30,
            heavy_mid_ma_break=True,
        ),
        previous_state=LifecycleState.READY,
    )
    expired = evaluate_lifecycle(
        snapshot(setup_score=6.0, classification="Continuation Pause"),
        previous_state=LifecycleState.DEVELOPING,
        state_age_sessions=20,
    )

    assert failed.setup_family is SetupFamily.CONTINUATION
    assert failed.proposed_state is LifecycleState.FAILED
    assert expired.proposed_state is LifecycleState.EXPIRED
