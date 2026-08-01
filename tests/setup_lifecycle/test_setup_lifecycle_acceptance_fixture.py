from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from lifecycle_helpers import snapshot

from app.services.setup_lifecycle.actionability_policy import SetupLifecycleActionabilityPolicy
from app.services.setup_lifecycle.enums import (
    Actionability,
    DataQualityLabel,
    LifecycleState,
    SetupFamily,
)
from app.services.setup_lifecycle.lifecycle_engine import evaluate_lifecycle


@dataclass(frozen=True)
class GoldenLifecycleFixture:
    name: str
    snapshot_kwargs: dict
    expected_family: SetupFamily
    expected_phase: str
    expected_state: LifecycleState
    expected_actionability: Actionability
    previous_state: LifecycleState | None = None
    state_age_sessions: int = 0
    persistence_sessions: int = 0
    missing_observation_sessions: int = 0
    data_quality: DataQualityLabel = DataQualityLabel.HIGH
    warning_flags: tuple[str, ...] = ()
    expected_blockers: tuple[str, ...] = ()


GOLDEN_FIXTURES = (
    GoldenLifecycleFixture(
        name="clean breakout",
        snapshot_kwargs={
            "setup_score": 7.8,
            "classification": "Breakout Base",
            "distance_to_pivot_pct": 1.0,
            "close_trigger_cross": True,
            "follow_through_sessions": 2,
        },
        expected_family=SetupFamily.BREAKOUT,
        expected_phase="FOLLOW_THROUGH",
        expected_state=LifecycleState.CONFIRMED,
        expected_actionability=Actionability.ACTIONABLE,
        persistence_sessions=2,
    ),
    GoldenLifecycleFixture(
        name="failed breakout",
        snapshot_kwargs={
            "setup_score": 8.0,
            "classification": "Breakout Base",
            "failed_breakout": True,
        },
        expected_family=SetupFamily.BREAKOUT,
        expected_phase="BREAKOUT_FAILED",
        expected_state=LifecycleState.FAILED,
        expected_actionability=Actionability.BLOCKED,
        previous_state=LifecycleState.TRIGGERED,
        expected_blockers=("FAILED",),
    ),
    GoldenLifecycleFixture(
        name="clean bull pullback",
        snapshot_kwargs={
            "setup_score": 7.8,
            "classification": "Pullback Uptrend",
            "support_distance_atr": 0.8,
            "reversal_ready": True,
            "close_trigger_cross": True,
            "follow_through_sessions": 2,
        },
        expected_family=SetupFamily.PULLBACK,
        expected_phase="FOLLOW_THROUGH",
        expected_state=LifecycleState.CONFIRMED,
        expected_actionability=Actionability.ACTIONABLE,
        persistence_sessions=2,
    ),
    GoldenLifecycleFixture(
        name="deteriorating pullback",
        snapshot_kwargs={
            "setup_score": 7.8,
            "classification": "Pullback Uptrend",
            "support_distance_atr": 0.8,
            "support_break": True,
        },
        expected_family=SetupFamily.PULLBACK,
        expected_phase="SUPPORT_BREAK",
        expected_state=LifecycleState.FAILED,
        expected_actionability=Actionability.BLOCKED,
        previous_state=LifecycleState.READY,
        expected_blockers=("FAILED",),
    ),
    GoldenLifecycleFixture(
        name="vcp",
        snapshot_kwargs={
            "setup_score": 7.8,
            "classification": "VCP",
            "contraction_count": 2,
            "volume_percentile_252": 30,
            "close_trigger_cross": True,
        },
        expected_family=SetupFamily.VCP,
        expected_phase="BREAKOUT",
        expected_state=LifecycleState.TRIGGERED,
        expected_actionability=Actionability.ACTIONABLE,
    ),
    GoldenLifecycleFixture(
        name="extended momentum",
        snapshot_kwargs={
            "setup_score": 7.8,
            "classification": "Continuation Pause",
            "range_percentile_252": 30,
            "close_trigger_cross": True,
            "extended_atr_from_trigger": 3.0,
        },
        expected_family=SetupFamily.CONTINUATION,
        expected_phase="CONTINUATION_TRIGGER",
        expected_state=LifecycleState.EXTENDED,
        expected_actionability=Actionability.WATCH_ONLY,
        previous_state=LifecycleState.TRIGGERED,
    ),
    GoldenLifecycleFixture(
        name="choppy score oscillation",
        snapshot_kwargs={
            "setup_score": 7.1,
            "classification": "Breakout Base",
            "distance_to_pivot_pct": 2.5,
        },
        expected_family=SetupFamily.BREAKOUT,
        expected_phase="BASE_FORMING",
        expected_state=LifecycleState.READY,
        expected_actionability=Actionability.LOW_CONFIDENCE,
        previous_state=LifecycleState.READY,
    ),
    GoldenLifecycleFixture(
        name="missing-data sequence",
        snapshot_kwargs={
            "setup_score": 7.8,
            "classification": "Breakout Base",
            "distance_to_pivot_pct": 1.0,
        },
        expected_family=SetupFamily.BREAKOUT,
        expected_phase="PIVOT_READY",
        expected_state=LifecycleState.READY,
        expected_actionability=Actionability.BLOCKED,
        data_quality=DataQualityLabel.INSUFFICIENT,
        warning_flags=("MISSING_REQUIRED_SETUP_SCORE",),
        expected_blockers=("HARD_REQUIRED_DATA_ABSENT", "INSUFFICIENT_DATA_QUALITY"),
    ),
    GoldenLifecycleFixture(
        name="market-gate block",
        snapshot_kwargs={
            "setup_score": 7.8,
            "classification": "Breakout Base",
            "distance_to_pivot_pct": 1.0,
            "market_regime": "risk_off",
        },
        expected_family=SetupFamily.BREAKOUT,
        expected_phase="PIVOT_READY",
        expected_state=LifecycleState.READY,
        expected_actionability=Actionability.BLOCKED,
        expected_blockers=("MARKET_POLICY_BLOCK",),
    ),
    GoldenLifecycleFixture(
        name="filtered-universe observation gap",
        snapshot_kwargs={
            "setup_score": 6.0,
            "classification": "Breakout Base",
        },
        expected_family=SetupFamily.BREAKOUT,
        expected_phase="BASE_FORMING",
        expected_state=LifecycleState.EXPIRED,
        expected_actionability=Actionability.WATCH_ONLY,
        previous_state=LifecycleState.DEVELOPING,
        missing_observation_sessions=4,
    ),
)


