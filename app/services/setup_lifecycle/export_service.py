from __future__ import annotations

import json
from typing import Any

from sqlalchemy import or_, select

from app.models.tables import SetupLifecycleEvent, SignalAlertEvent
from app.services.csv_export import write_csv

CHANGE_COLUMNS = (
    "id",
    "source_type",
    "lifecycle_event_id",
    "signal_change_event_id",
    "episode_id",
    "evaluation_run_id",
    "ticker",
    "company",
    "sector",
    "data_as_of_date",
    "comparison_date",
    "setup_family",
    "phase",
    "event_type",
    "signal_key",
    "previous_state",
    "current_state",
    "transition",
    "state_age_sessions",
    "actionability",
    "confidence",
    "confidence_label",
    "technical_score",
    "technical_score_previous",
    "technical_score_delta",
    "score_velocity_1d",
    "score_velocity_3d",
    "score_velocity_5d",
    "score_velocity_10d",
    "setup_score_velocity_1d",
    "setup_score_velocity_3d",
    "setup_score_velocity_5d",
    "setup_score_velocity_10d",
    "trigger_distance_pct",
    "trigger_reference_type",
    "trigger_reference_price",
    "trigger_reference_source",
    "trigger_reference_source_id",
    "trigger_reference_session",
    "trigger_distance_missing_reason",
    "sector_rank",
    "sector_rank_previous",
    "sector_rank_delta",
    "market_regime",
    "market_gate",
    "earnings_risk",
    "liquidity_risk",
    "required_feature_coverage",
    "freshness",
    "data_quality_label",
    "blockers",
    "latest_reason",
    "reason_codes",
    "warning_flags",
    "warning_count",
    "severity",
    "snapshot_id",
    "previous_snapshot_id",
    "source_run_id",
    "origin_type",
    "is_canonical",
    "record_status",
    "superseded_by_snapshot_id",
    "engine_version",
    "config_version",
    "schema_version",
    "config_hash",
    "source_data_hash",
    "source_event_key",
    "source_url",
    "timeline_url",
)

EPISODE_COLUMNS = (
    "id",
    "ticker",
    "setup_family",
    "status",
    "current_state",
    "current_phase",
    "current_actionability",
    "confidence_score",
    "confidence_label",
    "opening_snapshot_id",
    "current_snapshot_id",
    "closing_snapshot_id",
    "opening_evaluation_id",
    "closing_evaluation_id",
    "engine_version",
    "config_version",
    "config_hash",
    "record_status",
    "opened_on",
    "closed_on",
)

ALERT_COLUMNS = (
    "id",
    "ticker",
    "effective_date",
    "alert_type",
    "severity",
    "review_status",
    "source_type",
    "lifecycle_state",
    "actionability",
    "confidence",
    "confidence_label",
    "reason_codes",
    "blockers",
    "episode_id",
    "lifecycle_event_id",
    "signal_change_event_id",
    "evaluation_run_id",
    "source_event_key",
    "snapshot_id",
    "previous_snapshot_id",
    "origin_type",
    "is_canonical",
    "record_status",
    "engine_version",
    "config_version",
    "schema_version",
    "config_hash",
    "source_data_hash",
    "source_url",
)
SETUP_LIFECYCLE_SCHEMA_IDS = {
    CHANGE_COLUMNS: "swinglens.setup-lifecycle.changes.v3",
    EPISODE_COLUMNS: "swinglens.setup-lifecycle.episodes.v2",
    ALERT_COLUMNS: "swinglens.setup-lifecycle.alerts.v3",
}


def export_changes_csv(payload: dict[str, Any]) -> str:
    return _csv(payload.get("items", []), CHANGE_COLUMNS)


def export_episodes_csv(payload: dict[str, Any]) -> str:
    return _csv(payload.get("items", []), EPISODE_COLUMNS)


def export_alerts_csv(payload: dict[str, Any]) -> str:
    return _csv(payload.get("items", []), ALERT_COLUMNS)


def export_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, default=str)


def setup_lifecycle_point_in_time_features(
    db,
    *,
    ticker: str,
    as_of_date,
    timeframe: str = "1d",
) -> dict[str, Any]:
    event = db.scalar(
        select(SetupLifecycleEvent)
        .where(SetupLifecycleEvent.ticker == ticker.strip().upper())
        .where(SetupLifecycleEvent.timeframe == timeframe)
        .where(SetupLifecycleEvent.effective_date <= as_of_date)
        .where(SetupLifecycleEvent.is_current_version.is_(True))
        .where(
            or_(
                SetupLifecycleEvent.evidence_json["origin_type"].as_string().is_(None),
                SetupLifecycleEvent.evidence_json["origin_type"].as_string() != "RECONSTRUCTED",
            )
        )
        .order_by(SetupLifecycleEvent.effective_date.desc(), SetupLifecycleEvent.id.desc())
        .limit(1)
    )
    if event is None:
        return {
            "setup_lifecycle_available": False,
            "setup_lifecycle_as_of_date": None,
            "setup_lifecycle_episode_id": None,
        }
    alerts = list(
        db.scalars(
            select(SignalAlertEvent)
            .where(SignalAlertEvent.ticker == event.ticker)
            .where(SignalAlertEvent.timeframe == event.timeframe)
            .where(SignalAlertEvent.effective_date <= as_of_date)
            .where(SignalAlertEvent.lifecycle_event_id == event.id)
            .order_by(SignalAlertEvent.effective_date.desc(), SignalAlertEvent.id.desc())
        )
    )
    evidence = event.evidence_json or {}
    return {
        "setup_lifecycle_available": True,
        "setup_lifecycle_as_of_date": event.effective_date.isoformat(),
        "setup_lifecycle_state": event.to_state,
        "setup_lifecycle_phase": event.to_phase,
        "setup_lifecycle_transition_type": event.event_type,
        "setup_lifecycle_state_age": event.state_age_before,
        "setup_lifecycle_signal_velocity": evidence.get("velocity"),
        "setup_lifecycle_actionability": event.actionability_after,
        "setup_lifecycle_confidence": event.confidence_score,
        "setup_lifecycle_confidence_label": event.confidence_label,
        "setup_lifecycle_episode_id": event.episode_id,
        "setup_lifecycle_event_id": event.id,
        "setup_lifecycle_source_event_key": event.source_event_key,
        "setup_lifecycle_source_links": {
            "episode": f"/setup-lifecycle/episodes/{event.episode_id}"
            if event.episode_id
            else None,
            "event_api": f"/api/setup-lifecycle/episodes/{event.episode_id}"
            if event.episode_id
            else None,
        },
        "setup_lifecycle_alert_count": len(alerts),
        "setup_lifecycle_alert_ids": [alert.id for alert in alerts],
    }


def _csv(rows: list[dict[str, Any]], columns: tuple[str, ...]) -> str:
    return write_csv(
        columns,
        ({column: _cell(row.get(column)) for column in columns} for row in rows),
        schema_id=SETUP_LIFECYCLE_SCHEMA_IDS[columns],
        metadata={"guidance_type": "research_context", "execution_instruction": False},
    )


def _cell(value: Any) -> Any:
    if isinstance(value, list | dict):
        return json.dumps(value, sort_keys=True, default=str)
    return value
