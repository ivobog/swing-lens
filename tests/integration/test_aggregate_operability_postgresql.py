from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from alembic import command
from app.models.tables import SignalAlertEvent, SignalAlertRule
from app.services.background_performance_baseline import _technical_artifact_report
from app.services.ceri.query_service import _provider_cost_summary
from app.services.setup_lifecycle.query_service import _alerts_summary
from app.services.winner_probability.cohort_generation_service import (
    EvidenceWatermarkService,
)
from scripts.check_architecture_registry import R4_AGGREGATE_POLICIES
from scripts.inspect_aggregate_operability import _queries

pytestmark = [pytest.mark.integration, pytest.mark.performance]

SEED_ROWS = {
    "technical_feature_artifacts": 50_000,
    "ceri_ingestion_runs": 275_000,
    "ceri_provider_request_telemetry": 200_000,
    "signal_alert_events": 25_000,
    "winner_forward_outcomes": 900_000,
    "winner_target_stop_outcomes": 175_000,
    "winner_training_eligibility_decisions": 45_000,
    "winner_training_outcome_replays": 2_000,
    "winner_temporal_validity_decisions": 30_000,
    "winner_market_data_obligations": 45_000,
}


def _migrate(database_url: str) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")


def _seed(connection) -> None:
    connection.exec_driver_sql("SET LOCAL synchronous_commit = off")
    connection.exec_driver_sql("SET session_replication_role = replica")
    connection.execute(
        text(
            """
            INSERT INTO technical_feature_artifacts (
                ticker, timeframe, artifact_kind, input_signature,
                artifact_schema_version, technical_engine_version,
                feature_config_hash, scoring_config_hash, input_versions_json,
                artifact_json, status, shadow_validation_status,
                shadow_validation_count, shadow_mismatch_count
            )
            SELECT 'T' || g, '1 day', CASE WHEN g % 2 = 0 THEN 'LOCAL' ELSE 'RELATIVE' END,
                   'sig-' || g, 'v1', 'engine-v1', 'feature', 'score', '{}'::jsonb,
                   '{}'::jsonb, (ARRAY['READY','STALE','FAILED'])[1 + g % 3],
                   (ARRAY['UNVALIDATED','MATCH','MISMATCH'])[1 + g % 3], g % 7, g % 2
            FROM generate_series(1, :rows) AS g
            """
        ),
        {"rows": SEED_ROWS["technical_feature_artifacts"]},
    )
    connection.execute(
        text(
            """
            INSERT INTO ceri_ingestion_runs (
                provider, dataset, status, request_key, retry_count,
                requested_count, fetched_count, inserted_count, deduplicated_count,
                corrected_count, quarantined_count, failed_count, warning_count,
                started_at, completed_at
            )
            SELECT 'provider-' || g % 4, 'dataset-' || g % 2, 'COMPLETED', 'request-' || g,
                   0, 1, 1, 1, 0, 0, 0, 0, 0,
                   now() - (g % 365) * interval '1 day',
                   now() - (g % 365) * interval '1 day'
            FROM generate_series(1, :rows) AS g
            """
        ),
        {"rows": SEED_ROWS["ceri_ingestion_runs"]},
    )
    connection.execute(
        text(
            """
            INSERT INTO ceri_provider_request_telemetry (
                provider, dataset, endpoint, status_code, call_cost, latency_ms,
                retry_count, response_bytes, stored_bytes, observed_at
            )
            SELECT 'provider-' || g % 4, 'dataset-' || g % 2, '/endpoint/' || g % 8,
                   200, 1 + g % 3, 10 + g % 100, 0, 1000 + g % 200, 500 + g % 100,
                   now() - (g % 730) * interval '1 day'
            FROM generate_series(1, :rows) AS g
            """
        ),
        {"rows": SEED_ROWS["ceri_provider_request_telemetry"]},
    )
    connection.exec_driver_sql(
        """
        INSERT INTO signal_alert_rules (id, rule_id, severity, scope, config_version)
        SELECT g, 'rule-' || g, (ARRAY['INFO','NOTABLE','ACTIONABLE','RISK'])[g],
               'GLOBAL', 'v1'
        FROM generate_series(1, 4) AS g
        """
    )
    connection.execute(
        text(
            """
            INSERT INTO signal_alert_events (
                alert_rule_id, ticker, timeframe, effective_date, event_key,
                source_event_key, status, severity
            )
            SELECT 1 + g % 4, 'T' || g % 1000, '1d', current_date - (g % 365),
                   'event-' || g, 'source-' || g,
                   (ARRAY['UNREAD','ACKNOWLEDGED','DISMISSED'])[1 + g % 3],
                   (ARRAY['INFO','NOTABLE','ACTIONABLE','RISK'])[1 + g % 4]
            FROM generate_series(1, :rows) AS g
            """
        ),
        {"rows": SEED_ROWS["signal_alert_events"]},
    )
    connection.execute(
        text(
            """
            INSERT INTO winner_forward_outcomes (
                id, prediction_id, entry_model, horizon_sessions, status, is_current_revision
            )
            SELECT g, g, 'NEXT_OPEN', 5, 'MATURED', true
            FROM generate_series(1, :rows) AS g
            """
        ),
        {"rows": SEED_ROWS["winner_forward_outcomes"]},
    )
    connection.execute(
        text(
            """
            INSERT INTO winner_target_stop_outcomes (
                id, prediction_id, outcome_definition_id, forward_outcome_id,
                entry_model, horizon_sessions, status, is_current_revision,
                target_pct, stop_pct
            )
            SELECT g, g, 1 + g % 5, g * 5, 'NEXT_OPEN', 5, 'MATURED', true, 10, 5
            FROM generate_series(1, :rows) AS g
            """
        ),
        {"rows": SEED_ROWS["winner_target_stop_outcomes"]},
    )
    connection.execute(
        text(
            """
            INSERT INTO winner_training_eligibility_decisions (
                id, prediction_id, policy_version, training_family,
                compatibility_bridge_version, source_feature_schema_version,
                source_calculation_version, source_config_hash,
                target_feature_schema_version, target_calculation_version,
                target_config_hash, target_outcome_definition_id,
                classification_status, training_allowed, outcome_compatibility_status,
                pit_status, episode_status, quality_status, source_manifest_hash,
                request_key, decision_hash, classified_at, classified_by
            )
            SELECT g, g, 'p1', 'family-' || g % 2, 'b1', 's1', 'c1', 'h1',
                   's2', 'c2', 'h2', 1 + g % 5, 'ALLOWED', true, 'COMPATIBLE',
                   'VALID', 'VALID', 'VALID', 'manifest-' || g,
                   'eligibility-' || g, 'decision-' || g, now(), 'r4'
            FROM generate_series(1, :rows) AS g
            """
        ),
        {"rows": SEED_ROWS["winner_training_eligibility_decisions"]},
    )
    connection.execute(
        text(
            """
            INSERT INTO winner_training_outcome_replays (
                id, eligibility_decision_id, prediction_id, target_outcome_definition_id,
                training_family, reconstruction_method, replay_policy_version,
                compatibility_bridge_version, entry_model, horizon_sessions,
                entry_session, due_session, entry_price, exit_price, close_return_pct,
                mfe_pct, mae_pct, target_pct, stop_pct, target_hit, stop_hit,
                first_event, same_bar_conflict, primary_winner, optimistic_winner,
                conservative_winner, bar_lineage_json, source_bar_lineage_hash,
                source_revision_cutoff_at, status, request_key, replay_hash,
                replayed_at, replayed_by
            )
            SELECT g, g, g, 1 + g % 5, 'family-' || g % 2, 'DIRECT', 'p1', 'b1',
                   'NEXT_OPEN', 5, current_date - 10, current_date - 5, 100, 110, 10,
                   12, -3, 10, 5, true, false, 'TARGET', false, true, true, true,
                   '{}'::jsonb, 'lineage-' || g, now(), 'COMPLETED',
                   'replay-' || g, 'replay-hash-' || g, now(), 'r4'
            FROM generate_series(1, :rows) AS g
            """
        ),
        {"rows": SEED_ROWS["winner_training_outcome_replays"]},
    )
    connection.execute(
        text(
            """
            INSERT INTO winner_temporal_validity_decisions (
                id, prediction_id, validation_sequence, status, entry_timing_valid,
                source_cutoff_valid, semantic_input_time_valid, evidence_eligible,
                validation_version, decision_at, entry_session, entry_open_at,
                evaluated_by
            )
            SELECT g, g, 1, 'VALID', true, true, true, true, 'v1',
                   now() - interval '2 days', current_date - 1,
                   now() - interval '1 day', 'r4'
            FROM generate_series(1, :rows) AS g
            """
        ),
        {"rows": SEED_ROWS["winner_temporal_validity_decisions"]},
    )
    connection.execute(
        text(
            """
            INSERT INTO winner_market_data_obligations (
                prediction_id, forward_outcome_id, ticker_snapshot, entry_session,
                required_through_session, required_sessions_json, what_to_show,
                status, price_series_watermark
            )
            SELECT g, g, 'T' || g, current_date - 10, current_date - 5,
                   '[]'::jsonb, CASE WHEN g % 2 = 0 THEN 'TRADES' ELSE 'ADJUSTED_LAST' END,
                   (ARRAY['FETCH_REQUIRED','SATISFIED','IDENTITY_BLOCKED','UNAVAILABLE','FAILED'])[
                       1 + g % 5
                   ], 'watermark-' || g
            FROM generate_series(1, :rows) AS g
            """
        ),
        {"rows": SEED_ROWS["winner_market_data_obligations"]},
    )
    connection.exec_driver_sql("SET session_replication_role = origin")
    for table in SEED_ROWS:
        connection.exec_driver_sql(f'ANALYZE "{table}"')


