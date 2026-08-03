from __future__ import annotations

import json
from typing import Any

from sqlalchemy import or_, select

from app.models.tables import SetupLifecycleEvent, SignalAlertEvent
from app.services.csv_export import write_csv

CHANGE_COLUMNS = (
    "id",
    "ticker",
    "effective_date",
    "setup_family",
    "event_type",
    "from_state",
    "to_state",
    "actionability_after",
    "confidence_score",
    "severity",
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
    "opened_on",
    "closed_on",
)

ALERT_COLUMNS = (
    "id",
    "ticker",
    "effective_date",
    "status",
    "severity",
    "source_event_key",
)
SETUP_LIFECYCLE_SCHEMA_IDS = {
    CHANGE_COLUMNS: "swinglens.setup-lifecycle.changes.v1",
    EPISODE_COLUMNS: "swinglens.setup-lifecycle.episodes.v1",
    ALERT_COLUMNS: "swinglens.setup-lifecycle.alerts.v1",
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
