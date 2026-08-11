from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

from app.models.tables import SetupSignalSnapshot
from app.services.setup_lifecycle.change_detector import SetupLifecycleChangeDetector
from app.services.setup_lifecycle.config import load_setup_lifecycle_config
from app.services.setup_lifecycle.enums import EventSeverity, SignalCategory, SignalValueType
from app.services.setup_lifecycle.repository import SetupLifecycleRepository
from app.services.setup_lifecycle.signal_registry import SignalDefinition, SignalDefinitionRegistry


def test_threshold_entry_and_exit_produce_events_but_repeated_beyond_threshold_does_not() -> None:
    detector = SetupLifecycleChangeDetector(config=load_setup_lifecycle_config())

    entry = detector.detect_changes(
        previous=_snapshot(1, {"setup_score": "7.4"}),
        current=_snapshot(2, {"setup_score": "7.6"}),
    )
    repeated = detector.detect_changes(
        previous=_snapshot(3, {"setup_score": "7.6"}),
        current=_snapshot(4, {"setup_score": "7.8"}),
    )
    exit_change = detector.detect_changes(
        previous=_snapshot(5, {"setup_score": "7.6"}),
        current=_snapshot(6, {"setup_score": "7.4"}),
    )

    assert _only(entry).threshold_name == "crossing_7.5"
    assert _only(entry).threshold_direction == "ENTER"
    assert _only(entry).severity is EventSeverity.NOTABLE
    assert repeated == ()
    assert _only(exit_change).threshold_direction == "EXIT"


def test_detector_handles_numeric_rank_enum_boolean_set_date_and_nullability_changes() -> None:
    detector = SetupLifecycleChangeDetector(config=_config_with_custom_signals())

    changes = detector.detect_changes(
        previous=_snapshot(
            1,
            {
                "pct": "0.10",
                "rank": 10,
                "enum": "OLD",
                "bool": False,
                "set": ["base"],
                "date": "2026-08-01",
                "nullable": None,
            },
        ),
        current=_snapshot(
            2,
            {
                "pct": "0.16",
                "rank": 6,
                "enum": "NEW",
                "bool": True,
                "set": ["base", "breakout"],
                "date": "2026-08-04",
                "nullable": "present",
            },
        ),
    )

    by_key = {change.signal_key: change for change in changes}

    assert by_key["pct"].delta_numeric == Decimal("0.06")
    assert by_key["pct"].percentage_delta == Decimal("0.6")
    assert by_key["rank"].rank_delta == 4
    assert by_key["rank"].normalized_delta == Decimal("4.0")
    assert by_key["rank"].normalized_delta == Decimal("4")
    assert by_key["enum"].reason_codes == ("ENUM_CHANGE",)
    assert by_key["bool"].normalized_delta == Decimal("1")
    assert by_key["set"].new_value == frozenset({"base", "breakout"})
    assert by_key["date"].delta_numeric == Decimal("3")
    assert "MISSING_TO_PRESENT" in by_key["nullable"].reason_codes


def test_missing_to_present_and_present_to_missing_events_are_explicit() -> None:
    detector = SetupLifecycleChangeDetector(config=load_setup_lifecycle_config())

    present = detector.detect_changes(
        previous=_snapshot(1, {"technical_score": None}),
        current=_snapshot(2, {"technical_score": "8.2"}),
    )
    missing = detector.detect_changes(
        previous=_snapshot(3, {"technical_score": "8.2"}),
        current=_snapshot(4, {"technical_score": None}),
    )

    assert "MISSING_TO_PRESENT" in _only(present).reason_codes
    assert "PRESENT_TO_MISSING" in _only(missing).reason_codes


def test_data_quality_stale_to_fresh_and_degraded_events_are_explicit() -> None:
    detector = SetupLifecycleChangeDetector(config=load_setup_lifecycle_config())

    fresh = detector.detect_changes(
        previous=_snapshot(1, {"data_quality": "LOW"}),
        current=_snapshot(2, {"data_quality": "NORMAL"}),
    )
    degraded = detector.detect_changes(
        previous=_snapshot(3, {"data_quality": "NORMAL"}),
        current=_snapshot(4, {"data_quality": "LOW"}),
    )

    assert "STALE_TO_FRESH_DATA_QUALITY" in _only(fresh).reason_codes
    assert "DATA_QUALITY_DEGRADED" in _only(degraded).reason_codes
    assert _only(degraded).severity is EventSeverity.RISK


