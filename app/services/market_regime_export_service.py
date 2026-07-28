from __future__ import annotations

import csv
import json
from datetime import date, datetime
from io import StringIO
from typing import Any

from app.models.tables import MarketRegimeSnapshot

MARKET_REGIME_CSV_HEADERS = [
    "as_of_date",
    "regime",
    "risk_state",
    "score",
    "confidence",
    "risk_off",
    "gate_ok",
    "position_size_multiplier",
    "preferred_profiles",
    "warnings",
]


def snapshot_to_payload(snapshot: MarketRegimeSnapshot) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "run_id": snapshot.run_id,
        "as_of_date": _json_value(snapshot.as_of_date),
        "calculation_version": snapshot.calculation_version,
        "config_version": snapshot.config_version,
        "regime": snapshot.regime,
        "risk_state": snapshot.risk_state,
        "score": snapshot.score,
        "risk_off": snapshot.risk_off,
        "gate_ok": snapshot.gate_ok,
        "confidence": snapshot.confidence,
        "action_summary": snapshot.action_summary,
        "position_size_multiplier": snapshot.position_size_multiplier,
        "policy": {
            "preferred_profiles": snapshot.preferred_profiles_json,
            "allowed_profiles": snapshot.allowed_profiles_json,
            "reduced_profiles": snapshot.reduced_profiles_json,
            "blocked_profiles": snapshot.blocked_profiles_json,
            "allowed_setups": snapshot.allowed_setups_json,
            "blocked_setups": snapshot.blocked_setups_json,
        },
        "input_symbols": snapshot.input_symbols_json,
        "index_health": snapshot.index_health_json,
        "universe_participation": snapshot.universe_participation_json,
        "sector_leadership": snapshot.sector_leadership_json,
        "reasons": snapshot.reasons_json,
        "warnings": snapshot.warnings_json,
        "debug": snapshot.debug_json,
        "created_at": _json_value(snapshot.created_at),
        "updated_at": _json_value(snapshot.updated_at),
    }


def history_to_payload(snapshots: list[MarketRegimeSnapshot]) -> list[dict[str, Any]]:
    return [snapshot_to_payload(snapshot) for snapshot in snapshots]


def export_snapshot_json(snapshot: MarketRegimeSnapshot) -> str:
    return json.dumps(snapshot_to_payload(snapshot), indent=2, sort_keys=True)


def export_snapshot_csv(snapshot: MarketRegimeSnapshot) -> str:
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=MARKET_REGIME_CSV_HEADERS, lineterminator="\n")
    writer.writeheader()
    writer.writerow(_snapshot_csv_row(snapshot))
    return buffer.getvalue()


def _snapshot_csv_row(snapshot: MarketRegimeSnapshot) -> dict[str, Any]:
    return {
        "as_of_date": _csv_value(snapshot.as_of_date),
        "regime": snapshot.regime,
        "risk_state": snapshot.risk_state,
        "score": snapshot.score,
        "confidence": snapshot.confidence,
        "risk_off": snapshot.risk_off,
        "gate_ok": snapshot.gate_ok,
        "position_size_multiplier": snapshot.position_size_multiplier,
        "preferred_profiles": _list_text(snapshot.preferred_profiles_json),
        "warnings": _list_text(snapshot.warnings_json),
    }


def _json_value(value: Any) -> Any:
    if isinstance(value, date | datetime):
        return value.isoformat()
    return value


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    return value


def _list_text(values: Any) -> str:
    if not isinstance(values, list):
        return ""
    return "; ".join(str(value) for value in values)
