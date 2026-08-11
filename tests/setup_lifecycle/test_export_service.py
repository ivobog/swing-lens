from __future__ import annotations

import csv
import json
from io import StringIO

from app.services.setup_lifecycle.export_service import (
    export_alerts_csv,
    export_changes_csv,
    export_episodes_csv,
    export_json,
)


def test_export_changes_csv_uses_stable_columns() -> None:
    content = export_changes_csv(
        {
            "items": [
                {
                    "id": 1,
                    "source_type": "LIFECYCLE_EVENT",
                    "lifecycle_event_id": 1,
                    "episode_id": 10,
                    "ticker": "MSFT",
                    "data_as_of_date": "2026-08-01",
                    "setup_family": "BREAKOUT",
                    "phase": "BREAKOUT",
                    "event_type": "STATE_TRANSITION",
                    "previous_state": "READY",
                    "current_state": "TRIGGERED",
                    "transition": "READY_TO_TRIGGERED",
                    "actionability": "ACTIONABLE",
                    "confidence": 91,
                    "technical_score": 8.1,
                    "technical_score_previous": 7.6,
                    "technical_score_delta": 0.5,
                    "reason_codes": ["CLOSE_ABOVE_TRIGGER"],
                    "severity": "ACTIONABLE",
                    "source_event_key": "source-1",
                    "extra": "ignored",
                }
            ]
        }
    )

    rows = list(csv.DictReader(StringIO(content)))

    assert len(rows) == 1
    assert rows[0]["source_type"] == "LIFECYCLE_EVENT"
    assert rows[0]["data_as_of_date"] == "2026-08-01"
    assert rows[0]["transition"] == "READY_TO_TRIGGERED"
    assert rows[0]["technical_score_delta"] == "0.5"
    assert json.loads(rows[0]["reason_codes"]) == ["CLOSE_ABOVE_TRIGGER"]
    assert rows[0]["source_event_key"] == "source-1"
    assert rows[0]["export_schema_id"] == "swinglens.setup-lifecycle.changes.v2"


def test_export_episodes_and_alerts_csv_include_headers_when_empty() -> None:
    episodes = export_episodes_csv({"items": []})
    alerts = export_alerts_csv({"items": []})

    assert episodes.startswith("id,ticker,setup_family,status,current_state")
    assert alerts.startswith(
        "id,ticker,effective_date,alert_type,severity,review_status,source_type"
    )


def test_export_json_is_sorted_and_pretty() -> None:
    content = export_json({"z": 1, "a": None})

    assert json.loads(content) == {"a": None, "z": 1}
    assert content.splitlines()[1].strip() == '"a": null,'
