from __future__ import annotations

import csv
import json
from io import StringIO
from typing import Any

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


def export_changes_csv(payload: dict[str, Any]) -> str:
    return _csv(payload.get("items", []), CHANGE_COLUMNS)


def export_episodes_csv(payload: dict[str, Any]) -> str:
    return _csv(payload.get("items", []), EPISODE_COLUMNS)


def export_alerts_csv(payload: dict[str, Any]) -> str:
    return _csv(payload.get("items", []), ALERT_COLUMNS)


def export_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, default=str)


def _csv(rows: list[dict[str, Any]], columns: tuple[str, ...]) -> str:
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({column: _cell(row.get(column)) for column in columns})
    return buffer.getvalue()


def _cell(value: Any) -> Any:
    if isinstance(value, list | dict):
        return json.dumps(value, sort_keys=True, default=str)
    return value
