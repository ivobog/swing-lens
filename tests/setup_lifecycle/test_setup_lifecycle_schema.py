from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy.dialects.postgresql import JSONB

from app.db import Base
from app.models.tables import (
    SetupLifecycleEpisode,
    SetupLifecycleEvaluationRun,
    SetupLifecycleEvent,
    SetupSignalSnapshot,
    SignalAlertEvent,
    SignalAlertRule,
    SignalChangeEvent,
)

SETUP_LIFECYCLE_TABLES = {
    "setup_lifecycle_evaluation_runs",
    "setup_signal_snapshots",
    "setup_lifecycle_episodes",
    "setup_lifecycle_events",
    "signal_change_events",
    "signal_alert_rules",
    "signal_alert_events",
    "setup_lifecycle_administrative_audit_events",
}


def test_setup_lifecycle_metadata_includes_all_phase_2_tables() -> None:
    assert SETUP_LIFECYCLE_TABLES.issubset(Base.metadata.tables)


def test_evaluation_run_tracks_phase_counts_scope_and_audit_payloads() -> None:
    table = SetupLifecycleEvaluationRun.__table__

    for column_name in [
        "mode",
        "status",
        "current_phase",
        "engine_version",
        "config_version",
        "config_hash",
        "date_from",
        "date_to",
        "ticker_scope_json",
        "requested_config_json",
        "dry_run",
        "requester",
        "source_snapshot_min_id",
        "source_snapshot_max_id",
        "output_evaluation_version",
        "read_count",
        "captured_count",
        "canonical_count",
        "changed_count",
        "transitioned_count",
        "alerted_count",
        "skipped_count",
        "warning_count",
        "failed_count",
        "counts_json",
        "error_summary_json",
        "heartbeat_at",
        "last_heartbeat_at",
        "duration_ms",
        "audit_json",
    ]:
        assert column_name in table.c

    assert isinstance(table.c.requested_config_json.type, JSONB)
    assert isinstance(table.c.counts_json.type, JSONB)


def test_signal_snapshot_preserves_source_links_and_canonical_evidence() -> None:
    table = SetupSignalSnapshot.__table__

    for column_name in [
        "evaluation_run_id",
        "run_id",
        "source_run_id_text",
        "raw_row_id",
        "fundamental_score_id",
        "technical_score_id",
        "combined_result_id",
        "ranking_result_id",
        "market_regime_snapshot_id",
        "sector_rotation_snapshot_id",
        "ticker",
        "timeframe",
        "data_as_of_date",
        "origin_type",
        "source_data_hash",
        "schema_version",
        "is_canonical",
        "canonical_reason",
        "superseded_by_snapshot_id",
        "primary_setup_family",
        "primary_phase",
        "data_quality_label",
        "close_above_trigger",
        "high_above_trigger",
        "diagnostic_high_cross_json",
        "canonical_decision_json",
        "signals_json",
        "source_lineage_json",
    ]:
        assert column_name in table.c

    assert table.c.source_data_hash.nullable is False
    assert isinstance(table.c.signals_json.type, JSONB)
    assert isinstance(table.c.canonical_decision_json.type, JSONB)


def test_setup_lifecycle_unique_constraints_and_partial_indexes_are_defined() -> None:
    snapshot_constraints = {
        constraint.name for constraint in SetupSignalSnapshot.__table__.constraints
    }
    snapshot_indexes = {index.name: index for index in SetupSignalSnapshot.__table__.indexes}
    episode_indexes = {index.name: index for index in SetupLifecycleEpisode.__table__.indexes}
    lifecycle_constraints = {
        constraint.name for constraint in SetupLifecycleEvent.__table__.constraints
    }
    signal_constraints = {constraint.name for constraint in SignalChangeEvent.__table__.constraints}
    alert_rule_constraints = {
        constraint.name for constraint in SignalAlertRule.__table__.constraints
    }
    alert_event_constraints = {
        constraint.name for constraint in SignalAlertEvent.__table__.constraints
    }

    assert "uq_setup_signal_snapshots_run_identity" in snapshot_constraints
    assert "uq_setup_signal_snapshots_canonical_day" in snapshot_indexes
    assert snapshot_indexes["uq_setup_signal_snapshots_canonical_day"].unique
    assert (
        snapshot_indexes["uq_setup_signal_snapshots_canonical_day"]
        .dialect_options["postgresql"]["where"]
        is not None
    )
    assert "uq_setup_lifecycle_episodes_active_family" in episode_indexes
    assert episode_indexes["uq_setup_lifecycle_episodes_active_family"].unique
    assert (
        episode_indexes["uq_setup_lifecycle_episodes_active_family"]
        .dialect_options["postgresql"]["where"]
        is not None
    )
    assert "uq_setup_lifecycle_events_eval_source_key" in lifecycle_constraints
    assert "uq_signal_change_events_source_key" in signal_constraints
    assert "uq_signal_alert_rules_rule_id" in alert_rule_constraints
    assert "uq_signal_alert_events_event_key" in alert_event_constraints


def test_setup_lifecycle_models_accept_representative_values() -> None:
    evaluation = SetupLifecycleEvaluationRun(
        mode="LIVE",
        status="RUNNING",
        engine_version="slse-1.0.0",
        config_version="v1",
        config_hash="hash",
        ticker_scope_json=["MSFT"],
        requested_config_json={"families": ["BREAKOUT"]},
    )
    snapshot = SetupSignalSnapshot(
        ticker="MSFT",
        timeframe="1d",
        data_as_of_date=date(2026, 8, 1),
        calculated_at=datetime(2026, 8, 1, 21, 0, tzinfo=UTC),
        origin_type="LIVE_RUN",
        engine_version="slse-1.0.0",
        config_version="v1",
        config_hash="hash",
        source_data_hash="source-hash",
        schema_version="snapshot-v1",
        data_quality_label="NORMAL",
        close_above_trigger=True,
        high_above_trigger=True,
        diagnostic_high_cross_json={"diagnostic_only": True},
        canonical_decision_json={"authority": "COMPLETED_DAILY_CLOSE"},
        signals_json={"technical_score": 88},
    )
    episode = SetupLifecycleEpisode(
        ticker="MSFT",
        timeframe="1d",
        setup_family="BREAKOUT",
        status="ACTIVE",
        opened_on=date(2026, 8, 1),
        current_as_of_date=date(2026, 8, 1),
        last_observed_on=date(2026, 8, 1),
        current_state="READY",
        current_phase="ready",
        state_entered_on=date(2026, 8, 1),
        current_actionability="ACTIONABLE",
        confidence_score=82,
        confidence_label="HIGH",
        engine_version="slse-1.0.0",
        config_version="v1",
        config_hash="hash",
    )

    assert evaluation.ticker_scope_json == ["MSFT"]
    assert snapshot.canonical_decision_json["authority"] == "COMPLETED_DAILY_CLOSE"
    assert episode.status == "ACTIVE"


def test_setup_lifecycle_migration_follows_current_head() -> None:
    migration = Path(
        "alembic/versions/20260801_0017_create_setup_lifecycle_tables.py"
    ).read_text(encoding="utf-8")

    assert 'revision: str = "0017_create_setup_lifecycle_tables"' in migration
    assert 'down_revision: str | None = "0016_add_winner_probability_engine"' in migration
    for table_name in SETUP_LIFECYCLE_TABLES:
        assert table_name in migration
