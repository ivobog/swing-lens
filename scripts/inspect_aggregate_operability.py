"""Read-only PostgreSQL cardinality and plan inspection for R4 aggregate queries."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from sqlalchemy.engine import make_url

from app.settings import get_settings

TABLES: dict[str, dict[str, Any]] = {
    "technical_feature_artifacts": {
        "temporal": "created_at",
        "distinct": ("ticker", "artifact_kind", "status"),
    },
    "ceri_ingestion_runs": {
        "temporal": "created_at",
        "distinct": ("provider", "dataset", "status"),
    },
    "ceri_provider_request_telemetry": {
        "temporal": "observed_at",
        "distinct": ("provider", "dataset", "endpoint"),
    },
    "signal_alert_events": {
        "temporal": "created_at",
        "distinct": ("ticker", "status", "severity"),
    },
    "winner_target_stop_outcomes": {
        "temporal": "evaluated_at",
        "distinct": ("outcome_definition_id", "status"),
    },
    "winner_forward_outcomes": {
        "temporal": "matured_at",
        "distinct": ("status", "entry_model"),
    },
    "winner_training_eligibility_decisions": {
        "temporal": "classified_at",
        "distinct": ("target_outcome_definition_id", "training_family"),
    },
    "winner_training_outcome_replays": {
        "temporal": "replayed_at",
        "distinct": ("target_outcome_definition_id", "training_family", "status"),
    },
    "winner_temporal_validity_decisions": {
        "temporal": "evaluated_at",
        "distinct": ("status",),
    },
    "winner_market_data_obligations": {
        "temporal": "created_at",
        "distinct": ("status", "what_to_show", "ticker_snapshot"),
    },
}


def _queries(outcome_definition_id: int) -> dict[str, str]:
    return {
        "READ-002": """
            SELECT status, artifact_kind, count(id)
            FROM technical_feature_artifacts
            GROUP BY status, artifact_kind
        """,
        "READ-003": """
            SELECT shadow_validation_status, count(id),
                   sum(shadow_validation_count), sum(shadow_mismatch_count)
            FROM technical_feature_artifacts
            GROUP BY shadow_validation_status
        """,
        "READ-010": """
            SELECT provider, dataset
            FROM ceri_ingestion_runs
            GROUP BY provider, dataset
            ORDER BY provider, dataset
        """,
        "READ-012": """
            SELECT provider, count(*), coalesce(sum(call_cost), 0),
                   coalesce(sum(latency_ms), 0), coalesce(sum(response_bytes), 0),
                   coalesce(sum(stored_bytes), 0)
            FROM ceri_provider_request_telemetry
            GROUP BY provider
            ORDER BY provider
        """,
        "READ-022": """
            SELECT alerts.status, count(*)
            FROM (
                SELECT event.status
                FROM signal_alert_events AS event
                JOIN signal_alert_rules AS rule ON event.alert_rule_id = rule.id
            ) AS alerts
            GROUP BY alerts.status
        """,
        "READ-023": """
            SELECT alerts.severity, count(*)
            FROM (
                SELECT event.severity
                FROM signal_alert_events AS event
                JOIN signal_alert_rules AS rule ON event.alert_rule_id = rule.id
            ) AS alerts
            GROUP BY alerts.severity
        """,
        "READ-025": f"""
            SELECT
              (SELECT forward_outcome.id
                 FROM winner_forward_outcomes AS forward_outcome
                 JOIN winner_target_stop_outcomes AS target
                   ON target.forward_outcome_id = forward_outcome.id
                WHERE target.outcome_definition_id = {outcome_definition_id}
                  AND target.status = 'MATURED'
                  AND target.is_current_revision IS TRUE
                  AND forward_outcome.is_current_revision IS TRUE
                ORDER BY forward_outcome.id DESC LIMIT 1),
              (SELECT target.id
                 FROM winner_target_stop_outcomes AS target
                WHERE target.outcome_definition_id = {outcome_definition_id}
                  AND target.status = 'MATURED'
                  AND target.is_current_revision IS TRUE
                ORDER BY target.id DESC LIMIT 1),
              (SELECT decision.id
                 FROM winner_training_eligibility_decisions AS decision
                WHERE decision.target_outcome_definition_id = {outcome_definition_id}
                ORDER BY decision.id DESC LIMIT 1),
              (SELECT replay.id
                 FROM winner_training_outcome_replays AS replay
                WHERE replay.target_outcome_definition_id = {outcome_definition_id}
                ORDER BY replay.id DESC LIMIT 1),
              (SELECT temporal.id
                 FROM winner_temporal_validity_decisions AS temporal
                 JOIN winner_target_stop_outcomes AS target
                  ON target.prediction_id = temporal.prediction_id
                WHERE target.outcome_definition_id = {outcome_definition_id}
                ORDER BY temporal.id DESC LIMIT 1)
        """,
        "READ-026": """
            SELECT status, count(id)
            FROM winner_market_data_obligations
            GROUP BY status
        """,
    }


def _plan_nodes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = [plan]
    for child in plan.get("Plans", []):
        nodes.extend(_plan_nodes(child))
    return nodes


def inspect(database_url: str) -> dict[str, Any]:
    native_url = (
        make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)
    )
    with psycopg.connect(native_url) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        connection.execute("SET LOCAL statement_timeout = '15s'")
        identity = connection.execute(
            "SELECT current_database(), current_user, current_setting('server_version')"
        ).fetchone()
        cardinality: dict[str, Any] = {}
        for table, policy in TABLES.items():
            row_count = connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
            size = connection.execute(
                """
                SELECT pg_relation_size(%s::regclass), pg_indexes_size(%s::regclass),
                       pg_total_relation_size(%s::regclass)
                """,
                (table, table, table),
            ).fetchone()
            temporal = policy["temporal"]
            temporal_range = connection.execute(
                f'SELECT min("{temporal}"), max("{temporal}") FROM "{table}"'
            ).fetchone()
            distinct = {
                column: connection.execute(
                    f'SELECT count(DISTINCT "{column}") FROM "{table}"'
                ).fetchone()[0]
                for column in policy["distinct"]
            }
            indexes = connection.execute(
                """
                SELECT indexname, indexdef,
                       pg_relation_size(
                           (quote_ident(schemaname) || '.' || quote_ident(indexname))::regclass
                       )
                FROM pg_indexes
                WHERE schemaname = current_schema() AND tablename = %s
                ORDER BY indexname
                """,
                (table,),
            ).fetchall()
            cardinality[table] = {
                "rows": row_count,
                "table_bytes": size[0],
                "index_bytes": size[1],
                "total_bytes": size[2],
                "temporal_column": temporal,
                "temporal_min": temporal_range[0].isoformat() if temporal_range[0] else None,
                "temporal_max": temporal_range[1].isoformat() if temporal_range[1] else None,
                "distinct": distinct,
                "indexes": [name for name, _definition, _size in indexes],
                "index_details": [
                    {"name": name, "bytes": index_size} for name, _definition, index_size in indexes
                ],
            }
        outcome_definition_id = connection.execute(
            "SELECT coalesce(max(id), 0) FROM winner_outcome_definitions"
        ).fetchone()[0]
        plans: dict[str, Any] = {}
        for read_id, query in _queries(int(outcome_definition_id)).items():
            payload = connection.execute(f"EXPLAIN (FORMAT JSON) {query}").fetchone()[0]
            root = payload[0]["Plan"]
            nodes = _plan_nodes(root)
            plans[read_id] = {
                "top_node": root["Node Type"],
                "estimated_rows": root.get("Plan Rows"),
                "scan_nodes": [
                    {
                        "node_type": node.get("Node Type"),
                        "relation": node.get("Relation Name"),
                        "index": node.get("Index Name"),
                        "estimated_rows": node.get("Plan Rows"),
                        "filter": node.get("Filter"),
                        "index_condition": node.get("Index Cond"),
                    }
                    for node in nodes
                    if "Scan" in str(node.get("Node Type"))
                ],
                "sort_nodes": sum(node.get("Node Type") == "Sort" for node in nodes),
                "hash_nodes": sum("Hash" in str(node.get("Node Type")) for node in nodes),
            }
        connection.rollback()
    return {
        "captured_at": datetime.now(UTC).isoformat(),
        "database": identity[0],
        "user": identity[1],
        "server_version": identity[2],
        "transaction_read_only": True,
        "cardinality": cardinality,
        "plans": plans,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = inspect(get_settings().database_url)
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"R4_READ_ONLY_INSPECTION_WRITTEN: {args.output}")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