def _nodes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    result = [plan]
    for child in plan.get("Plans", []):
        result.extend(_nodes(child))
    return result


def _plan_summary(connection, read_id: str, query: str) -> dict[str, Any]:
    payload = connection.exec_driver_sql(
        f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {query}"
    ).scalar_one()
    if isinstance(payload, str):
        payload = json.loads(payload)
    document = payload[0]
    plan = document["Plan"]
    nodes = _nodes(plan)
    scans = [node for node in nodes if "Scan" in str(node.get("Node Type"))]
    sort_nodes = [node for node in nodes if node.get("Node Type") == "Sort"]
    hash_nodes = [node for node in nodes if "Hash" in str(node.get("Node Type"))]
    return {
        "read_id": read_id,
        "top_node": plan["Node Type"],
        "actual_result_rows": plan.get("Actual Rows", 0) * plan.get("Actual Loops", 1),
        "rows_examined": sum(
            (node.get("Actual Rows", 0) + node.get("Rows Removed by Filter", 0))
            * node.get("Actual Loops", 1)
            for node in scans
        ),
        "estimated_rows_examined": sum(
            node.get("Plan Rows", 0) * node.get("Actual Loops", 1) for node in scans
        ),
        "scan_nodes": [
            {
                "node_type": node.get("Node Type"),
                "relation": node.get("Relation Name"),
                "index": node.get("Index Name"),
                "actual_rows": node.get("Actual Rows", 0),
                "loops": node.get("Actual Loops", 1),
                "rows_removed_by_filter": node.get("Rows Removed by Filter", 0),
            }
            for node in scans
        ],
        "shared_hit_blocks": sum(node.get("Shared Hit Blocks", 0) for node in nodes),
        "shared_read_blocks": sum(node.get("Shared Read Blocks", 0) for node in nodes),
        "temp_read_blocks": sum(node.get("Temp Read Blocks", 0) for node in nodes),
        "temp_written_blocks": sum(node.get("Temp Written Blocks", 0) for node in nodes),
        "sort_nodes": [
            {
                "method": node.get("Sort Method"),
                "space_kb": node.get("Sort Space Used", 0),
                "space_type": node.get("Sort Space Type"),
            }
            for node in sort_nodes
        ],
        "hash_nodes": len(hash_nodes),
        "planning_time_ms": document.get("Planning Time", 0.0),
        "execution_time_ms": document.get("Execution Time", 0.0),
    }


