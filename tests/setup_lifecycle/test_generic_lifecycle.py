from __future__ import annotations

from dataclasses import replace
from datetime import date

from lifecycle_helpers import snapshot

from app.services.setup_lifecycle.enums import LifecycleState, SetupFamily
from app.services.setup_lifecycle.generic_adapter import GenericAdapter
from app.services.setup_lifecycle.lifecycle_engine import evaluate_lifecycle


def test_generic_adapter_maps_candidate_improving_ready_and_triggered() -> None:
    adapter = GenericAdapter()

    weak = adapter.evaluate(snapshot(setup_score=4.0, classification="Constructive Candidate"))
    prior = replace(
        snapshot(setup_score=5.2, classification="Constructive Candidate"),
        data_as_of_date=date(2026, 7, 31),
    )
    improving = adapter.evaluate(
        snapshot(setup_score=6.0, classification="Constructive Candidate"),
        history=(prior,),
    )
    ready = adapter.evaluate(snapshot(setup_score=7.2, classification="Constructive Candidate"))
    triggered = adapter.evaluate(
        snapshot(
            setup_score=7.2,
            classification="Constructive Candidate",
            close_trigger_cross=True,
        )
    )

    assert weak.setup_family is SetupFamily.GENERIC
    assert weak.phase_code == "CANDIDATE"
    assert improving.phase_code == "IMPROVING"
    assert ready.phase_code == "READY"
    assert triggered.phase_code == "TRIGGERED"


def test_generic_lifecycle_is_used_when_no_supported_family_matches() -> None:
    decision = evaluate_lifecycle(
        snapshot(setup_score=6.0, classification="Constructive Candidate")
    )

    assert decision.setup_family is SetupFamily.GENERIC
    assert decision.proposed_state is LifecycleState.DEVELOPING
