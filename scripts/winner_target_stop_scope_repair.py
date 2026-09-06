"""Plan, apply, and verify the append-only 2026-09-06 target/stop repair."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text

from app.db import SessionLocal
from app.models.tables import WinnerTargetStopOutcome, WinnerTemporalValidityDecision
from app.services.winner_probability.target_stop_scope_repair_service import (
    apply_target_stop_scope_repair,
    build_target_stop_scope_repair_manifest,
    target_stop_scope_repair_hash,
)
from app.services.winner_probability.temporal_manifest_canonicalization import (
    canonical_manifest_bytes,
    canonicalize_manifest_value,
)

EXPECTED_REPAIR_COUNT = 23
PROTECTED_TABLES = (
    "winner_estimate_evidence_members",
    "winner_evidence_manifest_members",
    "winner_probability_estimates",
    "winner_cohort_generations",
    "winner_temporal_validity_decisions",
    "price_bars",
    "price_series_versions",
)


def plan(*, previous_canary_path: Path, execution_path: Path, output_dir: Path) -> Path:
    canary = json.loads(previous_canary_path.read_text(encoding="utf-8"))
    execution = json.loads(execution_path.read_text(encoding="utf-8"))
    prediction_ids = [int(item["prediction_id"]) for item in canary["manifest"]["outcomes"]]
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        ids = list(
            db.scalars(
                select(WinnerTargetStopOutcome.id)
                .where(WinnerTargetStopOutcome.prediction_id.in_(prediction_ids))
                .where(WinnerTargetStopOutcome.entry_model == "SIGNAL_CLOSE_DIAGNOSTIC")
                .where(WinnerTargetStopOutcome.horizon_sessions == 5)
                .where(WinnerTargetStopOutcome.evaluated_at == execution["executed_at"])
                .where(WinnerTargetStopOutcome.is_current_revision.is_(True))
                .order_by(WinnerTargetStopOutcome.id)
            )
        )
        if len(ids) != EXPECTED_REPAIR_COUNT:
            raise RuntimeError(f"expected 23 repair rows, found {len(ids)}")
        manifest_1 = build_target_stop_scope_repair_manifest(
            db, target_stop_ids=ids, incident=execution["request_key"]
        )
        manifest_2 = build_target_stop_scope_repair_manifest(
            db, target_stop_ids=ids, incident=execution["request_key"]
        )
        if canonical_manifest_bytes(manifest_1) != canonical_manifest_bytes(manifest_2):
            raise RuntimeError("repair manifest is not byte deterministic")
        preservation = _preservation_state(db, canary)
        payload: dict[str, Any] = {
            "schema": "swinglens-winner-target-stop-scope-repair-artifact-v1",
            "repair_manifest_hash": target_stop_scope_repair_hash(manifest_1),
            "byte_deterministic_regeneration": True,
            "previous_canary_manifest_hash": canary["reviewed_manifest_hash"],
            "previous_execution_artifact_hash": execution["artifact_hash"],
            "preservation_before": preservation,
            "manifest": manifest_1,
        }
        payload["artifact_hash"] = _payload_hash(payload)
        path = output_dir / f"repair_reviewed_{payload['artifact_hash']}.json"
        _write(path, payload)
        db.rollback()
    print(json.dumps(_summary(path, payload), indent=2, sort_keys=True))
    return path


def apply(
    *,
    artifact_path: Path,
    expected_hash: str,
    actor: str,
    request_key: str,
    approve_write: bool,
    output_dir: Path,
) -> Path:
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    if artifact["repair_manifest_hash"] != expected_hash:
        raise RuntimeError("command hash differs from reviewed repair artifact")
    with SessionLocal() as db:
        if _active_winner_jobs(db):
            raise RuntimeError("active Winner jobs make repair unsafe")
        result = apply_target_stop_scope_repair(
            db,
            artifact["manifest"],
            reviewed_manifest_hash=expected_hash,
            approve_write=approve_write,
            actor=actor,
            request_key=request_key,
        )
        db.commit()
    payload = canonicalize_manifest_value(
        {"schema": "swinglens-winner-target-stop-scope-repair-result-v1", **result.__dict__}
    )
    payload["artifact_hash"] = _payload_hash(payload)
    path = output_dir / f"repair_result_{payload['artifact_hash']}.json"
    _write(path, payload)
    print(json.dumps(_summary(path, payload), indent=2, sort_keys=True))
    return path


def verify(*, artifact_path: Path, previous_canary_path: Path, output_dir: Path) -> Path:
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    canary = json.loads(previous_canary_path.read_text(encoding="utf-8"))
    manifest = artifact["manifest"]
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        checks: dict[str, bool] = {}
        for record in manifest["records"]:
            old = db.get(WinnerTargetStopOutcome, int(record["target_stop_id"]))
            current = db.scalar(
                select(WinnerTargetStopOutcome)
                .where(WinnerTargetStopOutcome.prediction_id == record["prediction_id"])
                .where(
                    WinnerTargetStopOutcome.outcome_definition_id == record["outcome_definition_id"]
                )
                .where(WinnerTargetStopOutcome.is_current_revision.is_(True))
            )
            checks[f"old_preserved_{old.id}"] = bool(
                old.status == "MATURED"
                and old.revision == record["old_revision"]
                and not old.is_current_revision
                and old.source_bar_lineage_hash == record["old_state"]["source_bar_lineage_hash"]
            )
            checks[f"corrective_current_{old.id}"] = bool(
                current is not None
                and current.revision == record["expected_corrective_revision"]["revision"]
                and current.status == "PENDING"
                and current.forward_outcome_id == record["correct_forward_outcome_id"]
                and current.source_bar_lineage_hash is None
                and current.evaluated_at is None
                and (current.metadata_json or {}).get("repair_type")
                == "MATURATION_SCOPE_LEAK_CORRECTION"
            )
        preservation_after = _preservation_state(db, canary)
        checks["all_23_source_revisions_preserved"] = len(manifest["records"]) == 23
        checks["all_23_corrective_revisions_current"] = (
            sum(
                1
                for key, value in checks.items()
                if key.startswith("corrective_current_") and value
            )
            == 23
        )
        checks["protected_state_unchanged"] = artifact["preservation_before"] == preservation_after
        checks["active_winner_jobs_zero"] = _active_winner_jobs(db) == 0
        if not all(checks.values()):
            raise RuntimeError(f"repair verification failed: {checks}")
        payload: dict[str, Any] = {
            "schema": "swinglens-winner-target-stop-scope-repair-verification-v1",
            "repair_manifest_hash": artifact["repair_manifest_hash"],
            "checks": checks,
            "preservation_after": preservation_after,
        }
        payload["artifact_hash"] = _payload_hash(payload)
        path = output_dir / f"repair_verification_{payload['artifact_hash']}.json"
        _write(path, payload)
        db.rollback()
    print(json.dumps(_summary(path, payload), indent=2, sort_keys=True))
    return path


def _preservation_state(db, canary: dict[str, Any]) -> dict[str, Any]:
    next_open_target_ids = sorted(
        int(target["target_stop_outcome_id"])
        for outcome in canary["manifest"]["outcomes"]
        for target in outcome["target_stops"]
    )
    forward_ids = sorted(int(item["outcome_id"]) for item in canary["manifest"]["outcomes"])
    return {
        "protected_tables": {name: _table_hash(db, name) for name in PROTECTED_TABLES},
        "previous_next_open_forwards": _filtered_hash(
            db, "winner_forward_outcomes", "id", forward_ids
        ),
        "previous_next_open_target_stops": _filtered_hash(
            db, "winner_target_stop_outcomes", "id", next_open_target_ids
        ),
        "quarantine_count": _quarantine_count(db),
        "clbk_target_stops": _clbk_target_hash(db),
    }


def _table_hash(db, table: str) -> dict[str, Any]:
    return _query_hash(db, f"SELECT row_to_json(t)::text FROM {table} t ORDER BY id")


def _filtered_hash(db, table: str, column: str, ids: list[int]) -> dict[str, Any]:
    rendered = ",".join(str(value) for value in ids) or "NULL"
    return _query_hash(
        db,
        f"SELECT row_to_json(t)::text FROM {table} t WHERE {column} IN ({rendered}) ORDER BY id",
    )


def _query_hash(db, query: str) -> dict[str, Any]:
    digest = hashlib.sha256()
    count = 0
    for value in db.scalars(text(query).execution_options(stream_results=True, yield_per=5000)):
        digest.update(str(value).encode())
        digest.update(b"\n")
        count += 1
    return {"count": count, "sha256": digest.hexdigest()}


def _quarantine_count(db) -> int:
    latest = (
        select(
            WinnerTemporalValidityDecision.prediction_id,
            func.max(WinnerTemporalValidityDecision.validation_sequence).label("sequence"),
        )
        .group_by(WinnerTemporalValidityDecision.prediction_id)
        .subquery()
    )
    return int(
        db.scalar(
            select(func.count(WinnerTemporalValidityDecision.id))
            .join(
                latest,
                (latest.c.prediction_id == WinnerTemporalValidityDecision.prediction_id)
                & (latest.c.sequence == WinnerTemporalValidityDecision.validation_sequence),
            )
            .where(WinnerTemporalValidityDecision.evidence_eligible.is_(False))
        )
        or 0
    )


def _clbk_target_hash(db) -> dict[str, Any]:
    return _query_hash(
        db,
        "SELECT row_to_json(t)::text FROM winner_target_stop_outcomes t "
        "JOIN winner_prediction_snapshots p ON p.id=t.prediction_id "
        "WHERE p.ticker='CLBK' ORDER BY t.id",
    )


def _active_winner_jobs(db) -> int:
    return int(
        db.scalar(
            text(
                "SELECT count(*) FROM background_jobs "
                "WHERE status IN ('PENDING','RUNNING','RETRYING') AND job_type LIKE 'WINNER%'"
            )
        )
        or 0
    )


def _payload_hash(payload: dict[str, Any]) -> str:
    return target_stop_scope_repair_hash(
        {key: value for key, value in payload.items() if key != "artifact_hash"}
    )


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(path)
    path.write_bytes(canonical_manifest_bytes(payload) + b"\n")


def _summary(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        **{
            key: payload[key]
            for key in (
                "artifact_hash",
                "repair_manifest_hash",
                "repair_count",
                "repaired_count",
                "checks",
            )
            if key in payload
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    plan_parser = sub.add_parser("plan")
    plan_parser.add_argument("--previous-canary", type=Path, required=True)
    plan_parser.add_argument("--execution", type=Path, required=True)
    plan_parser.add_argument("--output-dir", type=Path, required=True)
    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("--artifact", type=Path, required=True)
    apply_parser.add_argument("--expected-hash", required=True)
    apply_parser.add_argument("--actor", required=True)
    apply_parser.add_argument("--request-key", required=True)
    apply_parser.add_argument("--approve-write", action="store_true")
    apply_parser.add_argument("--output-dir", type=Path, required=True)
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--artifact", type=Path, required=True)
    verify_parser.add_argument("--previous-canary", type=Path, required=True)
    verify_parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "plan":
        plan(
            previous_canary_path=args.previous_canary,
            execution_path=args.execution,
            output_dir=args.output_dir,
        )
    elif args.command == "apply":
        apply(
            artifact_path=args.artifact,
            expected_hash=args.expected_hash,
            actor=args.actor,
            request_key=args.request_key,
            approve_write=args.approve_write,
            output_dir=args.output_dir,
        )
    else:
        verify(
            artifact_path=args.artifact,
            previous_canary_path=args.previous_canary,
            output_dir=args.output_dir,
        )


if __name__ == "__main__":
    main()
