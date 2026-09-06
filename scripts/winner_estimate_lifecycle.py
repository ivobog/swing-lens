"""Controlled Winner estimate-lifecycle backfill planning and verification."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.db import SessionLocal
from app.services.winner_probability.temporal_manifest_canonicalization import (
    canonical_manifest_bytes,
    canonicalize_manifest_value,
)

PRE_MIGRATION_HEAD = "0059_winner_market_data_obligations"
POST_MIGRATION_HEAD = "0060_winner_estimate_lifecycle"
PROTECTED_TABLES = (
    "winner_estimate_evidence_members",
    "winner_evidence_manifest_members",
    "winner_cohort_generations",
    "winner_cohort_statistics",
    "winner_temporal_validity_decisions",
    "winner_prediction_snapshots",
    "winner_forward_outcomes",
    "winner_target_stop_outcomes",
    "winner_market_data_obligations",
    "price_bars",
    "price_series_versions",
)


def plan(*, output_dir: Path) -> Path:
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        _require_head(db, PRE_MIGRATION_HEAD)
        _require_stopped(db)
        rows = _manifest_rows(db, post_migration=False)
        independent = _independent_expected_ids(db)
        published_ids = tuple(
            row["estimate_id"] for row in rows if row["new_lifecycle"] == "PUBLISHED"
        )
        superseded_ids = tuple(
            row["estimate_id"] for row in rows if row["new_lifecycle"] == "SUPERSEDED"
        )
        if published_ids != independent["published_ids"]:
            raise RuntimeError("service/independent PUBLISHED backfill sets differ")
        if superseded_ids != independent["superseded_ids"]:
            raise RuntimeError("service/independent SUPERSEDED backfill sets differ")
        payload: dict[str, Any] = {
            "schema": "swinglens-winner-estimate-lifecycle-backfill-v1",
            "database_head": PRE_MIGRATION_HEAD,
            "snapshot_at": db.scalar(text("SELECT transaction_timestamp()")),
            "estimate_count": len(rows),
            "published_count": len(published_ids),
            "superseded_count": len(superseded_ids),
            "candidate_count": 0,
            "raw_journal_divergence_ids": list(_raw_journal_divergence_ids(db)),
            "generation_10": _generation(db, 10),
            "generation_11": _generation(db, 11),
            "estimate_business_hash": _estimate_business_hash(db),
            "protected_hashes": {table: _table_hash(db, table) for table in PROTECTED_TABLES},
            "rows": rows,
        }
        payload["artifact_hash"] = _artifact_hash(payload)
        path = output_dir / f"lifecycle_backfill_{payload['artifact_hash']}.json"
        _write(path, payload)
        db.rollback()
    print(json.dumps(_summary(path, payload), indent=2, sort_keys=True))
    return path


def verify(*, artifact_path: Path, expected_hash: str, output_dir: Path) -> Path:
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    if artifact.get("artifact_hash") != expected_hash or _artifact_hash(artifact) != expected_hash:
        raise ValueError("reviewed lifecycle manifest hash mismatch")
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        _require_head(db, POST_MIGRATION_HEAD)
        _require_stopped(db)
        rows = _manifest_rows(db, post_migration=True)
        expected = artifact["rows"]
        if rows != expected:
            raise RuntimeError("post-migration lifecycle rows differ from reviewed manifest")
        checks = {
            "estimate_count": len(rows) == int(artifact["estimate_count"]),
            "published_count": sum(r["new_lifecycle"] == "PUBLISHED" for r in rows)
            == int(artifact["published_count"]),
            "superseded_count": sum(r["new_lifecycle"] == "SUPERSEDED" for r in rows)
            == int(artifact["superseded_count"]),
            "candidate_count": sum(r["new_lifecycle"] == "CANDIDATE" for r in rows) == 0,
            "estimate_business_hash": _estimate_business_hash(db)
            == artifact["estimate_business_hash"],
            "protected_hashes": {
                table: _table_hash(db, table) for table in PROTECTED_TABLES
            }
            == artifact["protected_hashes"],
            "generation_10": _generation(db, 10) == artifact["generation_10"],
            "generation_11": _generation(db, 11) == artifact["generation_11"],
        }
        if not all(checks.values()):
            raise RuntimeError(f"lifecycle verification failed: {checks}")
        payload: dict[str, Any] = {
            "schema": "swinglens-winner-estimate-lifecycle-verification-v1",
            "reviewed_artifact_hash": expected_hash,
            "database_head": POST_MIGRATION_HEAD,
            "checks": checks,
            "published_count": artifact["published_count"],
            "superseded_count": artifact["superseded_count"],
            "candidate_count": 0,
        }
        payload["artifact_hash"] = _artifact_hash(payload)
        path = output_dir / f"lifecycle_verification_{payload['artifact_hash']}.json"
        _write(path, payload)
        db.rollback()
    print(json.dumps(_summary(path, payload), indent=2, sort_keys=True))
    return path


def _manifest_rows(db, *, post_migration: bool) -> list[dict[str, Any]]:
    lifecycle_columns = (
        "e.lifecycle_status, e.published_at, e.superseded_at,"
        if post_migration
        else "NULL::text lifecycle_status, NULL::timestamptz published_at, "
        "NULL::timestamptz superseded_at,"
    )
    rows = db.execute(
        text(
            f"""
            SELECT e.id estimate_id, e.prediction_id, e.outcome_definition_id,
              e.estimate_kind, e.source_version, e.training_cutoff_at,
              e.cohort_generation_id, g.status generation_status, e.created_at,
              {lifecycle_columns}
              CASE
                WHEN e.cohort_generation_id IS NULL THEN 'PUBLISHED'
                WHEN g.status='PUBLISHED' THEN 'PUBLISHED'
                ELSE 'SUPERSEDED'
              END expected_lifecycle,
              CASE
                WHEN e.cohort_generation_id IS NULL
                  THEN 'LEGACY_NON_GENERATION_TIME_ADDRESSABLE'
                WHEN g.status='PUBLISHED' THEN 'PUBLISHED_SOURCE_GENERATION'
                ELSE 'NON_PUBLISHED_SOURCE_GENERATION'
              END reason
            FROM winner_probability_estimates e
            LEFT JOIN winner_cohort_generations g ON g.id=e.cohort_generation_id
            ORDER BY e.id
            """
        )
    ).mappings()
    result = []
    for row in rows:
        expected = str(row["expected_lifecycle"])
        if post_migration and row["lifecycle_status"] != expected:
            raise RuntimeError(f"estimate {row['estimate_id']} lifecycle differs from plan")
        result.append(
            canonicalize_manifest_value(
                {
                    "estimate_id": row["estimate_id"],
                    "logical_lineage": {
                        "prediction_id": row["prediction_id"],
                        "outcome_definition_id": row["outcome_definition_id"],
                        "estimate_kind": row["estimate_kind"],
                        "source_version": row["source_version"],
                        "training_cutoff_at": row["training_cutoff_at"],
                    },
                    "cohort_generation_id": row["cohort_generation_id"],
                    "generation_status": row["generation_status"],
                    "legacy_contract_serving": expected == "PUBLISHED",
                    "new_lifecycle": expected,
                    "reason": row["reason"],
                }
            )
        )
    return result


def _independent_expected_ids(db) -> dict[str, tuple[int, ...]]:
    base = """
      FROM winner_probability_estimates e
      LEFT JOIN winner_cohort_generations g ON g.id=e.cohort_generation_id
    """
    published = tuple(
        db.scalars(
            text(
                "SELECT e.id " + base
                + " WHERE e.cohort_generation_id IS NULL OR g.status='PUBLISHED' ORDER BY e.id"
            )
        )
    )
    superseded = tuple(
        db.scalars(
            text(
                "SELECT e.id " + base
                + " WHERE e.cohort_generation_id IS NOT NULL "
                "AND g.status<>'PUBLISHED' ORDER BY e.id"
            )
        )
    )
    return {"published_ids": published, "superseded_ids": superseded}


def _raw_journal_divergence_ids(db) -> tuple[int, ...]:
    return tuple(
        db.scalars(
            text(
                """
                WITH newest AS (
                  SELECT DISTINCT ON(prediction_id) id,cohort_generation_id
                  FROM winner_probability_estimates
                  ORDER BY prediction_id,created_at DESC,id DESC
                )
                SELECT n.id FROM newest n
                JOIN winner_cohort_generations g ON g.id=n.cohort_generation_id
                WHERE g.status<>'PUBLISHED' ORDER BY n.id
                """
            )
        )
    )


def _generation(db, generation_id: int) -> dict[str, Any]:
    row = db.execute(
        text(
            "SELECT id,generation_key,status,published_at,root_manifest_hash,evidence_row_count "
            "FROM winner_cohort_generations WHERE id=:id"
        ),
        {"id": generation_id},
    ).mappings().one()
    return canonicalize_manifest_value(dict(row))


def _require_head(db, expected: str) -> None:
    heads = tuple(db.scalars(text("SELECT version_num FROM alembic_version ORDER BY 1")))
    if heads != (expected,):
        raise RuntimeError(f"expected one Alembic head {expected}, found {heads}")


def _require_stopped(db) -> None:
    active = int(
        db.scalar(
            text(
                "SELECT count(*) FROM background_jobs WHERE job_type LIKE 'WINNER%' "
                "AND status IN ('QUEUED','RUNNING','RECOVERING')"
            )
        )
        or 0
    )
    if active:
        raise RuntimeError(f"active Winner jobs detected: {active}")


def _estimate_business_hash(db) -> dict[str, Any]:
    return _hash_query(
        db,
        "SELECT (to_jsonb(e)-ARRAY['lifecycle_status','published_at','superseded_at'])::text "
        "FROM winner_probability_estimates e ORDER BY e.id",
    )


def _table_hash(db, table: str) -> dict[str, Any]:
    return _hash_query(db, f"SELECT row_to_json(t)::text FROM {table} t ORDER BY id")


def _hash_query(db, query: str) -> dict[str, Any]:
    digest = hashlib.sha256()
    count = 0
    for value in db.scalars(text(query).execution_options(stream_results=True, yield_per=5000)):
        digest.update(str(value).encode("utf-8"))
        digest.update(b"\n")
        count += 1
    return {"count": count, "sha256": digest.hexdigest()}


def _artifact_hash(payload: dict[str, Any]) -> str:
    value = {
        key: val
        for key, val in payload.items()
        if key not in {"artifact_hash", "snapshot_at"}
    }
    return hashlib.sha256(canonical_manifest_bytes(value)).hexdigest()


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = canonical_manifest_bytes(payload) + b"\n"
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if _artifact_hash(existing) != _artifact_hash(payload):
            raise FileExistsError(path)
        return
    path.write_bytes(encoded)


def _summary(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "artifact_hash": payload["artifact_hash"],
        "estimate_count": payload.get("estimate_count"),
        "published_count": payload.get("published_count"),
        "superseded_count": payload.get("superseded_count"),
        "checks": payload.get("checks"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    plan_parser = sub.add_parser("plan")
    plan_parser.add_argument("--output-dir", type=Path, required=True)
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--artifact", type=Path, required=True)
    verify_parser.add_argument("--expected-hash", required=True)
    verify_parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "plan":
        plan(output_dir=args.output_dir)
    else:
        verify(
            artifact_path=args.artifact,
            expected_hash=args.expected_hash,
            output_dir=args.output_dir,
        )


if __name__ == "__main__":
    main()
