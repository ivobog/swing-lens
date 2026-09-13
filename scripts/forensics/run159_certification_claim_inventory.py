from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.settings import Settings

AFFECTED_JOB_IDS = (*range(43160, 43168), *range(43174, 43177))
PROTECTED_RUN_IDS = (157, 158, 159)


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _row_dict(row: Any) -> dict[str, Any]:
    return {key: _json_value(value) for key, value in row._mapping.items()}


def _scalar(connection: Any, statement: str, **params: Any) -> int:
    return int(connection.execute(text(statement), params).scalar_one())


def build_inventory() -> dict[str, Any]:
    settings = Settings()
    url = make_url(settings.database_url)
    if url.database != "swinglens":
        raise RuntimeError("forensic inventory is restricted to the swinglens production database")
    engine = create_engine(settings.database_url)
    prospective_session = f"prospective-{uuid4().hex}"
    generated_at = datetime.now(UTC)
    try:
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
            connection.execute(text("set transaction read only"))
            schema_head = str(
                connection.execute(text("select version_num from alembic_version")).scalar_one()
            )
            pipeline = connection.execute(
                text(
                    "select id, upload_run_id, status, created_at, started_at, completed_at "
                    "from pipeline_runs where id = 149"
                )
            ).one()
            cancellation_at = pipeline.completed_at

            queue = {
                "ready_jobs": _scalar(
                    connection,
                    "select count(*) from background_jobs "
                    "where status in ('QUEUED','RECOVERING') and run_after <= :now",
                    now=generated_at,
                ),
                "retryable_jobs": _scalar(
                    connection,
                    "select count(*) from background_jobs "
                    "where status = 'RETRYING' and run_after <= :now",
                    now=generated_at,
                ),
                "scheduled_jobs": _scalar(
                    connection,
                    "select count(*) from background_jobs "
                    "where status in ('QUEUED','RECOVERING','RETRYING') and run_after > :now",
                    now=generated_at,
                ),
                "active_jobs": _scalar(
                    connection,
                    "select count(*) from background_jobs "
                    "where status in ('QUEUED','RECOVERING','RETRYING','RUNNING')",
                ),
                "historical_authorized_descendants": _scalar(
                    connection,
                    "select count(*) from background_jobs child "
                    "where child.job_type <> 'FULL_PIPELINE' "
                    "and child.root_correlation_id in ("
                    "select root_correlation_id from background_jobs "
                    "where job_type='FULL_PIPELINE' "
                    "and coalesce((payload_json->>'certification_authorized')::boolean, false)"
                    ")",
                ),
            }
            queue["protected_run_descendants"] = {
                str(run_id): _scalar(
                    connection,
                    "select count(*) from background_jobs where related_run_id=:run_id "
                    "and job_type <> 'FULL_PIPELINE'",
                    run_id=run_id,
                )
                for run_id in PROTECTED_RUN_IDS
            }

            old_predicate = """
                select id from background_jobs job
                where status in ('QUEUED','RECOVERING') and run_after <= :now
                  and coalesce(
                    (job.job_type='FULL_PIPELINE'
                     and coalesce((job.payload_json->>'certification_authorized')::boolean,false))
                    or job.root_correlation_id in (
                      select root_correlation_id from background_jobs
                      where job_type='FULL_PIPELINE'
                        and coalesce((payload_json->>'certification_authorized')::boolean,false)
                        and root_correlation_id is not null
                    ), false)
                order by id
            """
            predicate_416 = """
                select id from background_jobs job
                where status in ('QUEUED','RECOVERING') and run_after <= :now
                  and coalesce(
                    (job.job_type='FULL_PIPELINE'
                     and coalesce((job.payload_json->>'certification_authorized')::boolean,false)
                     and job.status in ('QUEUED','RECOVERING','RETRYING','RUNNING'))
                    or job.root_correlation_id in (
                      select root_correlation_id from background_jobs
                      where job_type='FULL_PIPELINE'
                        and coalesce((payload_json->>'certification_authorized')::boolean,false)
                        and status in ('QUEUED','RECOVERING','RETRYING','RUNNING')
                        and root_correlation_id is not null
                    ), false)
                order by id
            """
            session_predicate = """
                select id from background_jobs job
                where job.status in ('QUEUED','RECOVERING') and job.run_after <= :now
                  and coalesce((job.payload_json->>'certification_authorized')::boolean,false)
                  and job.payload_json->>'certification_session_id' = :session_id
                  and (
                    job.job_type = 'FULL_PIPELINE'
                    or job.root_correlation_id in (
                      select root_correlation_id from background_jobs
                      where job_type='FULL_PIPELINE'
                        and coalesce((payload_json->>'certification_authorized')::boolean,false)
                        and payload_json->>'certification_session_id' = :session_id
                        and status in ('QUEUED','RECOVERING','RETRYING','RUNNING')
                        and root_correlation_id is not null
                    )
                  )
                order by id
            """
            queue["claimable_under_3124"] = list(
                connection.execute(text(old_predicate), {"now": generated_at}).scalars()
            )
            queue["claimable_under_416"] = list(
                connection.execute(text(predicate_416), {"now": generated_at}).scalars()
            )
            queue["claimable_under_prospective_session"] = list(
                connection.execute(
                    text(session_predicate),
                    {"now": generated_at, "session_id": prospective_session},
                ).scalars()
            )

            job_rows = connection.execute(
                text(
                    "select id, job_type, related_run_id, status, created_at, started_at, "
                    "completed_at, root_job_id, parent_job_id, triggered_by_job_id, "
                    "root_correlation_id, causation_id, worker_id, worker_instance_id, "
                    "locked_at, lease_expires_at from background_jobs where id = any(:ids) "
                    "order by id"
                ),
                {"ids": list(AFFECTED_JOB_IDS)},
            ).all()
            affected_jobs = []
            for row in job_rows:
                item = _row_dict(row)
                item["created_after_pipeline_149_cancelled"] = bool(
                    row.created_at and cancellation_at and row.created_at > cancellation_at
                )
                item["completed_after_pipeline_149_cancelled"] = bool(
                    row.completed_at and cancellation_at and row.completed_at > cancellation_at
                )
                item["future_influence"] = {
                    "creates_jobs_now": False,
                    "direct_setup_or_winner_consumer": False,
                    "historical_execution_audit": True,
                }
                affected_jobs.append(item)

            snapshot_rows = connection.execute(
                text(
                    "select id, run_id, company_id, ticker, as_of_session, cutoff_at, "
                    "created_at, comparison_snapshot_id, evidence_lineage_json "
                    "from ceri_score_snapshots where run_id=159 order by id"
                )
            ).all()
            snapshots = []
            all_source_ids: set[int] = set()
            for row in snapshot_rows:
                lineage = row.evidence_lineage_json or {}
                source_ids = sorted(
                    {
                        int(state["evidence_id"])
                        for state in lineage.get("evidence_states", [])
                        if state.get("evidence_type") == "SOURCE_RECORD"
                        and state.get("evidence_id") is not None
                    }
                )
                all_source_ids.update(source_ids)
                snapshots.append(
                    {
                        "id": row.id,
                        "run_id": row.run_id,
                        "company_id": row.company_id,
                        "ticker": row.ticker,
                        "as_of_session": row.as_of_session.isoformat(),
                        "cutoff_at": row.cutoff_at.isoformat(),
                        "created_at": row.created_at.isoformat(),
                        "comparison_snapshot_id": row.comparison_snapshot_id,
                        "source_record_ids": source_ids,
                        "source_record_count": len(source_ids),
                        "created_after_pipeline_149_cancelled": bool(
                            cancellation_at and row.created_at > cancellation_at
                        ),
                        "future_influence": {
                            "ceri_latest_snapshot_selection": True,
                            "future_ceri_comparison_baseline": True,
                            "direct_other_run_setup_handoff": False,
                            "direct_other_run_winner_capture": False,
                            "explicit_controlled_replay_source": True,
                        },
                    }
                )

            source_rows = connection.execute(
                text(
                    "select id, provider, dataset, published_at, retrieved_at "
                    "from ceri_source_records where id = any(:ids) order by id"
                ),
                {"ids": sorted(all_source_ids)},
            ).all()
            grouped: dict[tuple[str, str], list[Any]] = defaultdict(list)
            for row in source_rows:
                grouped[(row.provider, row.dataset)].append(row)
            source_summary = []
            for (provider, dataset), rows in sorted(grouped.items()):
                published = [row.published_at for row in rows if row.published_at is not None]
                retrieved = [row.retrieved_at for row in rows if row.retrieved_at is not None]
                source_summary.append(
                    {
                        "provider": provider,
                        "dataset": dataset,
                        "count": len(rows),
                        "min_id": min(row.id for row in rows),
                        "max_id": max(row.id for row in rows),
                        "published_at_min": min(published).isoformat() if published else None,
                        "published_at_max": max(published).isoformat() if published else None,
                        "retrieved_at_min": min(retrieved).isoformat() if retrieved else None,
                        "retrieved_at_max": max(retrieved).isoformat() if retrieved else None,
                    }
                )

            connection.rollback()
            return {
                "generated_at": generated_at.isoformat(),
                "transaction": "REPEATABLE READ, READ ONLY",
                "database": url.database,
                "schema_head": schema_head,
                "prospective_certification_session_id": prospective_session,
                "pipeline_149": _row_dict(pipeline),
                "queue_preflight": queue,
                "run_159_affected_jobs": affected_jobs,
                "run_159_ceri_snapshots": snapshots,
                "run_159_ceri_snapshot_ids": [row["id"] for row in snapshots],
                "source_record_lineage": {
                    "unique_ids": sorted(all_source_ids),
                    "unique_count": len(all_source_ids),
                    "rows_found": len(source_rows),
                    "sorted_id_sha256": hashlib.sha256(
                        json.dumps(sorted(all_source_ids), separators=(",", ":")).encode()
                    ).hexdigest(),
                    "provider_dataset_summary": source_summary,
                },
            }
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create the read-only Run 159 claim-fence inventory."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/forensics/run159_certification_claim_inventory.json"),
    )
    args = parser.parse_args()
    inventory = build_inventory()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "schema_head": inventory["schema_head"],
                "queue_preflight": inventory["queue_preflight"],
                "affected_jobs": len(inventory["run_159_affected_jobs"]),
                "ceri_snapshots": len(inventory["run_159_ceri_snapshots"]),
                "source_records": inventory["source_record_lineage"]["unique_count"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
