from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.models.tables import SetupLifecycleEpisode
from app.services.setup_lifecycle.dtos import EpisodeApplyResult
from app.services.setup_lifecycle.maintenance_service import (
    SetupLifecycleMaintenanceService,
)


def test_daily_maintenance_expires_absent_episode_once() -> None:
    episode = _episode()
    episode_service = FakeEpisodeService(
        results=[
            EpisodeApplyResult(episode_id=1, updated=True, closed=True),
            EpisodeApplyResult(episode_id=None),
        ]
    )
    service = SetupLifecycleMaintenanceService(
        episode_service=episode_service,
        alert_service=FakeAlertService(),
    )
    db = FakeMaintenanceDb(active_episodes=[episode])

    first = service.daily_maintenance(db, as_of_date=date(2026, 8, 5))
    second = service.daily_maintenance(db, as_of_date=date(2026, 8, 5))

    assert first.expired == 1
    assert first.aged == 1
    assert second.expired == 0
    assert second.aged == 0


def test_daily_maintenance_skips_non_completed_market_session() -> None:
    service = SetupLifecycleMaintenanceService(
        episode_service=FakeEpisodeService(),
        alert_service=FakeAlertService(),
    )

    result = service.daily_maintenance(
        FakeMaintenanceDb(active_episodes=[_episode()]),
        as_of_date=date(2026, 8, 5),
        market_session_completed=False,
    )

    assert result.status == "SKIPPED"
    assert result.warnings == ("MARKET_SESSION_NOT_COMPLETED",)


def test_repair_ticker_is_scoped_and_reports_counts() -> None:
    snapshot = SimpleNamespace(id=10, ticker="MSFT", data_as_of_date=date(2026, 8, 1))
    episode_result = SimpleNamespace(
        updated=True,
        opened=False,
        closed=False,
        decision=SimpleNamespace(setup_family=SimpleNamespace(value="BREAKOUT")),
    )
    episode_service = FakeEpisodeService(results=[episode_result])
    alert_service = FakeAlertService(created=1)
    service = SetupLifecycleMaintenanceService(
        episode_service=episode_service,
        alert_service=alert_service,
    )

    result = service.repair_ticker(
        FakeMaintenanceDb(repair_snapshots=[snapshot]),
        ticker="msft",
        as_of_date=date(2026, 8, 1),
        setup_family="BREAKOUT",
    )

    assert result.repaired == 1
    assert result.alerts_created == 1
    assert episode_service.repaired_snapshot_ids == [10]


def test_alert_rebuild_deduplicates_through_alert_service() -> None:
    alert_service = FakeAlertService(created=1, suppressed=2)
    service = SetupLifecycleMaintenanceService(
        episode_service=FakeEpisodeService(),
        alert_service=alert_service,
    )

    result = service.rebuild_alerts(
        FakeMaintenanceDb(
            lifecycle_events=[SimpleNamespace(id=1)],
            signal_change_events=[SimpleNamespace(id=2), SimpleNamespace(id=3)],
        ),
        ticker="MSFT",
    )

    assert result.alerts_created == 2
    assert result.alerts_suppressed == 4
    assert alert_service.seeded == 1


class FakeMaintenanceDb:
    def __init__(
        self,
        *,
        active_episodes=None,
        repair_snapshots=None,
        lifecycle_events=None,
        signal_change_events=None,
    ) -> None:
        self.active_episodes = list(active_episodes or [])
        self.repair_snapshots = list(repair_snapshots or [])
        self.lifecycle_events = list(lifecycle_events or [])
        self.signal_change_events = list(signal_change_events or [])

    def scalars(self, statement):
        text = str(statement)
        if "setup_lifecycle_episodes" in text:
            rows = self.active_episodes
            self.active_episodes = []
            return rows
        if "setup_signal_snapshots" in text:
            return self.repair_snapshots
        if "setup_lifecycle_events" in text:
            return self.lifecycle_events
        if "signal_change_events" in text:
            return self.signal_change_events
        return []


class FakeEpisodeService:
    def __init__(self, results=None) -> None:
        self.results = list(results or [])
        self.repaired_snapshot_ids = []

    def apply_observation_gap(self, *_args, **_kwargs):
        if self.results:
            return self.results.pop(0)
        return EpisodeApplyResult(episode_id=None)

    def apply_snapshot(self, _db, snapshot, **_kwargs):
        self.repaired_snapshot_ids.append(snapshot.id)
        if self.results:
            return self.results.pop(0)
        return SimpleNamespace(
            updated=False,
            opened=False,
            closed=False,
            decision=SimpleNamespace(setup_family=SimpleNamespace(value="BREAKOUT")),
        )


class FakeAlertService:
    def __init__(self, *, created: int = 0, suppressed: int = 0) -> None:
        self.created = created
        self.suppressed = suppressed
        self.seeded = 0

    def seed_builtin_rules(self, _db):
        self.seeded += 1
        return ()

    def evaluate_episode_result(self, *_args, **_kwargs):
        return SimpleNamespace(created=self.created, suppressed=self.suppressed)

    def evaluate_lifecycle_event(self, *_args, **_kwargs):
        return SimpleNamespace(created=self.created, suppressed=self.suppressed)

    def evaluate_signal_change_events(self, _db, events):
        return SimpleNamespace(
            created=self.created if events else 0,
            suppressed=self.suppressed if events else 0,
        )


def _episode() -> SetupLifecycleEpisode:
    return SetupLifecycleEpisode(
        id=1,
        ticker="MSFT",
        timeframe="1d",
        setup_family="BREAKOUT",
        status="ACTIVE",
        opened_on=date(2026, 8, 1),
        current_as_of_date=date(2026, 8, 1),
        last_observed_on=date(2026, 8, 1),
        missing_observation_sessions=0,
        current_state="READY",
        current_phase="PIVOT_READY",
        state_entered_on=date(2026, 8, 1),
        state_age_sessions=0,
        current_actionability="WATCH_ONLY",
        confidence_score=80,
        confidence_label="NORMAL",
        engine_version="slse-1.0.0",
        config_version="v1",
        config_hash="hash",
    )
