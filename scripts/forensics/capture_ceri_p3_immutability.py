"""Hash all directly run-linked rows for immutable CERI forensic runs."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

from app.db import engine


def _digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", type=int, action="append", required=True)
    args = parser.parse_args()
    run_ids = tuple(sorted(set(args.run_id)))
    tables: dict[str, object] = {}

    with engine.connect() as connection:
        pipeline_ids = tuple(
            connection.execute(
                text("SELECT id FROM pipeline_runs WHERE upload_run_id = ANY(:ids)"),
                {"ids": list(run_ids)},
            ).scalars()
        )
        job_ids = tuple(
            connection.execute(
                text("SELECT id FROM background_jobs WHERE related_run_id = ANY(:ids)"),
                {"ids": list(run_ids)},
            ).scalars()
        )
        manifest_ids = tuple(
            connection.execute(
                text("SELECT id FROM ceri_feature_source_manifests WHERE run_id = ANY(:ids)"),
                {"ids": list(run_ids)},
            ).scalars()
        )
        snapshot_ids = tuple(
            connection.execute(
                text("SELECT id FROM ceri_score_snapshots WHERE run_id = ANY(:ids)"),
                {"ids": list(run_ids)},
            ).scalars()
        )
        table_queries = {
            "upload_runs": ("t.id = ANY(:ids)", run_ids),
            "raw_company_rows": ("t.run_id = ANY(:ids)", run_ids),
            "pipeline_runs": ("t.upload_run_id = ANY(:ids)", run_ids),
            "pipeline_steps": ("t.pipeline_run_id = ANY(:ids)", pipeline_ids),
            "background_jobs": ("t.related_run_id = ANY(:ids)", run_ids),
            "ceri_feature_source_manifests": ("t.run_id = ANY(:ids)", run_ids),
            "ceri_feature_build_states": ("t.source_manifest_id = ANY(:ids)", manifest_ids),
            "ceri_score_snapshots": ("t.run_id = ANY(:ids)", run_ids),
            "ceri_change_events": (
                "t.from_snapshot_id = ANY(:ids) OR t.to_snapshot_id = ANY(:ids)",
                snapshot_ids,
            ),
            "ceri_alert_events": (
                "t.source_change_event_id IN ("
                "SELECT id FROM ceri_change_events "
                "WHERE from_snapshot_id = ANY(:ids) OR to_snapshot_id = ANY(:ids))",
                snapshot_ids,
            ),
        }
        for table_name, (predicate, identities) in table_queries.items():
            if not identities:
                continue
            statement = text(
                f"SELECT to_jsonb(t) AS body, t.xmin::text AS xmin "
                f'FROM "{table_name}" AS t WHERE {predicate}'
            )
            rows = [
                {"body": row.body, "xmin": row.xmin}
                for row in connection.execute(statement, {"ids": list(identities)})
            ]
            rows.sort(key=lambda row: _digest(row))
            tables[table_name] = {
                "rows": len(rows),
                "sha256": _digest(rows),
            }

    report = {
        "captured_at": datetime.now(UTC).isoformat(),
        "run_ids": run_ids,
        "pipeline_ids": pipeline_ids,
        "job_ids": job_ids,
        "manifest_ids": manifest_ids,
        "snapshot_ids": snapshot_ids,
        "tables": tables,
        "overall_sha256": _digest(tables),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
