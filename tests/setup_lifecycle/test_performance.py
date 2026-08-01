from __future__ import annotations

import inspect
from datetime import date
from time import perf_counter

from app.models.tables import (
    SetupLifecycleEpisode,
    SetupLifecycleEvaluationRun,
    SetupLifecycleEvent,
    SetupSignalSnapshot,
    SignalAlertEvent,
    SignalChangeEvent,
)
from app.routers import setup_lifecycle_routes
from app.services.setup_lifecycle.config import load_setup_lifecycle_config
from app.services.setup_lifecycle.constants import (
    SLSE_API_P95_TARGET_MS,
    SLSE_CAPTURE_EVALUATION_TARGET_SECONDS,
    SLSE_PERFORMANCE_FIXTURE_MIN_SNAPSHOTS,
)
from app.services.setup_lifecycle.query_service import SetupLifecycleFilters
from app.services.setup_lifecycle.repository import SetupLifecycleRepository
from app.settings import Settings


def test_phase_12_performance_targets_match_config_constants_and_settings() -> None:
    config = load_setup_lifecycle_config()
    settings = Settings(_env_file=None)

    assert config.api.capture_evaluation_target_seconds == (
        SLSE_CAPTURE_EVALUATION_TARGET_SECONDS
    )
    assert config.api.p95_target_ms == SLSE_API_P95_TARGET_MS
    assert config.api.performance_fixture_min_snapshots == (
        SLSE_PERFORMANCE_FIXTURE_MIN_SNAPSHOTS
    )
    assert settings.setup_lifecycle_capture_evaluation_target_seconds == 60
    assert settings.setup_lifecycle_api_p95_target_ms == 500
    assert settings.setup_lifecycle_retain_indefinitely is True
    assert settings.setup_lifecycle_purge_enabled is False


def test_phase_12_dashboard_and_canonical_queries_have_index_contracts() -> None:
    assert _index_columns(SetupSignalSnapshot, "uq_setup_signal_snapshots_canonical_day") == (
        "ticker",
        "timeframe",
        "data_as_of_date",
    )
    assert _index_columns(SetupSignalSnapshot, "idx_setup_signal_snapshots_ticker_as_of") == (
        "ticker",
        "data_as_of_date",
    )
    assert _index_columns(SetupLifecycleEvent, "idx_setup_lifecycle_events_ticker_date") == (
        "ticker",
        "effective_date",
    )
    assert _index_columns(SetupLifecycleEpisode, "idx_setup_lifecycle_episodes_family_state") == (
        "setup_family",
        "current_state",
    )
    assert _index_columns(SignalChangeEvent, "idx_signal_change_events_ticker_date") == (
        "ticker",
        "effective_date",
    )
    assert _index_columns(SignalAlertEvent, "idx_signal_alert_events_status_severity") == (
        "status",
        "severity",
    )
    assert _index_columns(
        SetupLifecycleEvaluationRun,
        "idx_setup_lifecycle_eval_runs_date_range",
    ) == ("date_from", "date_to")


def test_phase_12_filter_contract_uses_promoted_fields_not_json_scan_only() -> None:
    filter_fields = set(SetupLifecycleFilters.__dataclass_fields__)

    assert {
        "ticker",
        "sector",
        "setup_family",
        "lifecycle_state",
        "transition",
        "actionability",
        "confidence_min",
        "confidence_max",
        "state_age_min",
        "state_age_max",
        "setup_score_min",
        "setup_score_max",
        "trigger_distance_min",
        "trigger_distance_max",
        "sector_rank_min",
        "sector_rank_max",
        "velocity_min",
        "velocity_max",
        "market_regime",
        "warning_flag",
    } <= filter_fields


def test_phase_12_list_routes_keep_cursor_pagination_and_limits() -> None:
    changes = inspect.signature(setup_lifecycle_routes.setup_lifecycle_changes)
    alerts = inspect.signature(setup_lifecycle_routes.setup_lifecycle_alerts)

    assert changes.parameters["limit"].default == 50
    assert changes.parameters["cursor"].default is None
    assert alerts.parameters["limit"].default == 50
    assert alerts.parameters["cursor"].default is None


def test_phase_12_thousand_ticker_batch_identity_generation_is_deterministic() -> None:
    repository = SetupLifecycleRepository()
    tickers = [f"T{i:04d}" for i in range(1_000)]

    start = perf_counter()
    keys = [
        repository.lifecycle_event_key(
            ticker=ticker,
            timeframe="1d",
            setup_family="BREAKOUT",
            effective_date=date(2026, 8, 1),
            from_state="READY",
            to_state="TRIGGERED",
            engine_version="slse-1.0.0",
            config_hash="hash",
        )
        for ticker in tickers
    ]
    elapsed = perf_counter() - start

    assert len(set(keys)) == 1_000
    assert keys == [
        repository.lifecycle_event_key(
            ticker=ticker.lower(),
            timeframe="1d",
            setup_family="BREAKOUT",
            effective_date=date(2026, 8, 1),
            from_state="READY",
            to_state="TRIGGERED",
            engine_version="slse-1.0.0",
            config_hash="hash",
        )
        for ticker in tickers
    ]
    assert elapsed < 1.0


def _index_columns(model, name: str) -> tuple[str, ...]:
    for index in model.__table__.indexes:
        if index.name == name:
            return tuple(column.name for column in index.columns)
    raise AssertionError(f"{name} index not found on {model.__tablename__}")