def _reference_watermark(session: Session) -> tuple[int, int, int, int, int]:
    return tuple(
        int(value or 0)
        for value in session.execute(
            text(
                """
                SELECT
                  (SELECT max(forward_outcome.id)
                     FROM winner_forward_outcomes AS forward_outcome
                     JOIN winner_target_stop_outcomes AS target
                       ON target.forward_outcome_id = forward_outcome.id
                    WHERE target.outcome_definition_id = 1
                      AND target.status = 'MATURED'
                      AND target.is_current_revision IS TRUE
                      AND forward_outcome.is_current_revision IS TRUE),
                  (SELECT max(id) FROM winner_target_stop_outcomes
                    WHERE outcome_definition_id = 1 AND status = 'MATURED'
                      AND is_current_revision IS TRUE),
                  (SELECT max(id) FROM winner_training_eligibility_decisions
                    WHERE target_outcome_definition_id = 1),
                  (SELECT max(id) FROM winner_training_outcome_replays
                    WHERE target_outcome_definition_id = 1),
                  (SELECT max(temporal.id)
                     FROM winner_temporal_validity_decisions AS temporal
                     JOIN winner_target_stop_outcomes AS target
                       ON target.prediction_id = temporal.prediction_id
                    WHERE target.outcome_definition_id = 1)
                """
            )
        ).one()
    )


