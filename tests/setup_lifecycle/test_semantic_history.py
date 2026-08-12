from __future__ import annotations

from dataclasses import replace
from datetime import date

from lifecycle_helpers import snapshot

from app.services.setup_lifecycle.breakout_adapter import BreakoutAdapter
from app.services.setup_lifecycle.continuation_adapter import ContinuationAdapter
from app.services.setup_lifecycle.family_adapters import numeric_history_is_improving
from app.services.setup_lifecycle.generic_adapter import GenericAdapter
from app.services.setup_lifecycle.pullback_adapter import PullbackAdapter
from app.services.setup_lifecycle.vcp_adapter import VcpAdapter


def test_flat_history_is_not_classified_as_improving() -> None:
    current = snapshot(
        setup_score=6.0,
        classification="Breakout Base",
        volume_percentile_252=55,
    )
    history = (
        _prior(
            setup_score=6.0,
            classification="Breakout Base",
            volume_percentile_252=55,
        ),
    )

    assert (
        numeric_history_is_improving(
            current,
            history,
            "volume_percentile_252",
            lower_is_better=True,
        )
        is False
    )


def test_same_breakout_current_snapshot_changes_with_valid_contraction_history() -> None:
    current = snapshot(
        setup_score=6.8,
        classification="Breakout Base",
        range_contraction=True,
    )
    improving = (_prior(setup_score=6.3, classification="Breakout Base", range_contraction=True),)
    absent = (_prior(setup_score=6.3, classification="Breakout Base", range_contraction=False),)

    with_history = BreakoutAdapter().evaluate(current, history=improving)
    without_history = BreakoutAdapter().evaluate(current, history=absent)

    assert with_history.phase_code == "RANGE_CONTRACTION"
    assert without_history.phase_code == "BASE_FORMING"
    assert with_history.evidence["contraction_sessions"] == 2
    assert without_history.evidence["contraction_sessions"] == 1


def test_same_breakout_current_snapshot_changes_with_hold_history() -> None:
    current = snapshot(
        setup_score=7.8,
        classification="Breakout Base",
        distance_to_pivot_pct=1.0,
        close_trigger_cross=True,
    )
    held = (_prior(setup_score=7.8, classification="Breakout Base", close_trigger_cross=True),)
    not_held = (
        _prior(setup_score=7.8, classification="Breakout Base", close_trigger_cross=False),
    )

    confirmed = BreakoutAdapter().evaluate(current, history=held)
    triggered = BreakoutAdapter().evaluate(current, history=not_held)

    assert confirmed.confirmed is True
    assert triggered.confirmed is False


def test_same_pullback_current_snapshot_changes_with_selling_pressure_history() -> None:
    current = snapshot(
        setup_score=6.4,
        classification="Pullback Uptrend",
        trend_score=7.0,
        red_volume_declining=True,
        volume_percentile_252=40,
        range_percentile_252=40,
    )
    constructive = (
        _prior(
            setup_score=6.0,
            classification="Pullback Uptrend",
            trend_score=7.0,
            volume_percentile_252=60,
            range_percentile_252=60,
        ),
    )
    distribution = (
        _prior(
            setup_score=6.0,
            classification="Pullback Uptrend",
            trend_score=7.0,
            volume_percentile_252=20,
            range_percentile_252=20,
        ),
    )

    tightening = PullbackAdapter().evaluate(current, history=constructive)
    not_tightening = PullbackAdapter().evaluate(current, history=distribution)

    assert tightening.phase_code == "SELLING_PRESSURE_DECLINING"
    assert not_tightening.phase_code == "PULLBACK_STARTED"


def test_same_vcp_current_snapshot_changes_with_contraction_history() -> None:
    current = snapshot(
        setup_score=7.8,
        classification="VCP",
        volume_percentile_252=30,
    )
    contraction = (_prior(setup_score=6.5, classification="VCP", range_contraction=True),)
    no_contraction = (
        _prior(setup_score=6.5, classification="Constructive Candidate"),
    )

    ready = VcpAdapter().evaluate(current, history=contraction)
    developing = VcpAdapter().evaluate(current, history=no_contraction)

    assert ready.ready is True
    assert developing.ready is False


def test_same_continuation_current_snapshot_changes_with_tight_range_history() -> None:
    current = snapshot(
        setup_score=7.8,
        classification="Continuation Pause",
        range_percentile_252=30,
    )
    tight = (
        _prior(
            setup_score=6.8,
            classification="Continuation Pause",
            range_percentile_252=35,
        ),
    )
    wide = (
        _prior(
            setup_score=6.8,
            classification="Continuation Pause",
            range_percentile_252=60,
        ),
    )

    ready = ContinuationAdapter().evaluate(current, history=tight)
    tightening = ContinuationAdapter().evaluate(current, history=wide)

    assert ready.ready is True
    assert tightening.ready is False


def test_same_generic_current_snapshot_changes_with_score_history() -> None:
    current = snapshot(setup_score=6.0, classification="Constructive Candidate")
    rising = (_prior(setup_score=5.0, classification="Constructive Candidate"),)
    falling = (_prior(setup_score=7.0, classification="Constructive Candidate"),)

    improving = GenericAdapter().evaluate(current, history=rising)
    candidate = GenericAdapter().evaluate(current, history=falling)

    assert improving.phase_code == "IMPROVING"
    assert candidate.phase_code == "CANDIDATE"


def _prior(*, setup_score: float, classification: str, **signals):
    return replace(
        snapshot(
            setup_score=setup_score,
            classification=classification,
            **signals,
        ),
        data_as_of_date=date(2026, 7, 31),
    )
