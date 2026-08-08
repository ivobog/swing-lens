from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, inspect, text

RUN_COLUMN_NAMES = ("run_id", "source_run_id", "upload_run_id", "related_run_id")


@dataclass(frozen=True)
class EvidenceGraphResult:
    manifest: dict[str, Any]
    relationship_count: int
    row_count: int


def build_run_evidence_graph(
    engine: Engine,
    *,
    run_id: int,
    artifact_dir: Path,
    tickers: tuple[str, ...],
) -> EvidenceGraphResult:
    sql_dir = artifact_dir / "sql"
    result_dir = artifact_dir / "db-results"
    sql_dir.mkdir(parents=True, exist_ok=True)
    result_dir.mkdir(parents=True, exist_ok=True)
    schema = inspect(engine)
    table_names = sorted(schema.get_table_names())
    entries: list[dict[str, Any]] = []
    executed: set[tuple[str, str]] = set()

    for table_name in table_names:
        columns = {column["name"] for column in schema.get_columns(table_name)}
        for column in RUN_COLUMN_NAMES:
            if column in columns:
                relationship = (
                    "semantic background-job lineage"
                    if table_name == "background_jobs" and column == "related_run_id"
                    else f"direct {column}"
                )
                _append_query(
                    engine,
                    entries,
                    executed,
                    table_name=table_name,
                    relationship=relationship,
                    where_sql=f'"{column}" = :run_id',
                    params={"run_id": run_id},
                    sql_dir=sql_dir,
                    result_dir=result_dir,
                    ordinal=len(entries) + 1,
                )

    indirect_queries = _indirect_queries()
    for table_name, relationship, where_sql in indirect_queries:
        if table_name not in table_names:
            continue
        _append_query(
            engine,
            entries,
            executed,
            table_name=table_name,
            relationship=relationship,
            where_sql=where_sql,
            params={"run_id": run_id, "tickers": list(tickers)},
            sql_dir=sql_dir,
            result_dir=result_dir,
            ordinal=len(entries) + 1,
        )

    manifest = {
        "schema": "swinglens.single-run-evidence-graph.v1",
        "run_id": run_id,
        "database_table_count": len(table_names),
        "run_relationship_count": len(entries),
        "run_row_count": sum(entry["row_count"] for entry in entries),
        "tables": entries,
    }
    (artifact_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, default=_json_default),
        encoding="utf-8",
    )
    return EvidenceGraphResult(
        manifest=manifest,
        relationship_count=len(entries),
        row_count=manifest["run_row_count"],
    )


def query_rows(
    engine: Engine,
    sql: str,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(text(sql), params or {}).mappings()]


def database_integrity_checks(engine: Engine, run_id: int) -> list[dict[str, Any]]:
    checks = [
        (
            "run_exists_once",
            "select count(*) as value from upload_runs where id = :run_id",
            1,
        ),
        (
            "no_duplicate_raw_tickers",
            """
            select count(*) as value from (
              select ticker from raw_company_rows where run_id = :run_id
              group by ticker having count(*) > 1
            ) duplicates
            """,
            0,
        ),
        (
            "no_orphan_pipeline_steps",
            """
            select count(*) as value from pipeline_steps ps
            left join pipeline_runs pr on pr.id = ps.pipeline_run_id
            where pr.id is null
            """,
            0,
        ),
        (
            "no_orphan_winner_estimates",
            """
            select count(*) as value from winner_probability_estimates e
            left join winner_prediction_snapshots p on p.id = e.prediction_id
            where p.id is null
            """,
            0,
        ),
        (
            "no_orphan_lifecycle_alerts",
            """
            select count(*) as value from signal_alert_events a
            left join setup_lifecycle_evaluation_runs e on e.id = a.evaluation_run_id
            where a.evaluation_run_id is not null and e.id is null
            """,
            0,
        ),
        (
            "one_ceri_snapshot_per_run_ticker",
            """
            select count(*) as value from ceri_score_snapshots
            where run_id = :run_id
            """,
            8,
        ),
        (
            "no_orphan_ceri_alerts",
            """
            select count(*) as value from ceri_alert_events a
            left join ceri_change_events c on c.id = a.source_change_event_id
            where a.source_change_event_id is not null and c.id is null
            """,
            0,
        ),
        (
            "pipeline_terminal",
            """
            select count(*) as value from pipeline_runs
            where upload_run_id = :run_id and status in ('COMPLETED', 'PARTIAL')
            """,
            1,
        ),
    ]
    results: list[dict[str, Any]] = []
    for name, sql, expected in checks:
        rows = query_rows(engine, sql, {"run_id": run_id})
        actual = int(rows[0]["value"])
        results.append(
            {
                "name": name,
                "expected": expected,
                "actual": actual,
                "passed": actual == expected,
                "sql": " ".join(sql.split()),
            }
        )
    return results