def test_r4_aggregate_semantics_and_postgresql_plans(
    disposable_postgres_database: str,
) -> None:
    _migrate(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with engine.begin() as connection:
        _seed(connection)

    with Session(engine) as session:
        technical = _technical_artifact_report(session)
        assert (
            sum(row["count"] for row in technical["counts"])
            == SEED_ROWS["technical_feature_artifacts"]
        )
        assert (
            sum(row["artifact_count"] for row in technical["shadow_validation"])
            == SEED_ROWS["technical_feature_artifacts"]
        )
        pairs = session.execute(
            text(
                "SELECT provider, dataset FROM ceri_ingestion_runs "
                "GROUP BY provider, dataset ORDER BY provider, dataset"
            )
        ).all()
        assert len(pairs) == 4
        provider_summary = _provider_cost_summary(session)
        assert (
            sum(row["request_rows"] for row in provider_summary.values())
            == SEED_ROWS["ceri_provider_request_telemetry"]
        )
        alert_statement = select(SignalAlertEvent).join(
            SignalAlertRule, SignalAlertEvent.alert_rule_id == SignalAlertRule.id
        )
        alert_summary = _alerts_summary(session, alert_statement)
        assert (
            sum(alert_summary[key] for key in ("unread", "acknowledged", "dismissed"))
            == SEED_ROWS["signal_alert_events"]
        )
        assert (
            sum(alert_summary[key] for key in ("info", "notable", "actionable", "risk"))
            == SEED_ROWS["signal_alert_events"]
        )
        expected_watermark = _reference_watermark(session)
        actual_watermark = EvidenceWatermarkService().current_material_watermark(
            session, outcome_definition_id=1
        )
        assert tuple(actual_watermark.as_dict().values()) == expected_watermark
        obligation_counts = dict(
            session.execute(
                text("SELECT status, count(id) FROM winner_market_data_obligations GROUP BY status")
            ).all()
        )
        assert sum(obligation_counts.values()) == SEED_ROWS["winner_market_data_obligations"]

    policies = {policy["id"]: policy for policy in R4_AGGREGATE_POLICIES}
    summaries: dict[str, Any] = {}
    with engine.connect() as connection:
        index_names = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()"
            )
        }
        for read_id, query in _queries(1).items():
            policy = policies[read_id]
            assert set(policy["required_indexes"]) <= index_names
            summary = _plan_summary(connection, read_id, query)
            summaries[read_id] = summary
            assert summary["actual_result_rows"] <= policy["maximum_result_rows"]
            assert summary["rows_examined"] <= policy["maximum_expected_rows_scanned"]
            assert summary["rows_examined"] <= max(
                summary["estimated_rows_examined"] * policy["plan_rows_multiplier"],
                policy["maximum_result_rows"],
            )
            assert summary["execution_time_ms"] <= policy["execution_budget_ms"]
            assert summary["temp_read_blocks"] == 0
            assert summary["temp_written_blocks"] == 0
            if not policy["sequential_scan_allowed"]:
                forbidden_tables = set(policy["tables"])
                assert not [
                    scan
                    for scan in summary["scan_nodes"]
                    if scan["node_type"] == "Seq Scan" and scan["relation"] in forbidden_tables
                ]
    evidence_path = os.environ.get("R4_PLAN_EVIDENCE_PATH")
    if evidence_path:
        path = Path(evidence_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"seed_rows": SEED_ROWS, "plans": summaries}, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
    engine.dispose()
