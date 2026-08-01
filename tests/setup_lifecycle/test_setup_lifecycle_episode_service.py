from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from app.models.tables import SetupLifecycleEpisode, SetupLifecycleEvent, SetupSignalSnapshot
from app.services.setup_lifecycle.enums import LifecycleState, SetupFamily
from app.services.setup_lifecycle.episode_service import SetupLifecycleEpisodeService
from app.services.setup_lifecycle.repository import SetupLifecycleRepository


def test_first_observed_ready_opens_episode_without_manufacturing_prior_events() -> None:
    repository = FakeEpisodeRepository()
    service = SetupLifecycleEpisodeService(repository=repository)

    result = service.apply_snapshot(
        db=object(),
        snapshot=_snapshot(
            101,
            setup_score=Decimal("7.8"),
            classification="Breakout Base",
            distance_to_pivot_pct=Decimal("1.0"),
        ),
        evaluation_run_id=7,
    )

    assert result.opened is True
    assert result.episode is not None
    assert result.episode.current_state == LifecycleState.READY.value
    assert len(repository.events) == 1
    assert repository.events[0].event_type == "EPISODE_OPENED"
    assert "SKIPPED_PRIOR_PROGRESSION" in repository.events[0].reason_codes_json


def test_no_state_or_phase_change_updates_episode_without_transition_event() -> None:
    repository = FakeEpisodeRepository()
    active = _episode(
        episode_id=1,
        state=LifecycleState.READY,
        phase="PIVOT_READY",
        state_age_sessions=2,
    )
    repository.active[("MSFT", "1d", SetupFamily.BREAKOUT.value)] = active
    service = SetupLifecycleEpisodeService(repository=repository)

    result = service.apply_snapshot(
        db=object(),
        snapshot=_snapshot(
            102,
            setup_score=Decimal("7.8"),
            classification="Breakout Base",
            distance_to_pivot_pct=Decimal("1.0"),
            data_as_of_date=date(2026, 8, 4),
        ),
        evaluation_run_id=8,
    )

    assert result.lifecycle_event is None
    assert active.current_snapshot_id == 102
    assert active.state_age_sessions == 3
    assert active.missing_observation_sessions == 0
    assert repository.events == []


def test_phase_change_in_same_state_creates_event_and_retains_state_age() -> None:
    repository = FakeEpisodeRepository()
    active = _episode(
        episode_id=1,
        state=LifecycleState.TIGHTENING,
        phase="RANGE_CONTRACTION",
        state_age_sessions=1,
    )
    repository.active[("MSFT", "1d", SetupFamily.BREAKOUT.value)] = active
    service = SetupLifecycleEpisodeService(repository=repository)

    result = service.apply_snapshot(
        db=object(),
        snapshot=_snapshot(
            103,
            setup_score=Decimal("6.8"),
            classification="Breakout Base",
            volume_dry_up=True,
            data_as_of_date=date(2026, 8, 4),
        ),
        evaluation_run_id=8,
    )

    assert result.lifecycle_event is not None
    assert result.lifecycle_event.event_type == "PHASE_TRANSITION"
    assert active.current_state == LifecycleState.TIGHTENING.value
    assert active.current_phase == "VOLUME_DRY_UP"
    assert active.state_age_sessions == 2


def test_state_transition_creates_event_and_resets_state_age() -> None:
    repository = FakeEpisodeRepository()
    active = _episode(
        episode_id=1,
        state=LifecycleState.TIGHTENING,
        phase="RANGE_CONTRACTION",
        state_age_sessions=5,
    )
    repository.active[("MSFT", "1d", SetupFamily.BREAKOUT.value)] = active
    service = SetupLifecycleEpisodeService(repository=repository)

    result = service.apply_snapshot(
        db=object(),
        snapshot=_snapshot(
            104,
            setup_score=Decimal("7.8"),
            classification="Breakout Base",
            distance_to_pivot_pct=Decimal("1.0"),
            data_as_of_date=date(2026, 8, 4),
        ),
        evaluation_run_id=8,
    )

    assert result.lifecycle_event is not None
    assert result.lifecycle_event.event_type == "STATE_TRANSITION"
    assert active.current_state == LifecycleState.READY.value
    assert active.state_age_sessions == 0
    assert active.state_entered_on == date(2026, 8, 4)


