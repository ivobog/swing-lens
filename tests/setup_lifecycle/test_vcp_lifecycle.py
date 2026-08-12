from __future__ import annotations

from dataclasses import replace
from datetime import date

from lifecycle_helpers import snapshot

from app.services.setup_lifecycle.enums import LifecycleState, SetupFamily
from app.services.setup_lifecycle.lifecycle_engine import evaluate_lifecycle
from app.services.setup_lifecycle.vcp_adapter import VcpAdapter


def test_vcp_adapter_maps_contractions_dry_up_ready_and_triggered() -> None:
    adapter = VcpAdapter()

    first = adapter.evaluate(snapshot(setup_score=5.8, classification="VCP"))
    prior = replace(
        snapshot(setup_score=5.8, classification="VCP"),
        data_as_of_date=date(2026, 7, 31),
    )
    second = adapter.evaluate(
        snapshot(setup_score=6.2, classification="VCP"),
        history=(prior,),
    )
    dry_up = adapter.evaluate(
        snapshot(
            setup_score=6.8,
            classification="VCP",
            volume_percentile_252=30,
        ),
        history=(prior,),
    )
    ready = adapter.evaluate(
        snapshot(
            setup_score=7.8,
            classification="VCP",
            volume_percentile_252=30,
        ),
        history=(prior,),
    )
    triggered = adapter.evaluate(
        snapshot(
            setup_score=7.8,
            classification="VCP",
            volume_percentile_252=30,
            close_trigger_cross=True,
        ),
        history=(prior,),
    )

    assert first.setup_family is SetupFamily.VCP
    assert first.phase_code == "CONTRACTION_1"
    assert second.phase_code == "CONTRACTION_2"
    assert dry_up.phase_code == "VOLUME_DRY_UP"
    assert ready.phase_code == "PIVOT_READY"
    assert triggered.phase_code == "BREAKOUT"


def test_vcp_failure_and_expiry_map_to_terminal_states() -> None:
    failed = evaluate_lifecycle(
        snapshot(
            setup_score=7.8,
            classification="VCP",
            volume_percentile_252=30,
            failed_breakout=True,
        ),
        previous_state=LifecycleState.READY,
    )
    expired = evaluate_lifecycle(
        snapshot(setup_score=6.0, classification="VCP"),
        previous_state=LifecycleState.DEVELOPING,
        state_age_sessions=45,
    )

    assert failed.setup_family is SetupFamily.VCP
    assert failed.proposed_state is LifecycleState.FAILED
    assert expired.proposed_state is LifecycleState.EXPIRED