@pytest.mark.parametrize("fixture", GOLDEN_FIXTURES, ids=[item.name for item in GOLDEN_FIXTURES])
def test_phase_12_golden_lifecycle_acceptance_fixtures(
    fixture: GoldenLifecycleFixture,
) -> None:
    normalized = snapshot(
        data_quality=fixture.data_quality,
        **fixture.snapshot_kwargs,
    )
    if fixture.warning_flags:
        normalized = replace(normalized, warning_flags=fixture.warning_flags)

    lifecycle = evaluate_lifecycle(
        normalized,
        previous_state=fixture.previous_state,
        state_age_sessions=fixture.state_age_sessions,
        persistence_sessions=fixture.persistence_sessions,
        missing_observation_sessions=fixture.missing_observation_sessions,
    )
    actionability = SetupLifecycleActionabilityPolicy().evaluate(lifecycle, normalized)

    assert lifecycle.setup_family is fixture.expected_family
    assert lifecycle.phase_code == fixture.expected_phase
    assert lifecycle.proposed_state is fixture.expected_state
    assert actionability.actionability is fixture.expected_actionability
    assert actionability.blockers == fixture.expected_blockers


def test_phase_12_acceptance_fixtures_cover_required_release_scenarios() -> None:
    names = {fixture.name for fixture in GOLDEN_FIXTURES}

    assert names == {
        "clean breakout",
        "failed breakout",
        "clean bull pullback",
        "deteriorating pullback",
        "vcp",
        "extended momentum",
        "choppy score oscillation",
        "missing-data sequence",
        "market-gate block",
        "filtered-universe observation gap",
    }


def test_phase_12_research_only_acceptance_surface_has_no_order_routes() -> None:
    route_source = "app/routers/setup_lifecycle_routes.py"
    service_sources = (
        "app/services/setup_lifecycle/alert_service.py",
        "app/services/setup_lifecycle/evaluation_service.py",
        "app/services/setup_lifecycle/episode_service.py",
        "app/services/setup_lifecycle/job_handlers.py",
        "app/services/setup_lifecycle/maintenance_service.py",
        "app/services/setup_lifecycle/replay_service.py",
    )
    forbidden_fragments = (
        "placeOrder",
        "submit_order",
        "cancel_order",
        "modify_order",
        "/orders",
        "broker_order",
    )

    combined = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in (route_source, *service_sources)
    )

    assert all(fragment not in combined for fragment in forbidden_fragments)