def _append_query(
    engine: Engine,
    entries: list[dict[str, Any]],
    executed: set[tuple[str, str]],
    *,
    table_name: str,
    relationship: str,
    where_sql: str,
    params: dict[str, Any],
    sql_dir: Path,
    result_dir: Path,
    ordinal: int,
) -> None:
    key = (table_name, where_sql)
    if key in executed:
        return
    executed.add(key)
    primary_keys = inspect(engine).get_pk_constraint(table_name).get("constrained_columns") or []
    sql = f'SELECT * FROM "{table_name}" WHERE {where_sql} ORDER BY 1'
    effective_params = dict(params)
    if ":tickers" in sql:
        # PostgreSQL cannot bind a list to IN directly.  The semantic queries use ANY.
        effective_params["tickers"] = params["tickers"]
    rows = query_rows(engine, sql, effective_params)
    if not rows:
        return
    sanitized = [_sanitize_row(row) for row in rows]
    stem = f"{ordinal:03d}-{table_name}"
    (sql_dir / f"{stem}.sql").write_text(
        f"-- relationship: {relationship}\n{sql};\n-- params: {_safe_params(effective_params)}\n",
        encoding="utf-8",
    )
    (result_dir / f"{stem}.json").write_text(
        json.dumps(sanitized, indent=2, sort_keys=True, default=_json_default),
        encoding="utf-8",
    )
    ticker_values = sorted(
        {str(row.get("ticker")) for row in sanitized if row.get("ticker") not in (None, "")}
    )
    entries.append(
        {
            "table": table_name,
            "relationship_to_run": relationship,
            "row_count": len(rows),
            "primary_keys": [
                {column: row.get(column) for column in primary_keys} for row in sanitized
            ],
            "tickers": ticker_values,
            "important_identifiers": _important_identifiers(sanitized),
            "content_hash": _hash_rows(sanitized),
            "sql_artifact": f"sql/{stem}.sql",
            "result_artifact": f"db-results/{stem}.json",
        }
    )