def test_failed_state_closes_episode_and_preserves_terminal_reason() -> None:
    repository = FakeEpisodeRepository()
    active = _episode(
        episode_id=1,
        state=LifecycleState.TRIGGERED,
        phase="BREAKOUT",
        state_age_sessions=1,
    )
    repository.active[("MSFT", "1d", SetupFamily.BREAKOUT.value)] = active
    service = SetupLifecycleEpisodeService(repository=repository)

    result = service.apply_snapshot(
        db=object(),
        snapshot=_snapshot(
            105,
            setup_score=Decimal("8.0"),
            classification="Breakout Base",
            failed_breakout=True,
            data_as_of_date=date(2026, 8, 4),
        ),
        evaluation_run_id=8,
    )

    assert result.closed is True
    assert active.status == "CLOSED"
    assert active.terminal_state == LifecycleState.FAILED.value
    assert active.terminal_reason_code == "HARD_FAILURE"


def test_observation_gap_expires_once_after_family_threshold_without_failure() -> None:
    repository = FakeEpisodeRepository()
    active = _episode(
        episode_id=1,
        state=LifecycleState.DEVELOPING,
        phase="BASE_FORMING",
        state_age_sessions=2,
        last_observed_on=date(2026, 8, 3),
    )
    repository.active[("MSFT", "1d", SetupFamily.BREAKOUT.value)] = active
    service = SetupLifecycleEpisodeService(repository=repository)

    expired = service.apply_observation_gap(
        db=object(),
        ticker="MSFT",
        timeframe="1d",
        setup_family=SetupFamily.BREAKOUT,
        observed_on=date(2026, 8, 7),
        evaluation_run_id=9,
    )
    repeated = service.apply_observation_gap(
        db=object(),
        ticker="MSFT",
        timeframe="1d",
        setup_family=SetupFamily.BREAKOUT,
        observed_on=date(2026, 8, 10),
        evaluation_run_id=10,
    )

    assert expired.closed is True
    assert active.status == "CLOSED"
    assert active.terminal_state == LifecycleState.EXPIRED.value
    assert active.terminal_reason_code == "OBSERVATION_GAP"
    assert repository.events[0].to_state == LifecycleState.EXPIRED.value
    assert repository.events[0].to_state != LifecycleState.FAILED.value
    assert repeated.episode_id is None
    assert len(repository.events) == 1


def test_rearm_cooldown_blocks_duplicate_episode_until_fresh_ready_evidence() -> None:
    repository = FakeEpisodeRepository()
    repository.closed.append(
        _episode(
            episode_id=1,
            state=LifecycleState.FAILED,
            phase="BREAKOUT_FAILED",
            state_age_sessions=0,
            status="CLOSED",
            closed_on=date(2026, 8, 1),
        )
    )
    service = SetupLifecycleEpisodeService(repository=repository)

    blocked = service.apply_snapshot(
        db=object(),
        snapshot=_snapshot(
            106,
            setup_score=Decimal("6.0"),
            classification="Breakout Base",
            data_as_of_date=date(2026, 8, 3),
        ),
    )
    fresh = service.apply_snapshot(
        db=object(),
        snapshot=_snapshot(
            107,
            setup_score=Decimal("7.8"),
            classification="Breakout Base",
            distance_to_pivot_pct=Decimal("1.0"),
            data_as_of_date=date(2026, 8, 3),
        ),
    )

    assert blocked.episode is None
    assert blocked.warning_codes == ("REARM_COOLDOWN_ACTIVE",)
    assert fresh.opened is True


