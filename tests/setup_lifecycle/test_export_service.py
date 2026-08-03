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
                    "ticker": "MSFT",
                    "effective_date": "2026-08-01",
                    "setup_family": "BREAKOUT",
                    "event_type": "STATE_TRANSITION",
                    "from_state": "READY",
                    "to_state": "TRIGGERED",
                    "actionability_after": "ACTIONABLE",
                    "confidence_score": 91,
                    "severity": "ACTIONABLE",
                    "extra": "ignored",
                }
            ]
        }
    )

    rows = list(csv.DictReader(StringIO(content)))

    assert rows == [
        {
            "id": "1",
            "ticker": "MSFT",
            "effective_date": "2026-08-01",
            "setup_family": "BREAKOUT",
            "event_type": "STATE_TRANSITION",
            "from_state": "READY",
            "to_state": "TRIGGERED",
            "actionability_after": "ACTIONABLE",
            "confidence_score": "91",
            "severity": "ACTIONABLE",
            "export_schema_id": "swinglens.setup-lifecycle.changes.v1",
            "export_schema_version": "1",
            "guidance_type": "research_context",
            "execution_instruction": "False",
        }
    ]


def test_export_episodes_and_alerts_csv_include_headers_when_empty() -> None:
    episodes = export_episodes_csv({"items": []})
    alerts = export_alerts_csv({"items": []})

    assert episodes.startswith("id,ticker,setup_family,status,current_state")
    assert alerts.startswith("id,ticker,effective_date,status,severity,source_event_key")


def test_export_json_is_sorted_and_pretty() -> None:
    content = export_json({"z": 1, "a": None})

    assert json.loads(content) == {"a": None, "z": 1}
    assert content.splitlines()[1].strip() == '"a": null,'
