from __future__ import annotations

from datetime import date

from app.models.tables import SetupLifecycleEpisode
from app.services.setup_lifecycle.enums import LifecycleState, SetupFamily
from app.services.setup_lifecycle.episode_service import select_primary_episodes


def test_primary_selection_is_deterministic() -> None:
    breakout = _episode(
        family=SetupFamily.BREAKOUT,
        state=LifecycleState.READY,
        confidence=80,
        setup_score=7.8,
        current_as_of_date=date(2026, 8, 1),
    )
    pullback = _episode(
        family=SetupFamily.PULLBACK,
        state=LifecycleState.CONFIRMED,
        confidence=70,
        setup_score=7.6,
        current_as_of_date=date(2026, 8, 1),
    )
    vcp = _episode(
        family=SetupFamily.VCP,
        state=LifecycleState.READY,
        confidence=80,
        setup_score=7.8,
        current_as_of_date=date(2026, 8, 1),
    )

    selected = select_primary_episodes([vcp, breakout, pullback])

    assert selected == [pullback, breakout, vcp]


def _episode(
    *,
    family: SetupFamily,
    state: LifecycleState,
    confidence: int,
    setup_score: float,
    current_as_of_date: date,
) -> SetupLifecycleEpisode:
    return SetupLifecycleEpisode(
        ticker="MSFT",
        timeframe="1d",
        setup_family=family.value,
        status="ACTIVE",
        opened_on=date(2026, 8, 1),
        current_as_of_date=current_as_of_date,
        last_observed_on=current_as_of_date,
        current_state=state.value,
        current_phase="TEST",
        state_entered_on=date(2026, 8, 1),
        state_age_sessions=0,
        current_actionability="WATCH_ONLY",
        confidence_score=confidence,
        confidence_label="NORMAL",
        engine_version="slse-1.0.0",
        config_version="v1",
        config_hash="hash",
        metadata_json={"setup_score": setup_score},
    )