def test_detect_and_persist_uses_stable_source_event_keys_for_retry_deduplication() -> None:
    previous = _snapshot(1, {"setup_score": "7.4"})
    current = _snapshot(2, {"setup_score": "7.6"})
    repository = FakeChangeRepository(previous=previous, current=current)
    detector = SetupLifecycleChangeDetector(
        repository=repository,
        config=load_setup_lifecycle_config(),
    )

    first = detector.detect_and_persist(
        db=object(),
        evaluation_run_id=99,
        snapshot_ids=(2,),
    )
    second = detector.detect_and_persist(
        db=object(),
        evaluation_run_id=99,
        snapshot_ids=(2,),
    )

    assert first.created_events == 1
    assert second.created_events == 0
    assert len(repository.events_by_key) == 1


class FakeChangeRepository:
    signal_change_key = staticmethod(SetupLifecycleRepository.signal_change_key)

    def __init__(
        self,
        *,
        previous: SetupSignalSnapshot,
        current: SetupSignalSnapshot,
    ) -> None:
        self.previous = previous
        self.current = current
        self.events_by_key = {}

    def get_snapshots_by_ids(self, _db, snapshot_ids):
        return [self.current] if self.current.id in snapshot_ids else []

    def previous_canonical_snapshot(self, _db, **_kwargs):
        return self.previous

    def canonical_snapshot_history(self, _db, **_kwargs):
        return [self.previous]

    def get_signal_change_event(self, _db, source_event_key):
        return self.events_by_key.get(source_event_key)

    def add_signal_change_event(self, _db, event):
        event.id = len(self.events_by_key) + 1
        self.events_by_key[event.source_event_key] = event
        return event


def _config_with_custom_signals():
    definitions = {
        "pct": SignalDefinition(
            key="pct",
            source="pct",
            value_type=SignalValueType.PERCENTAGE,
            category=SignalCategory.SCORE,
            direction="higher_is_better",
            percentage_change=0.5,
        ),
        "rank": SignalDefinition(
            key="rank",
            source="rank",
            value_type=SignalValueType.INTEGER_RANK,
            category=SignalCategory.LEADERSHIP,
            direction="lower_rank_is_better",
            rank_change=3,
        ),
        "enum": SignalDefinition(
            key="enum",
            source="enum",
            value_type=SignalValueType.ENUM,
            category=SignalCategory.SETUP,
            direction="neutral",
            material_on_change=True,
        ),
        "bool": SignalDefinition(
            key="bool",
            source="bool",
            value_type=SignalValueType.BOOLEAN,
            category=SignalCategory.SETUP,
            direction="true_is_better",
            material_on_change=True,
        ),
        "set": SignalDefinition(
            key="set",
            source="set",
            value_type=SignalValueType.SET,
            category=SignalCategory.SETUP,
            direction="neutral",
            material_on_change=True,
        ),
        "date": SignalDefinition(
            key="date",
            source="date",
            value_type=SignalValueType.DATE,
            category=SignalCategory.RISK,
            direction="neutral",
            absolute_change=2,
        ),
        "nullable": SignalDefinition(
            key="nullable",
            source="nullable",
            value_type=SignalValueType.NULLABILITY,
            category=SignalCategory.DATA_QUALITY,
            direction="neutral",
            missing_value_policy="warn",
        ),
    }
    return SimpleNamespace(
        signal_registry=SignalDefinitionRegistry(definitions),
        engine=SimpleNamespace(config_version="test-v1"),
    )


def _snapshot(snapshot_id: int, signals: dict[str, object]) -> SetupSignalSnapshot:
    return SetupSignalSnapshot(
        id=snapshot_id,
        run_id=7,
        ticker="MSFT",
        timeframe="1d",
        data_as_of_date=date(2026, 8, snapshot_id),
        calculated_at=datetime(2026, 8, snapshot_id, 21, tzinfo=UTC),
        origin_type="LIVE_RUN",
        engine_version="slse-1.0.0",
        config_version="v1",
        config_hash="hash",
        source_data_hash=f"source-{snapshot_id}",
        schema_version="snapshot-v1",
        data_quality_label=str(signals.get("data_quality") or "NORMAL"),
        signals_json={
            key: {"value": value}
            for key, value in signals.items()
        },
        is_canonical=True,
    )


def _only(changes):
    assert len(changes) == 1
    return changes[0]
