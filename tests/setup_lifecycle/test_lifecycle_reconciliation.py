from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from test_setup_lifecycle_episode_service import (
    FakeEpisodeRepository,
    _episode,
    _snapshot,
)

from app.services.setup_lifecycle.enums import LifecycleState, SetupFamily
from app.services.setup_lifecycle.episode_service import SetupLifecycleEpisodeService
from app.services.setup_lifecycle.errors import SetupLifecycleReconciliationError


def _active(repository, episode_id: int, family: SetupFamily):
    episode = _episode(
        episode_id=episode_id,
        state=LifecycleState.DEVELOPING,
        phase="TRACKING",
        state_age_sessions=2,
        last_observed_on=date(2026, 8, 1),
    )
    episode.setup_family = family.value
    repository.active[("MSFT", "1d", family.value)] = episode
    return episode


def _current(snapshot_id: int = 200):
    return _snapshot(
        snapshot_id,
        setup_score=Decimal("6"),
        classification="Generic",
        data_as_of_date=date(2026, 8, 4),
    )


def test_acmr_cross_family_reconciles_before_current_family_opens() -> None:
    repository = FakeEpisodeRepository()
    displaced = _active(repository, 1, SetupFamily.VCP)
    service = SetupLifecycleEpisodeService(repository=repository)

    result = service.apply_snapshot(object(), _current())

    assert displaced.status == "CLOSED"
    assert displaced.current_state == "EXPIRED"
    assert displaced.current_phase == "FAMILY_DISPLACED"
    assert displaced.terminal_reason_code == "FAMILY_DISPLACED"
    assert result.episode is not None
    assert result.episode.setup_family == "GENERIC"
    assert len(result.reconciliation_results) == 1
    assert result.reconciliation_results[0].closed is True


def test_ltc_no_current_family_closes_every_active_episode() -> None:
    repository = FakeEpisodeRepository()
    displaced = _active(repository, 1, SetupFamily.CONTINUATION)
    service = SetupLifecycleEpisodeService(repository=repository)
    snapshot = _snapshot(
        201,
        setup_score=Decimal("0"),
        classification="",
        data_as_of_date=date(2026, 8, 4),
    )

    result = service.apply_snapshot(object(), snapshot)

    assert displaced.status == "CLOSED"
    assert displaced.current_phase == "NO_CURRENT_FAMILY"
    assert displaced.terminal_reason_code == "NO_CURRENT_FAMILY"
    assert result.episode is None
    assert result.warning_codes == ("NOT_TRACKABLE_FOR_EPISODE",)


def test_multiple_active_families_are_all_reconciled() -> None:
    repository = FakeEpisodeRepository()
    vcp = _active(repository, 1, SetupFamily.VCP)
    pullback = _active(repository, 2, SetupFamily.PULLBACK)
    service = SetupLifecycleEpisodeService(repository=repository)

    result = service.apply_snapshot(object(), _current())

    assert {vcp.status, pullback.status} == {"CLOSED"}
    assert {vcp.current_phase, pullback.current_phase} == {"FAMILY_DISPLACED"}
    assert len(result.reconciliation_results) == 2
    assert result.episode is not None and result.episode.setup_family == "GENERIC"


def test_same_family_progresses_without_reconciliation() -> None:
    repository = FakeEpisodeRepository()
    current = _active(repository, 1, SetupFamily.GENERIC)
    service = SetupLifecycleEpisodeService(repository=repository)

    result = service.apply_snapshot(object(), _current())

    assert result.episode is current
    assert current.status == "ACTIVE"
    assert current.current_snapshot_id == 200
    assert result.reconciliation_results == ()


def test_repeating_same_frozen_observation_does_not_repeat_displacement() -> None:
    repository = FakeEpisodeRepository()
    displaced = _active(repository, 1, SetupFamily.VCP)
    service = SetupLifecycleEpisodeService(repository=repository)
    snapshot = _current()

    first = service.apply_snapshot(object(), snapshot)
    second = service.apply_snapshot(object(), snapshot)

    assert displaced.status == "CLOSED"
    assert len(first.reconciliation_results) == 1
    assert second.reconciliation_results == ()
    assert len([event for event in repository.events if event.to_state == "EXPIRED"]) == 1


def test_duplicate_active_same_family_fails_in_preflight() -> None:
    repository = FakeEpisodeRepository()
    service = SetupLifecycleEpisodeService(repository=repository)
    first = _active(repository, 1, SetupFamily.VCP)
    second = _episode(
        episode_id=2,
        state=LifecycleState.READY,
        phase="READY",
        state_age_sessions=1,
    )
    second.setup_family = SetupFamily.VCP.value

    with pytest.raises(
        SetupLifecycleReconciliationError,
        match="MUTATION_LIFECYCLE_RECONCILIATION_DUPLICATE_ACTIVE_FAMILY",
    ) as error:
        service.plan_reconciliation(object(), _current(), preloaded_episodes=(first, second))

    assert error.value.details["stage"] == "preflight"
    assert error.value.details["ticker"] == "MSFT"
    assert error.value.details["episode_ids"] == {"VCP": [1, 2]}
