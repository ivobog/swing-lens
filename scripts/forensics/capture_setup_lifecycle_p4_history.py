"""Hash immutable rows directly owned by historical pipeline runs."""

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


def _rows(connection, table: str, predicate: str, run_id: int) -> dict[str, object]:
    statement = text(
        f'SELECT to_jsonb(t) AS body, t.xmin::text AS xmin FROM "{table}" AS t WHERE {predicate}'
    )
    rows = [
        {"body": row.body, "xmin": row.xmin}
        for row in connection.execute(statement, {"run_id": run_id})
    ]
    rows.sort(key=_digest)
    return {"rows": len(rows), "sha256": _digest(rows)}


def _capture_run(connection, run_id: int) -> dict[str, object]:
    predicates = {
        "upload_runs": "t.id = :run_id",
        "raw_company_rows": "t.run_id = :run_id",
        "pipeline_runs": "t.upload_run_id = :run_id",
        "pipeline_steps": (
            "t.pipeline_run_id IN (SELECT id FROM pipeline_runs WHERE upload_run_id = :run_id)"
        ),
        "background_jobs": "t.related_run_id = :run_id",
        "ceri_feature_source_manifests": "t.run_id = :run_id",
        "ceri_feature_build_states": (
            "t.source_manifest_id IN (SELECT id FROM ceri_feature_source_manifests "
            "WHERE run_id = :run_id)"
        ),
        "ceri_score_snapshots": "t.run_id = :run_id",
        "setup_signal_snapshots": "t.run_id = :run_id",
        "setup_signal_snapshot_selection_events": "t.run_id = :run_id",
        "setup_lifecycle_evaluation_runs": "t.source_run_id = :run_id",
        "setup_lifecycle_evaluation_evidence": (
            "t.evaluation_run_id IN (SELECT id FROM setup_lifecycle_evaluation_runs "
            "WHERE source_run_id = :run_id)"
        ),
        "setup_lifecycle_transition_evidence": (
            "t.evaluation_evidence_id IN (SELECT id FROM setup_lifecycle_evaluation_evidence "
            "WHERE evaluation_run_id IN (SELECT id FROM setup_lifecycle_evaluation_runs "
            "WHERE source_run_id = :run_id))"
        ),
        "setup_lifecycle_events": (
            "t.evaluation_run_id IN (SELECT id FROM setup_lifecycle_evaluation_runs "
            "WHERE source_run_id = :run_id) OR t.snapshot_id IN "
            "(SELECT id FROM setup_signal_snapshots WHERE run_id = :run_id)"
        ),
        "setup_lifecycle_episodes": (
            "t.opening_snapshot_id IN (SELECT id FROM setup_signal_snapshots "
            "WHERE run_id = :run_id) OR t.current_snapshot_id IN "
            "(SELECT id FROM setup_signal_snapshots WHERE run_id = :run_id) OR "
            "t.closing_snapshot_id IN (SELECT id FROM setup_signal_snapshots "
            "WHERE run_id = :run_id)"
        ),
        "winner_prediction_snapshots": "t.run_id = :run_id",
        "winner_processing_runs": "t.run_id = :run_id",
    }
    tables = {
        table: _rows(connection, table, predicate, run_id)
        for table, predicate in predicates.items()
    }
    return {"tables": tables, "overall_sha256": _digest(tables)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", type=int, action="append", required=True)
    args = parser.parse_args()
    run_ids = tuple(sorted(set(args.run_id)))
    with engine.connect() as connection:
        runs = {str(run_id): _capture_run(connection, run_id) for run_id in run_ids}
    report = {
        "captured_at": datetime.now(UTC).isoformat(),
        "run_ids": run_ids,
        "runs": runs,
        "overall_sha256": _digest(runs),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
