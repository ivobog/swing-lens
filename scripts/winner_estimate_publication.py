# ruff: noqa: E501
"""Build and verify a deterministic, non-executing Winner publication plan."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.db import SessionLocal
from app.services.winner_probability.estimate_publication_service import (
    DecisionReconstructionCategory,
    PublicationTransitionCategory,
    WinnerEstimatePublicationService,
    classify_decision_reconstruction,
    serving_id_snapshot,
    transition_manifest_hash,
)
from app.services.winner_probability.temporal_manifest_canonicalization import (
    canonical_manifest_bytes,
    canonicalize_manifest_value,
)

EXPECTED_HEAD = "0061_winner_estimate_policy"
CANDIDATE_SOURCE_VERSION = "cohort_baseline_v2_clean_candidate"
EXPECTED_GENERATION_ID = 11
EXPECTED_PREVIOUS_GENERATION_ID = 10
NO_CLEAN_REASON = "no_clean_evidence_at_original_decision_cutoff"


def plan(*, output_dir: Path) -> Path:
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        _preflight(db)
        rows = _transition_rows(db)
        candidate_hashes = {
            str(row["candidate_manifest_hash"]) for row in rows if row["candidate_manifest_hash"]
        }
        if len(candidate_hashes) != 1:
            raise RuntimeError(f"expected one candidate manifest hash, found {candidate_hashes}")
        candidate_manifest_hash = candidate_hashes.pop()
        records = [_record(row) for row in rows]
        _validate_records(records)
        serving_before_ids = _serving_ids(db)
        original_ids = {int(row["original_estimate_id"]) for row in records}
        candidate_ids = {int(row["candidate_estimate_id"]) for row in records}
        serving_after_ids = tuple(sorted((set(serving_before_ids) - original_ids) | candidate_ids))
        classifications = Counter(
            row["decision_reconstruction_category"]
            for row in records
            if row["decision_reconstruction_category"] is not None
        )
        transitions = Counter(row["transition_category"] for row in records)
        payload: dict[str, Any] = {
            "schema": "swinglens-winner-estimate-publication-v1",
            "publication_authorized": False,
            "generation": _generation(db, EXPECTED_GENERATION_ID),
            "previous_generation": _generation(db, EXPECTED_PREVIOUS_GENERATION_ID),
            "candidate_manifest_hash": candidate_manifest_hash,
            "candidate_count": len(records),
            "decision_time_count": sum(row["estimate_kind"] == "DECISION_TIME" for row in records),
            "latest_rescore_count": sum(
                row["estimate_kind"] == "LATEST_RESCORE" for row in records
            ),
            "decision_reconstruction_categories": dict(sorted(classifications.items())),
            "transition_categories": dict(sorted(transitions.items())),
            "no_replacement_count": 0,
            "quarantined_prediction_count": _quarantine_count(db),
            "clbk_candidate_evidence_count": _candidate_clbk_count(db),
            "candidate_membership_count": _candidate_membership_count(db),
            "serving_before": _id_snapshot(serving_before_ids),
            "serving_after": _id_snapshot(serving_after_ids),
            "serving_transition": {
                "removed_count": len(original_ids),
                "added_count": len(candidate_ids),
                "unchanged_count": len(set(serving_before_ids) - original_ids),
            },
            "protected": _protected_state(db),
            "records": records,
        }
        payload["artifact_hash"] = transition_manifest_hash(payload)
        path = output_dir / f"estimate_publication_{payload['artifact_hash']}.json"
        _write(path, payload)
        db.rollback()
    print(json.dumps(_summary(path, payload), indent=2, sort_keys=True))
    return path


def verify(*, artifact_path: Path, expected_hash: str, output_dir: Path) -> Path:
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    if artifact.get("artifact_hash") != expected_hash:
        raise RuntimeError("artifact does not identify the reviewed hash")
    if transition_manifest_hash(artifact) != expected_hash:
        raise RuntimeError("publication artifact hash mismatch")
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        _preflight(db)
        independent_rows = [_record(row) for row in _transition_rows(db)]
        serving_before = _serving_ids(db)
        old_ids = {int(row["original_estimate_id"]) for row in independent_rows}
        candidate_ids = {int(row["candidate_estimate_id"]) for row in independent_rows}
        checks = {
            "records_exact": independent_rows == artifact["records"],
            "candidate_count": len(independent_rows) == int(artifact["candidate_count"]),
            "decision_time_8887": sum(
                row["estimate_kind"] == "DECISION_TIME" for row in independent_rows
            )
            == 8887,
            "direct_contamination_1322": sum(
                row["decision_reconstruction_category"]
                == DecisionReconstructionCategory.DIRECTLY_CONTAMINATED
                for row in independent_rows
            )
            == 1322,
            "all_decision_candidates_insufficient": all(
                row["candidate_probability"] is None
                and row["candidate_evidence_grade"] == "Insufficient"
                and row["candidate_reason"] == NO_CLEAN_REASON
                and row["candidate_member_count"] == 0
                for row in independent_rows
                if row["estimate_kind"] == "DECISION_TIME"
            ),
            "latest_cutoffs_cover_evidence": all(
                row["candidate_max_evidence_matured_at"] is None
                or row["candidate_max_evidence_matured_at"] <= row["candidate_training_cutoff_at"]
                for row in independent_rows
                if row["estimate_kind"] == "LATEST_RESCORE"
            ),
            "one_to_one": len(old_ids) == len(candidate_ids) == len(independent_rows),
            "no_replacement_zero": int(artifact["no_replacement_count"]) == 0,
            "candidate_memberships_unchanged": _candidate_membership_count(db)
            == int(artifact["candidate_membership_count"]),
            "candidate_purity": _candidate_clbk_count(db) == 0
            and _candidate_temporal_impurity_count(db) == 0,
            "serving_before": _id_snapshot(serving_before) == artifact["serving_before"],
            "serving_after": _id_snapshot(
                tuple(sorted((set(serving_before) - old_ids) | candidate_ids))
            )
            == artifact["serving_after"],
            "protected_state": _protected_state(db) == artifact["protected"],
        }
        if not all(checks.values()):
            raise RuntimeError(f"publication verification failed: {checks}")
        result = {
            "schema": "swinglens-winner-estimate-publication-verification-v1",
            "reviewed_artifact_hash": expected_hash,
            "checks": checks,
        }
        result["artifact_hash"] = transition_manifest_hash(result)
        path = output_dir / f"estimate_publication_verification_{result['artifact_hash']}.json"
        _write(path, result)
        db.rollback()
    print(json.dumps({"path": str(path.resolve()), **result}, indent=2, sort_keys=True))
    return path


def simulate(
    *,
    artifact_path: Path,
    expected_hash: str,
    candidate_manifest_hash: str,
    output_dir: Path,
    approve_write: bool,
) -> Path:
    if not approve_write:
        raise PermissionError("simulation requires --approve-write")
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    if (
        artifact.get("artifact_hash") != expected_hash
        or transition_manifest_hash(artifact) != expected_hash
    ):
        raise RuntimeError("simulation artifact hash mismatch")
    if artifact.get("candidate_manifest_hash") != candidate_manifest_hash:
        raise RuntimeError("simulation candidate manifest hash mismatch")
    stages = (
        "generation_switch",
        "estimate_switch",
        "pointer_switch",
        "request_recorded",
    )
    rollback_checks: dict[str, bool] = {}
    for stage in stages:
        with SessionLocal() as db:
            _assert_disposable_simulation_database(db)

            def fail_at(observed_stage: str, target_stage: str = stage) -> None:
                if observed_stage == target_stage:
                    raise RuntimeError(f"injected failure after {target_stage}")

            try:
                WinnerEstimatePublicationService().publish(
                    db,
                    manifest=artifact,
                    reviewed_manifest_hash=expected_hash,
                    candidate_manifest_hash=candidate_manifest_hash,
                    actor="controlled-disposable-simulation",
                    request_key="winner-generation-11-publication-simulation",
                    approve_write=True,
                    stage_hook=fail_at,
                )
            except RuntimeError as exc:
                if str(exc) != f"injected failure after {stage}":
                    raise
                db.rollback()
            else:
                raise RuntimeError(f"failure injection did not fire after {stage}")
        with SessionLocal() as db:
            _assert_disposable_simulation_database(db)
            rollback_checks[stage] = _simulation_is_baseline(db, artifact)
            if not rollback_checks[stage]:
                raise RuntimeError(f"rollback after {stage} left partial publication state")

    with SessionLocal() as db:
        _assert_disposable_simulation_database(db)
        result = WinnerEstimatePublicationService().publish(
            db,
            manifest=artifact,
            reviewed_manifest_hash=expected_hash,
            candidate_manifest_hash=candidate_manifest_hash,
            actor="controlled-disposable-simulation",
            request_key="winner-generation-11-publication-simulation",
            approve_write=True,
        )
        db.commit()
    with SessionLocal() as db:
        _assert_disposable_simulation_database(db)
        replay = WinnerEstimatePublicationService().publish(
            db,
            manifest=artifact,
            reviewed_manifest_hash=expected_hash,
            candidate_manifest_hash=candidate_manifest_hash,
            actor="controlled-disposable-simulation",
            request_key="winner-generation-11-publication-simulation",
            approve_write=True,
        )
        checks = _simulation_checks(db, artifact)
        checks["idempotent_replay"] = replay == result
        if not all(checks.values()):
            raise RuntimeError(f"full publication simulation failed: {checks}")
        payload = {
            "schema": "swinglens-winner-estimate-publication-simulation-v1",
            "source_manifest_hash": expected_hash,
            "candidate_manifest_hash": candidate_manifest_hash,
            "rollback_checks": rollback_checks,
            "checks": checks,
            "result": result,
        }
        payload["artifact_hash"] = transition_manifest_hash(payload)
        path = output_dir / f"estimate_publication_simulation_{payload['artifact_hash']}.json"
        _write(path, payload)
        db.rollback()
    print(json.dumps({"path": str(path.resolve()), **payload}, indent=2, sort_keys=True))
    return path


def _transition_rows(db):
    return list(
        db.execute(
            text(
                """
                WITH candidates AS MATERIALIZED (
                  SELECT c.*,(c.metadata_json->>'reviewed_manifest_hash') candidate_manifest_hash
                  FROM winner_probability_estimates c
                  WHERE c.cohort_generation_id=:generation_id
                    AND c.source_version=:source_version
                    AND c.lifecycle_status='CANDIDATE'
                ), latest_temporal AS MATERIALIZED (
                  SELECT DISTINCT ON(prediction_id)
                    prediction_id,evidence_eligible,status
                  FROM winner_temporal_validity_decisions
                  ORDER BY prediction_id,validation_sequence DESC,id DESC
                ), original_members AS MATERIALIZED (
                  SELECT m.estimate_id,
                    count(*) member_count,
                    count(*) FILTER(WHERE lt.prediction_id IS NOT NULL AND NOT lt.evidence_eligible) invalid_member_count,
                    count(*) FILTER(WHERE lt.prediction_id IS NULL) unverifiable_member_count
                  FROM winner_estimate_evidence_members m
                  JOIN candidates c ON c.supersedes_estimate_id=m.estimate_id
                  LEFT JOIN latest_temporal lt ON lt.prediction_id=m.prediction_id
                  GROUP BY m.estimate_id
                ), candidate_members AS MATERIALIZED (
                  SELECT m.estimate_id,count(*) member_count,max(o.matured_at) max_evidence_matured_at
                  FROM winner_estimate_evidence_members m
                  JOIN candidates c ON c.id=m.estimate_id
                  JOIN winner_forward_outcomes o ON o.id=m.outcome_id
                  GROUP BY m.estimate_id
                )
                SELECT
                  o.id original_estimate_id,c.id candidate_estimate_id,
                  c.prediction_id,p.ticker,c.outcome_definition_id,c.estimate_kind,
                  o.lifecycle_status original_lifecycle,c.lifecycle_status candidate_lifecycle,
                  o.point_probability original_probability,c.point_probability candidate_probability,
                  o.evidence_manifest_hash original_evidence_hash,
                  c.evidence_manifest_hash candidate_evidence_hash,
                  c.evidence_grade candidate_evidence_grade,
                  c.reconstruction_category stored_reconstruction_category,
                  c.insufficient_reasons_json,c.source candidate_source,
                  c.source_version candidate_source_version,c.cohort_generation_id,
                  o.cohort_generation_id original_generation_id,
                  o.training_cutoff_at original_training_cutoff_at,
                  c.training_cutoff_at candidate_training_cutoff_at,
                  coalesce(om.member_count,0) original_member_count,
                  coalesce(om.invalid_member_count,0) invalid_member_count,
                  coalesce(om.unverifiable_member_count,0) unverifiable_member_count,
                  coalesce(cm.member_count,0) candidate_member_count,
                  cm.max_evidence_matured_at candidate_max_evidence_matured_at,
                  c.supersedes_estimate_id,c.candidate_manifest_hash
                FROM candidates c
                JOIN winner_probability_estimates o ON o.id=c.supersedes_estimate_id
                JOIN winner_prediction_snapshots p ON p.id=c.prediction_id
                LEFT JOIN original_members om ON om.estimate_id=o.id
                LEFT JOIN candidate_members cm ON cm.estimate_id=c.id
                ORDER BY o.id,c.id
                """
            ),
            {
                "generation_id": EXPECTED_GENERATION_ID,
                "source_version": CANDIDATE_SOURCE_VERSION,
            },
        ).mappings()
    )


def _record(row) -> dict[str, Any]:
    kind = str(row["estimate_kind"])
    decision_category = None
    reason = None
    if kind == "DECISION_TIME":
        decision_category = classify_decision_reconstruction(
            member_count=int(row["original_member_count"]),
            invalid_member_count=int(row["invalid_member_count"]),
            unverifiable_member_count=int(row["unverifiable_member_count"]),
        )
        transition = PublicationTransitionCategory.DECISION_TIME_TO_INSUFFICIENT
        reasons = list(row["insufficient_reasons_json"] or ())
        reason = reasons[0] if len(reasons) == 1 else None
    elif row["candidate_probability"] is None:
        transition = PublicationTransitionCategory.LATEST_RESCORE_TO_INSUFFICIENT
        reasons = list(row["insufficient_reasons_json"] or ())
        reason = reasons[0] if len(reasons) == 1 else None
    else:
        transition = PublicationTransitionCategory.LATEST_RESCORE_TO_CLEAN_COHORT
    return canonicalize_manifest_value(
        {
            "logical_identity": {
                "prediction_id": int(row["prediction_id"]),
                "outcome_definition_id": int(row["outcome_definition_id"]),
                "estimate_kind": kind,
            },
            "ticker": row["ticker"],
            "original_estimate_id": int(row["original_estimate_id"]),
            "candidate_estimate_id": int(row["candidate_estimate_id"]),
            "original_lifecycle": row["original_lifecycle"],
            "candidate_lifecycle": row["candidate_lifecycle"],
            "estimate_kind": kind,
            "original_probability": row["original_probability"],
            "candidate_probability": row["candidate_probability"],
            "candidate_evidence_grade": row["candidate_evidence_grade"],
            "candidate_reason": reason,
            "original_evidence_hash": row["original_evidence_hash"],
            "candidate_evidence_hash": row["candidate_evidence_hash"],
            "original_member_count": int(row["original_member_count"]),
            "old_invalid_member_count": int(row["invalid_member_count"]),
            "old_unverifiable_member_count": int(row["unverifiable_member_count"]),
            "candidate_member_count": int(row["candidate_member_count"]),
            "original_training_cutoff_at": row["original_training_cutoff_at"],
            "candidate_training_cutoff_at": row["candidate_training_cutoff_at"],
            "candidate_max_evidence_matured_at": row["candidate_max_evidence_matured_at"],
            "original_generation_id": row["original_generation_id"],
            "target_generation_id": int(row["cohort_generation_id"]),
            "supersedes_estimate_id": int(row["supersedes_estimate_id"]),
            "decision_reconstruction_category": decision_category,
            "stored_reconstruction_category": row["stored_reconstruction_category"],
            "transition_category": transition,
        }
    )


def _validate_records(records) -> None:
    if len(records) != 18983:
        raise RuntimeError(f"expected 18983 replacements, found {len(records)}")
    old_ids = {int(row["original_estimate_id"]) for row in records}
    candidate_ids = {int(row["candidate_estimate_id"]) for row in records}
    if len(old_ids) != len(records) or len(candidate_ids) != len(records):
        raise RuntimeError("replacement lineage is not one-to-one")
    for row in records:
        if row["supersedes_estimate_id"] != row["original_estimate_id"]:
            raise RuntimeError("candidate supersession lineage mismatch")
        if row["stored_reconstruction_category"] != row["decision_reconstruction_category"]:
            raise RuntimeError("stored reconstruction category disagrees with evidence census")
        if row["original_lifecycle"] != "PUBLISHED" or row["candidate_lifecycle"] != "CANDIDATE":
            raise RuntimeError("estimate lifecycle drifted")
        if row["estimate_kind"] == "DECISION_TIME" and not (
            row["candidate_probability"] is None
            and row["candidate_evidence_grade"] == "Insufficient"
            and row["candidate_reason"] == NO_CLEAN_REASON
            and row["candidate_member_count"] == 0
        ):
            raise RuntimeError("decision-time replacement fabricates historical evidence")


def _generation(db, generation_id: int) -> dict[str, Any]:
    row = (
        db.execute(
            text(
                "SELECT id,refresh_state_id,generation_key,status,published_at,training_cutoff_at,"
                "watermark_hash,root_manifest_hash,evidence_row_count FROM winner_cohort_generations WHERE id=:id"
            ),
            {"id": generation_id},
        )
        .mappings()
        .one()
    )
    return canonicalize_manifest_value(dict(row))


def _serving_ids(db) -> tuple[int, ...]:
    return tuple(
        int(value)
        for value in db.scalars(
            text(
                """
                SELECT e.id FROM winner_probability_estimates e
                WHERE e.lifecycle_status='PUBLISHED'
                  AND e.estimate_kind IN ('DECISION_TIME','LATEST_RESCORE','AS_OF_REPLAY')
                  AND (e.cohort_generation_id IS NULL OR EXISTS(
                    SELECT 1 FROM winner_cohort_generations g
                    WHERE g.id=e.cohort_generation_id AND g.status='PUBLISHED'
                  ))
                ORDER BY e.id
                """
            )
        )
    )


def _id_snapshot(ids) -> dict[str, Any]:
    digest = hashlib.sha256()
    for value in ids:
        digest.update(str(int(value)).encode())
        digest.update(b"\n")
    return {"count": len(ids), "sha256": digest.hexdigest()}


def _quarantine_count(db) -> int:
    return int(
        db.scalar(
            text(
                "WITH latest AS (SELECT DISTINCT ON(prediction_id) prediction_id,evidence_eligible FROM winner_temporal_validity_decisions ORDER BY prediction_id,validation_sequence DESC,id DESC) SELECT count(*) FROM latest WHERE NOT evidence_eligible"
            )
        )
        or 0
    )


def _candidate_membership_count(db) -> int:
    return int(
        db.scalar(
            text(
                "SELECT count(*) FROM winner_estimate_evidence_members m JOIN winner_probability_estimates e ON e.id=m.estimate_id WHERE e.cohort_generation_id=:g AND e.source_version=:v"
            ),
            {"g": EXPECTED_GENERATION_ID, "v": CANDIDATE_SOURCE_VERSION},
        )
        or 0
    )


def _candidate_clbk_count(db) -> int:
    return int(
        db.scalar(
            text(
                "SELECT count(*) FROM winner_estimate_evidence_members m JOIN winner_probability_estimates e ON e.id=m.estimate_id JOIN winner_prediction_snapshots p ON p.id=m.prediction_id WHERE e.cohort_generation_id=:g AND e.source_version=:v AND upper(p.ticker)='CLBK'"
            ),
            {"g": EXPECTED_GENERATION_ID, "v": CANDIDATE_SOURCE_VERSION},
        )
        or 0
    )


def _candidate_temporal_impurity_count(db) -> int:
    return int(
        db.scalar(
            text(
                "WITH latest AS (SELECT DISTINCT ON(prediction_id) prediction_id,evidence_eligible FROM winner_temporal_validity_decisions ORDER BY prediction_id,validation_sequence DESC,id DESC) SELECT count(*) FROM winner_estimate_evidence_members m JOIN winner_probability_estimates e ON e.id=m.estimate_id LEFT JOIN latest l ON l.prediction_id=m.prediction_id WHERE e.cohort_generation_id=:g AND e.source_version=:v AND (l.prediction_id IS NULL OR NOT l.evidence_eligible)"
            ),
            {"g": EXPECTED_GENERATION_ID, "v": CANDIDATE_SOURCE_VERSION},
        )
        or 0
    )


def _protected_state(db) -> dict[str, Any]:
    tables = (
        "winner_prediction_snapshots",
        "winner_forward_outcomes",
        "winner_target_stop_outcomes",
        "winner_temporal_validity_decisions",
        "price_bars",
        "price_series_versions",
        "winner_market_data_obligations",
    )
    return {
        "table_counts": {
            table: int(db.scalar(text(f"SELECT count(*) FROM {table}")) or 0) for table in tables
        },
        "generation_10": _generation(db, 10),
        "generation_11": _generation(db, 11),
        "candidate_count": int(
            db.scalar(
                text(
                    "SELECT count(*) FROM winner_probability_estimates WHERE cohort_generation_id=11 AND source_version=:v AND lifecycle_status='CANDIDATE'"
                ),
                {"v": CANDIDATE_SOURCE_VERSION},
            )
            or 0
        ),
        "candidate_membership_count": _candidate_membership_count(db),
        "publication_request_count": int(
            db.scalar(text("SELECT count(*) FROM winner_estimate_publication_requests")) or 0
        ),
    }


def _preflight(db) -> None:
    heads = tuple(db.scalars(text("SELECT version_num FROM alembic_version ORDER BY 1")))
    if heads != (EXPECTED_HEAD,):
        raise RuntimeError(f"expected {EXPECTED_HEAD}, found {heads}")
    if int(
        db.scalar(
            text(
                "SELECT count(*) FROM background_jobs WHERE job_type LIKE 'WINNER%' AND status IN ('QUEUED','RUNNING','RECOVERING')"
            )
        )
        or 0
    ):
        raise RuntimeError("active Winner jobs detected")
    generation = _generation(db, 11)
    previous = _generation(db, 10)
    if generation["status"] != "READY" or generation["published_at"] is not None:
        raise RuntimeError("Generation 11 is not READY and unpublished")
    if previous["status"] != "PUBLISHED":
        raise RuntimeError("Generation 10 is not PUBLISHED")


def _assert_disposable_simulation_database(db) -> None:
    database_name = str(db.scalar(text("SELECT current_database()")))
    if not database_name.startswith("swinglens_policy_sim_"):
        raise RuntimeError(
            "publication simulation is restricted to swinglens_policy_sim_* databases"
        )


def _simulation_is_baseline(db, artifact) -> bool:
    generation = _generation(db, 11)
    previous = _generation(db, 10)
    candidate_count = int(
        db.scalar(
            text(
                "SELECT count(*) FROM winner_probability_estimates "
                "WHERE cohort_generation_id=11 AND lifecycle_status='CANDIDATE'"
            )
        )
        or 0
    )
    superseded_originals = int(
        db.scalar(
            text(
                "SELECT count(*) FROM winner_probability_estimates "
                "WHERE id=ANY(:ids) AND lifecycle_status='SUPERSEDED'"
            ),
            {"ids": [int(r["original_estimate_id"]) for r in artifact["records"]]},
        )
        or 0
    )
    requests = int(
        db.scalar(text("SELECT count(*) FROM winner_estimate_publication_requests")) or 0
    )
    return (
        generation["status"] == "READY"
        and generation["published_at"] is None
        and previous["status"] == "PUBLISHED"
        and candidate_count == int(artifact["candidate_count"])
        and superseded_originals == 0
        and requests == 0
        and _id_snapshot(_serving_ids(db)) == artifact["serving_before"]
    )


def _simulation_checks(db, artifact) -> dict[str, bool]:
    decision = (
        db.execute(
            text(
                """
            SELECT count(*) total,
              count(*) FILTER(WHERE c.lifecycle_status='PUBLISHED'
                AND c.point_probability IS NULL
                AND c.evidence_grade='Insufficient'
                AND c.insufficient_reasons_json ? :reason) clean_insufficient,
              count(*) FILTER(WHERE o.lifecycle_status='SUPERSEDED'
                AND o.point_probability IS NOT NULL) old_numeric_auditable
            FROM winner_probability_estimates c
            JOIN winner_probability_estimates o ON o.id=c.supersedes_estimate_id
            WHERE c.cohort_generation_id=11 AND c.estimate_kind='DECISION_TIME'
            """
            ),
            {"reason": NO_CLEAN_REASON},
        )
        .mappings()
        .one()
    )
    latest = (
        db.execute(
            text(
                """
            SELECT
              count(*) FILTER(WHERE point_probability IS NOT NULL) numeric,
              count(*) FILTER(WHERE point_probability IS NULL
                AND evidence_grade='Insufficient') insufficient
            FROM winner_probability_estimates
            WHERE cohort_generation_id=11 AND estimate_kind='LATEST_RESCORE'
              AND lifecycle_status='PUBLISHED'
            """
            )
        )
        .mappings()
        .one()
    )
    states = (
        db.execute(
            text(
                """
            SELECT
              (SELECT status FROM winner_cohort_generations WHERE id=10) old_status,
              (SELECT status FROM winner_cohort_generations WHERE id=11) new_status,
              (SELECT count(*) FROM winner_cohort_refresh_state
               WHERE published_generation_id=11) new_pointer_count,
              (SELECT count(*) FROM winner_cohort_refresh_state
               WHERE published_generation_id=10) old_pointer_count
            """
            )
        )
        .mappings()
        .one()
    )
    serving = serving_id_snapshot(db)
    return {
        "decision_time_8887_serves_insufficient": int(decision["total"]) == 8887
        and int(decision["clean_insufficient"]) == 8887,
        "old_decision_numeric_auditable": int(decision["old_numeric_auditable"]) == 8887,
        "latest_rescore_9966_numeric": int(latest["numeric"]) == 9966,
        "latest_rescore_130_insufficient": int(latest["insufficient"]) == 130,
        "generation_transition": states["old_status"] == "SUPERSEDED"
        and states["new_status"] == "PUBLISHED",
        "pointer_transition": int(states["old_pointer_count"]) == 0
        and int(states["new_pointer_count"]) == 1,
        "serving_set_exact": _id_snapshot(serving["ids"]) == artifact["serving_after"],
        "request_recorded_once": int(
            db.scalar(text("SELECT count(*) FROM winner_estimate_publication_requests")) or 0
        )
        == 1,
    }


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = canonical_manifest_bytes(payload) + b"\n"
    if path.exists() and path.read_bytes() != encoded:
        raise FileExistsError(path)
    if not path.exists():
        path.write_bytes(encoded)


def _summary(path, payload):
    return {
        "path": str(path.resolve()),
        "artifact_hash": payload["artifact_hash"],
        "candidate_count": payload.get("candidate_count"),
        "decision_reconstruction_categories": payload.get("decision_reconstruction_categories"),
        "transition_categories": payload.get("transition_categories"),
        "serving_before": payload.get("serving_before"),
        "serving_after": payload.get("serving_after"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("plan", "verify", "simulate"))
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--expected-hash")
    parser.add_argument("--candidate-manifest-hash")
    parser.add_argument("--approve-write", action="store_true")
    parser.add_argument(
        "--output-dir", type=Path, default=Path("output/winner_estimate_publication")
    )
    args = parser.parse_args()
    if args.command == "plan":
        plan(output_dir=args.output_dir)
    elif args.command == "verify":
        if args.artifact is None or not args.expected_hash:
            parser.error("verify requires --artifact and --expected-hash")
        verify(
            artifact_path=args.artifact,
            expected_hash=args.expected_hash,
            output_dir=args.output_dir,
        )
    else:
        if args.artifact is None or not args.expected_hash or not args.candidate_manifest_hash:
            parser.error(
                "simulate requires --artifact, --expected-hash, and --candidate-manifest-hash"
            )
        simulate(
            artifact_path=args.artifact,
            expected_hash=args.expected_hash,
            candidate_manifest_hash=args.candidate_manifest_hash,
            output_dir=args.output_dir,
            approve_write=args.approve_write,
        )


if __name__ == "__main__":
    main()