def _indirect_queries() -> tuple[tuple[str, str, str], ...]:
    return (
        (
            "pipeline_steps",
            "pipeline_runs.upload_run_id -> pipeline_steps.pipeline_run_id",
            "pipeline_run_id in (select id from pipeline_runs where upload_run_id = :run_id)",
        ),
        (
            "ib_fetch_items",
            "ib_fetch_runs.run_id -> ib_fetch_items.fetch_run_id",
            "fetch_run_id in (select id from ib_fetch_runs where run_id = :run_id)",
        ),
        (
            "price_bar_revisions",
            "ib_fetch_items -> shared price-bar revision delta",
            "fetch_run_id in (select id from ib_fetch_runs where run_id = :run_id)",
        ),
        (
            "price_bars",
            "shared cache consumed by run ticker/date/timeframe/source",
            "ticker = any(:tickers)",
        ),
        (
            "ib_contracts",
            "shared contract cache consumed by run",
            "ticker = any(:tickers)",
        ),
        (
            "sector_rotation_rows",
            "sector_rotation_snapshots.run_id -> rows.snapshot_id",
            "snapshot_id in (select id from sector_rotation_snapshots where run_id = :run_id)",
        ),
        (
            "setup_lifecycle_events",
            "setup lifecycle evaluation for source run",
            "evaluation_run_id in (select id from setup_lifecycle_evaluation_runs "
            "where source_run_id = :run_id)",
        ),
        (
            "signal_change_events",
            "signal changes emitted by run evaluation",
            "evaluation_run_id in (select id from setup_lifecycle_evaluation_runs "
            "where source_run_id = :run_id)",
        ),
        (
            "signal_alert_events",
            "alerts emitted by run evaluation",
            "evaluation_run_id in (select id from setup_lifecycle_evaluation_runs "
            "where source_run_id = :run_id)",
        ),
        (
            "setup_lifecycle_episodes",
            "episodes whose current/opening snapshot belongs to run",
            "current_snapshot_id in (select id from setup_signal_snapshots "
            "where run_id = :run_id) or opening_snapshot_id in "
            "(select id from setup_signal_snapshots where run_id = :run_id)",
        ),
        (
            "winner_probability_estimates",
            "run prediction -> decision-time probability estimate",
            "prediction_id in (select id from winner_prediction_snapshots where run_id = :run_id)",
        ),
        (
            "winner_estimate_evidence_members",
            "run prediction/estimate -> evidence membership",
            "prediction_id in (select id from winner_prediction_snapshots "
            "where run_id = :run_id) or estimate_id in (select e.id from "
            "winner_probability_estimates e join winner_prediction_snapshots p "
            "on p.id=e.prediction_id where p.run_id=:run_id)",
        ),
        (
            "winner_forward_outcomes",
            "run prediction -> forward outcome",
            "prediction_id in (select id from winner_prediction_snapshots where run_id = :run_id)",
        ),
        (
            "winner_target_stop_outcomes",
            "run prediction -> target/stop outcome",
            "prediction_id in (select id from winner_prediction_snapshots where run_id = :run_id)",
        ),
        (
            "winner_similarity_links",
            "run prediction -> similarity evidence",
            "prediction_id in (select id from winner_prediction_snapshots where run_id = :run_id)",
        ),
        (
            "winner_evidence_manifests",
            "probability estimate -> immutable evidence manifest",
            "id in (select e.evidence_manifest_id from winner_probability_estimates e "
            "join winner_prediction_snapshots p on p.id=e.prediction_id "
            "where p.run_id=:run_id and e.evidence_manifest_id is not null)",
        ),
        (
            "winner_processing_runs",
            "single certification maturation operation produced run-owned outcomes",
            "process_type='WINNER_OUTCOME_MATURATION' and exists ("
            "select 1 from winner_forward_outcomes o join winner_prediction_snapshots p "
            "on p.id=o.prediction_id where p.run_id=:run_id and o.matured_at is not null "
            "and o.is_current_revision)",
        ),
        (
            "background_jobs",
            "run-owned matured outcome -> winner processing run -> background job",
            "id in (select w.background_job_id from winner_processing_runs w where "
            "w.process_type='WINNER_OUTCOME_MATURATION' and exists (select 1 from "
            "winner_forward_outcomes o join winner_prediction_snapshots p "
            "on p.id=o.prediction_id where p.run_id=:run_id and o.matured_at is not null "
            "and o.is_current_revision))",
        ),
        (
            "ceri_companies",
            "CERI run snapshot -> canonical company",
            "id in (select company_id from ceri_score_snapshots where run_id = :run_id)",
        ),
        (
            "ceri_revision_features",
            "CERI company evidence consumed by run snapshot",
            "company_id in (select company_id from ceri_score_snapshots where run_id = :run_id)",
        ),
        (
            "ceri_estimate_snapshots",
            "CERI estimate evidence for run companies",
            "company_id in (select company_id from ceri_score_snapshots where run_id = :run_id)",
        ),
        (
            "ceri_guidance_events",
            "CERI guidance evidence for run companies",
            "company_id in (select company_id from ceri_score_snapshots where run_id = :run_id)",
        ),
        (
            "ceri_catalyst_events",
            "CERI catalyst evidence for run companies",
            "company_id in (select company_id from ceri_score_snapshots where run_id = :run_id)",
        ),
        (
            "ceri_catalyst_event_revisions",
            "CERI catalyst event -> immutable revisions",
            "catalyst_event_id in (select id from ceri_catalyst_events where company_id in "
            "(select company_id from ceri_score_snapshots where run_id=:run_id))",
        ),
        (
            "ceri_catalyst_sources",
            "CERI catalyst event -> provider source lineage",
            "catalyst_event_id in (select id from ceri_catalyst_events where company_id in "
            "(select company_id from ceri_score_snapshots where run_id=:run_id))",
        ),
        (
            "ceri_derived_features",
            "CERI derived evidence for run companies",
            "company_id in (select company_id from ceri_score_snapshots where run_id=:run_id)",
        ),
        (
            "ceri_price_response_features",
            "CERI price-response evidence for run companies",
            "company_id in (select company_id from ceri_score_snapshots where run_id=:run_id)",
        ),
        (
            "ceri_source_records",
            "normalized CERI evidence -> provider source records",
            "id in (select source_record_id from ceri_estimate_snapshots where company_id in "
            "(select company_id from ceri_score_snapshots where run_id=:run_id) union "
            "select source_record_id from ceri_guidance_events where company_id in "
            "(select company_id from ceri_score_snapshots where run_id=:run_id) union "
            "select cs.source_record_id from ceri_catalyst_sources cs join ceri_catalyst_events ce "
            "on ce.id=cs.catalyst_event_id where ce.company_id in "
            "(select company_id from ceri_score_snapshots where run_id=:run_id))",
        ),
        (
            "ceri_ingestion_runs",
            "CERI source records -> deterministic manual-provider ingestion",
            "id in (select ingestion_run_id from ceri_source_records where id in "
            "(select source_record_id from ceri_estimate_snapshots where company_id in "
            "(select company_id from ceri_score_snapshots where run_id=:run_id) union "
            "select source_record_id from ceri_guidance_events where company_id in "
            "(select company_id from ceri_score_snapshots where run_id=:run_id) union "
            "select cs.source_record_id from ceri_catalyst_sources cs join ceri_catalyst_events ce "
            "on ce.id=cs.catalyst_event_id where ce.company_id in "
            "(select company_id from ceri_score_snapshots where run_id=:run_id)))",
        ),
        (
            "ceri_processing_runs",
            "CERI normalization/feature processing scoped to run tickers",
            "scope_json->>'ticker' = any(:tickers) or exists (select 1 from "
            "jsonb_array_elements_text(coalesce(scope_json->'tickers', '[]'::jsonb)) item "
            "where item.value = any(:tickers))",
        ),
        (
            "ceri_change_events",
            "CERI score snapshot delta linked to run",
            "to_snapshot_id in (select id from ceri_score_snapshots where run_id = :run_id)",
        ),
        (
            "ceri_alert_events",
            "CERI change event -> alert",
            "source_change_event_id in (select c.id from ceri_change_events c "
            "join ceri_score_snapshots s on s.id=c.to_snapshot_id "
            "where s.run_id=:run_id)",
        ),
    )


def _sanitize_row(row: dict[str, Any]) -> dict[str, Any]:
    restricted = {
        "raw_json",
        "restricted_normalized_json",
        "source_url",
        "source_reference",
        "file_path",
    }
    return {
        key: "<restricted>" if key in restricted and value not in (None, "") else value
        for key, value in row.items()
    }


def _important_identifiers(rows: list[dict[str, Any]]) -> dict[str, list[Any]]:
    candidates = (
        "id",
        "run_id",
        "source_run_id",
        "upload_run_id",
        "related_run_id",
        "pipeline_run_id",
        "prediction_id",
        "evaluation_run_id",
        "snapshot_id",
        "episode_id",
        "evidence_hash",
        "config_hash",
        "feature_vector_hash",
    )
    result: dict[str, list[Any]] = {}
    for key in candidates:
        values = sorted(
            {row[key] for row in rows if row.get(key) not in (None, "")},
            key=lambda value: str(value),
        )
        if values:
            result[key] = values[:100]
    return result


def _hash_rows(rows: list[dict[str, Any]]) -> str:
    encoded = json.dumps(rows, sort_keys=True, default=_json_default).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_params(params: dict[str, Any]) -> str:
    return json.dumps(params, sort_keys=True, default=_json_default)


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    return str(value)