class FakeEpisodeRepository:
    normalize_ticker = staticmethod(SetupLifecycleRepository.normalize_ticker)
    stable_key = staticmethod(SetupLifecycleRepository.stable_key)

    def __init__(self) -> None:
        self.active: dict[tuple[str, str, str], SetupLifecycleEpisode] = {}
        self.closed: list[SetupLifecycleEpisode] = []
        self.events: list[SetupLifecycleEvent] = []
        self.next_episode_id = 100
        self.next_event_id = 1000

    def active_episode_for_update(self, _db, *, ticker, timeframe, setup_family, lock=True):
        episode = self.active.get((self.normalize_ticker(ticker), timeframe, setup_family))
        if episode is not None and episode.status == "ACTIVE":
            return episode
        return None

    def latest_closed_episode(self, _db, *, ticker, timeframe, setup_family):
        matches = [
            episode
            for episode in self.closed
            if episode.ticker == self.normalize_ticker(ticker)
            and episode.timeframe == timeframe
            and episode.setup_family == setup_family
        ]
        return matches[-1] if matches else None

    def active_episodes_for_ticker(self, _db, *, ticker, timeframe):
        return [
            episode
            for episode in self.active.values()
            if episode.ticker == self.normalize_ticker(ticker)
            and episode.timeframe == timeframe
            and episode.status == "ACTIVE"
        ]

    def add(self, _db, row):
        if isinstance(row, SetupLifecycleEpisode):
            row.id = self.next_episode_id
            self.next_episode_id += 1
            self.active[(row.ticker, row.timeframe, row.setup_family)] = row
        return row

    def add_lifecycle_event(self, _db, event):
        event.id = self.next_event_id
        self.next_event_id += 1
        self.events.append(event)
        return event

    def supersede_prior_current_events(self, _db, event):
        for prior in self.events:
            if prior is not event and prior.event_type == event.event_type:
                prior.is_current_version = False
                prior.superseded_by_event_id = event.id


def _snapshot(
    snapshot_id: int,
    *,
    setup_score: Decimal,
    classification: str,
    data_as_of_date: date = date(2026, 8, 1),
    distance_to_pivot_pct: Decimal | None = None,
    close_trigger_cross: bool = False,
    failed_breakout: bool = False,
    volume_dry_up: bool = False,
) -> SetupSignalSnapshot:
    return SetupSignalSnapshot(
        id=snapshot_id,
        run_id=7,
        ticker="MSFT",
        timeframe="1d",
        data_as_of_date=data_as_of_date,
        calculated_at=datetime(2026, 8, 1, 21, tzinfo=UTC),
        origin_type="LIVE_RUN",
        engine_version="slse-1.0.0",
        config_version="v1",
        config_hash="hash",
        source_data_hash=f"source-{snapshot_id}",
        schema_version="snapshot-v1",
        data_quality_label="HIGH",
        setup_score=setup_score,
        trend_score=setup_score,
        technical_classification=classification,
        distance_to_pivot_pct=distance_to_pivot_pct,
        close_above_trigger=close_trigger_cross,
        signals_json={
            "setup_score": {"value": float(setup_score)},
            "technical_score": {"value": float(setup_score)},
            "classification": {"value": classification},
            "distance_to_pivot_pct": {
                "value": float(distance_to_pivot_pct)
                if distance_to_pivot_pct is not None
                else None
            },
            "close_trigger_cross": {"value": close_trigger_cross},
            "failed_breakout": {"value": failed_breakout},
            "volume_dry_up": {"value": volume_dry_up},
        },
        warning_flags_json=[],
        source_lineage_json={
            "market_regime_as_of": "2026-08-01",
            "sector_rotation_as_of": "2026-08-01",
        },
    )


def _episode(
    *,
    episode_id: int,
    state: LifecycleState,
    phase: str,
    state_age_sessions: int,
    last_observed_on: date = date(2026, 8, 1),
    status: str = "ACTIVE",
    closed_on: date | None = None,
) -> SetupLifecycleEpisode:
    return SetupLifecycleEpisode(
        id=episode_id,
        ticker="MSFT",
        timeframe="1d",
        setup_family=SetupFamily.BREAKOUT.value,
        status=status,
        opened_on=date(2026, 8, 1),
        current_as_of_date=last_observed_on,
        last_observed_on=last_observed_on,
        closed_on=closed_on,
        missing_observation_sessions=0,
        current_state=state.value,
        current_phase=phase,
        state_entered_on=date(2026, 8, 1),
        state_age_sessions=state_age_sessions,
        current_actionability="WATCH_ONLY",
        confidence_score=80,
        confidence_label="NORMAL",
        engine_version="slse-1.0.0",
        config_version="v1",
        config_hash="hash",
        metadata_json={"setup_score": 7.8},
    )
