from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import Select

from app.models.tables import (
    SetupLifecycleEpisode,
    SetupLifecycleEvaluationRun,
    SetupLifecycleEvent,
    SetupSignalSnapshot,
    SignalAlertEvent,
    SignalChangeEvent,
)
from app.services.setup_lifecycle.repository import (
    PurgeScope,
    SetupLifecycleRepository,
)


def test_repository_snapshot_identity_key_normalizes_ticker() -> None:
    key = SetupLifecycleRepository.snapshot_identity_key(
        run_id=7,
        ticker=" msft ",
        timeframe="1d",
        data_as_of_date=date(2026, 8, 1),
        engine_version="slse-1.0.0",
        config_hash="config-hash",
        source_data_hash="source-hash",
    )

    assert key == (
        7,
        "MSFT",
        "1d",
        "2026-08-01",
        "slse-1.0.0",
        "config-hash",
        "source-hash",
    )


def test_repository_snapshot_identity_key_distinguishes_revised_source_data() -> None:
    original = SetupLifecycleRepository.snapshot_identity_key(
        run_id=7,
        ticker="MSFT",
        timeframe="1d",
        data_as_of_date=date(2026, 8, 1),
        engine_version="slse-1.0.0",
        config_hash="config-hash",
        source_data_hash="source-a",
    )
    revised = SetupLifecycleRepository.snapshot_identity_key(
        run_id=7,
        ticker="MSFT",
        timeframe="1d",
        data_as_of_date=date(2026, 8, 1),
        engine_version="slse-1.0.0",
        config_hash="config-hash",
        source_data_hash="source-b",
    )

    assert original != revised


def test_repository_event_keys_are_stable_and_distinct_by_payload() -> None:
    first = SetupLifecycleRepository.lifecycle_event_key(
        ticker="MSFT",
        timeframe="1d",
        setup_family="BREAKOUT",
        effective_date=date(2026, 8, 1),
        from_state="READY",
        to_state="TRIGGERED",
        engine_version="slse-1.0.0",
        config_hash="config-hash",
    )
    repeated = SetupLifecycleRepository.lifecycle_event_key(
        ticker=" msft ",
        timeframe="1d",
        setup_family="BREAKOUT",
        effective_date=date(2026, 8, 1),
        from_state="READY",
        to_state="TRIGGERED",
        engine_version="slse-1.0.0",
        config_hash="config-hash",
    )
    changed = SetupLifecycleRepository.lifecycle_event_key(
        ticker="MSFT",
        timeframe="1d",
        setup_family="BREAKOUT",
        effective_date=date(2026, 8, 1),
        from_state="READY",
        to_state="CONFIRMED",
        engine_version="slse-1.0.0",
        config_hash="config-hash",
    )

    assert first == repeated
    assert first != changed


def test_repository_signal_change_key_handles_json_values_deterministically() -> None:
    first = SetupLifecycleRepository.signal_change_key(
        ticker="MSFT",
        timeframe="1d",
        signal_key="technical_score",
        effective_date=date(2026, 8, 1),
        old_value={"score": 80, "flags": ["ready", "close_cross"]},
        new_value={"flags": ["ready", "close_cross"], "score": 90},
        config_hash="config-hash",
    )
    repeated = SetupLifecycleRepository.signal_change_key(
        ticker=" msft ",
        timeframe="1d",
        signal_key="technical_score",
        effective_date=date(2026, 8, 1),
        old_value={"flags": ["ready", "close_cross"], "score": 80},
        new_value={"score": 90, "flags": ["ready", "close_cross"]},
        config_hash="config-hash",
    )

    assert first == repeated


def test_repository_alert_event_key_includes_rule_source_and_ticker() -> None:
    first = SetupLifecycleRepository.alert_event_key(
        rule_id="became_actionable",
        source_event_key="event-key",
        ticker="MSFT",
    )
    second = SetupLifecycleRepository.alert_event_key(
        rule_id="risk_downgrade",
        source_event_key="event-key",
        ticker="MSFT",
    )

    assert first != second
    assert len(first) == 64


def test_repository_applies_evaluation_counts_to_explicit_columns() -> None:
    evaluation_run = SetupLifecycleEvaluationRun(
        mode="LIVE",
        status="RUNNING",
        engine_version="slse-1.0.0",
        config_version="v1",
        config_hash="hash",
    )
    repository = SetupLifecycleRepository()

    repository.apply_evaluation_counts(
        evaluation_run,
        {
            "read": 10,
            "captured": 9,
            "canonical": 8,
            "changed": 7,
            "transitioned": 6,
            "alerted": 5,
            "skipped": 4,
            "warning": 3,
            "failed": 2,
        },
    )

    assert evaluation_run.read_count == 10
    assert evaluation_run.captured_count == 9
    assert evaluation_run.canonical_count == 8
    assert evaluation_run.changed_count == 7
    assert evaluation_run.transitioned_count == 6
    assert evaluation_run.alerted_count == 5
    assert evaluation_run.skipped_count == 4
    assert evaluation_run.warning_count == 3
    assert evaluation_run.failed_count == 2
    assert evaluation_run.counts_json["read"] == 10


def test_purge_preview_is_stable_for_the_same_scope_and_counts() -> None:
    repository = CountingRepository()
    scope = PurgeScope(before_date=date(2026, 8, 1), ticker="msft", evaluation_run_id=11)

    first = repository.preview_purge(db=object(), scope=scope)  # type: ignore[arg-type]
    repeated = repository.preview_purge(db=object(), scope=scope)  # type: ignore[arg-type]

    assert first == repeated
    assert first.counts == {
        "alert_events": 2,
        "signal_change_events": 3,
        "lifecycle_events": 5,
        "episodes": 7,
        "snapshots": 11,
        "evaluation_runs": 13,
    }


class CountingRepository(SetupLifecycleRepository):
    COUNTS_BY_ENTITY = {
        SignalAlertEvent: 2,
        SignalChangeEvent: 3,
        SetupLifecycleEvent: 5,
        SetupLifecycleEpisode: 7,
        SetupSignalSnapshot: 11,
        SetupLifecycleEvaluationRun: 13,
    }

    @staticmethod
    def _count(db: Any, statement: Select[tuple[Any]]) -> int:
        entity = statement.column_descriptions[0]["entity"]
        return CountingRepository.COUNTS_BY_ENTITY[entity]
