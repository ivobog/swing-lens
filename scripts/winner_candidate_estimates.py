# ruff: noqa: E501
"""Plan, persist, and independently verify non-serving Generation-11 estimates."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import insert, select, text

from app.db import SessionLocal
from app.models.tables import (
    EstimateLifecycleStatus,
    EvidenceGrade,
    WinnerCohortDefinition,
    WinnerCohortStatistic,
    WinnerEvidenceManifest,
    WinnerProbabilityEstimate,
)
from app.services.winner_probability.cohort_definition import CohortDefinitionService
from app.services.winner_probability.config import load_winner_probability_config
from app.services.winner_probability.estimate_lifecycle import estimate_is_serving
from app.services.winner_probability.evidence_manifest_service import _hash_payload
from app.services.winner_probability.temporal_manifest_canonicalization import (
    canonical_manifest_bytes,
    canonicalize_manifest_value,
)

EXPECTED_HEAD = "0061_winner_estimate_policy"
GENERATION_ID = 11
SOURCE_VERSION = "cohort_baseline_v2_clean_candidate"
EXPECTED_GENERATION_KEY = "d47833be5025a3ea00f242d4e14e495ccd9da62f3d608fde98a12cdcd43cc370"
EXPECTED_ROOT_HASH = "1db8c8557a4941b7b8372c912d0ad3ca6eae75128a50f8280e413f26539c99ec"


def plan(*, output_dir: Path) -> Path:
    config = load_winner_probability_config()
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        _preflight(db)
        generation_state = _generation_state(db)
        statistic_rows = _statistics(db)
        stats_by_key = {row["cohort_key"]: row for row in statistic_rows}
        manifest_ids = sorted({int(row["evidence_manifest_id"]) for row in statistic_rows})
        manifest_members = _manifest_members(db, manifest_ids)
        old_rows = _source_rows(db)
        old_members = _old_members(db, [int(row["id"]) for row in old_rows])
        predictions = _predictions(db, [int(row["prediction_id"]) for row in old_rows])
        clean_window = _clean_window(db)
        max_decision_cutoff = max(
            row["training_cutoff_at"] for row in old_rows if row["estimate_kind"] == "DECISION_TIME"
        )
        if datetime.fromisoformat(clean_window["min_matured_at"]) <= max_decision_cutoff:
            raise RuntimeError("Generation-11 evidence overlaps a historical decision-time cutoff")

        records: list[dict[str, Any]] = []
        for old in old_rows:
            prediction = predictions[int(old["prediction_id"])]
            old_ids = old_members.get(int(old["id"]), ())
            if old["estimate_kind"] == "DECISION_TIME":
                record = _decision_record(old, old_ids, prediction, generation_state)
            else:
                record = _latest_record(
                    old,
                    old_ids,
                    prediction,
                    generation_state,
                    stats_by_key,
                    manifest_members,
                    config,
                )
                independent = _independent_latest_statistic(
                    prediction,
                    statistic_rows,
                    manifest_members,
                    config,
                )
                if independent != record["candidate_cohort_statistic_id"]:
                    raise RuntimeError(
                        f"candidate estimator/verifier disagree for estimate {old['id']}: "
                        f"{record['candidate_cohort_statistic_id']} != {independent}"
                    )
            records.append(canonicalize_manifest_value(record))

        classifications = Counter(row["scope_classification"] for row in records)
        reasons = Counter(row["replacement_reason"] for row in records)
        contaminated_ids = _contaminated_ids(db)
        mapped_contaminated = {
            int(row["existing_estimate_id"])
            for row in records
            if int(row["existing_estimate_id"]) in contaminated_ids
        }
        if mapped_contaminated != contaminated_ids:
            raise RuntimeError("not every historically contaminated estimate maps to a candidate")
        serving = _serving_snapshot(db)
        payload: dict[str, Any] = {
            "schema": "swinglens-winner-candidate-estimates-v1",
            "generation": generation_state,
            "source_version": SOURCE_VERSION,
            "candidate_count": len(records),
            "decision_time_count": sum(r["estimate_kind"] == "DECISION_TIME" for r in records),
            "latest_rescore_count": sum(r["estimate_kind"] == "LATEST_RESCORE" for r in records),
            "classifications": dict(sorted(classifications.items())),
            "replacement_reasons": dict(sorted(reasons.items())),
            "historically_contaminated_count": len(contaminated_ids),
            "historically_contaminated_mapped_count": len(mapped_contaminated),
            "clean_window": clean_window,
            "serving_before": serving,
            "original_estimate_max_id": int(
                db.scalar(text("SELECT coalesce(max(id),0) FROM winner_probability_estimates")) or 0
            ),
            "original_membership_max_id": int(
                db.scalar(text("SELECT coalesce(max(id),0) FROM winner_estimate_evidence_members"))
                or 0
            ),
            "original_estimate_business_hash": _original_estimate_hash(db),
            "original_membership_hash": _original_membership_hash(db),
            "protected": _protected_state(db),
            "records": records,
        }
        payload["artifact_hash"] = _artifact_hash(payload)
        path = output_dir / f"candidate_estimates_{payload['artifact_hash']}.json"
        _write(path, payload)
        db.rollback()
    print(json.dumps(_summary(path, payload), indent=2, sort_keys=True))
    return path


def write(
    *,
    artifact_path: Path,
    expected_hash: str,
    actor: str,
    request_key: str,
    approve_write: bool,
    output_dir: Path,
) -> Path:
    if not approve_write:
        raise PermissionError("explicit --approve-write is required")
    if not actor.strip() or not request_key.strip():
        raise ValueError("actor and request key are required")
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    if artifact.get("artifact_hash") != expected_hash or _artifact_hash(artifact) != expected_hash:
        raise ValueError("reviewed candidate manifest hash mismatch")
    records = artifact["records"]
    with SessionLocal() as db:
        _preflight(db)
        if _generation_state(db) != artifact["generation"]:
            raise RuntimeError("Generation 11 drifted after review")
        if _protected_state(db) != artifact["protected"]:
            raise RuntimeError("protected Winner state drifted after review")
        if (
            _original_estimate_hash(db, max_id=artifact["original_estimate_max_id"])
            != artifact["original_estimate_business_hash"]
        ):
            raise RuntimeError("original estimate rows drifted after review")
        if (
            _original_membership_hash(db, max_id=artifact["original_membership_max_id"])
            != artifact["original_membership_hash"]
        ):
            raise RuntimeError("original estimate memberships drifted after review")
        serving_before = _serving_ids(db)
        empty_manifest = _ensure_empty_manifest(db)
        existing_candidate_count = int(
            db.scalar(
                text(
                    "SELECT count(*) FROM winner_probability_estimates "
                    "WHERE cohort_generation_id=:generation_id AND source_version=:source_version "
                    "AND lifecycle_status='CANDIDATE'"
                ),
                {"generation_id": GENERATION_ID, "source_version": SOURCE_VERSION},
            )
            or 0
        )
        if existing_candidate_count not in {0, len(records)}:
            raise RuntimeError(f"partial candidate population detected: {existing_candidate_count}")
        inserted = 0
        if existing_candidate_count == 0:
            for offset in range(0, len(records), 1000):
                values = [
                    _insert_value(
                        record,
                        empty_manifest_id=int(empty_manifest.id),
                        actor=actor,
                        request_key=request_key,
                        reviewed_hash=expected_hash,
                    )
                    for record in records[offset : offset + 1000]
                ]
                db.execute(insert(WinnerProbabilityEstimate), values)
                inserted += len(values)
            db.flush()
        candidate_count = int(
            db.scalar(
                text(
                    "SELECT count(*) FROM winner_probability_estimates "
                    "WHERE cohort_generation_id=:generation_id AND source_version=:source_version "
                    "AND lifecycle_status='CANDIDATE'"
                ),
                {"generation_id": GENERATION_ID, "source_version": SOURCE_VERSION},
            )
            or 0
        )
        if candidate_count != len(records):
            raise RuntimeError(f"candidate row count mismatch: {candidate_count} != {len(records)}")
        db.execute(
            text(
                """
                INSERT INTO winner_estimate_evidence_members (
                  estimate_id,prediction_id,outcome_id,outcome_revision,
                  eligibility_decision_id,temporal_validity_decision_id,outcome_replay_id,
                  evidence_origin,episode_id,inclusion_weight,included_as_of,
                  inclusion_cutoff_at,metadata_json
                )
                SELECT e.id,m.prediction_id,m.forward_outcome_id,m.forward_revision,
                  m.eligibility_decision_id,m.temporal_validity_decision_id,m.outcome_replay_id,
                  m.evidence_origin,m.episode_id,m.inclusion_weight,transaction_timestamp(),
                  e.training_cutoff_at,
                  jsonb_build_object(
                    'target_stop_outcome_id',m.target_stop_outcome_id,
                    'target_stop_revision',m.target_stop_revision,
                    'eligibility_decision_id',m.eligibility_decision_id,
                    'temporal_validity_decision_id',m.temporal_validity_decision_id,
                    'outcome_replay_id',m.outcome_replay_id,
                    'evidence_origin',m.evidence_origin,
                    'candidate_generation_id',CAST(:generation_id AS bigint),
                    'reviewed_manifest_hash',CAST(:reviewed_hash AS text)
                  )
                FROM winner_probability_estimates e
                JOIN winner_evidence_manifest_members m ON m.manifest_id=e.evidence_manifest_id
                WHERE e.cohort_generation_id=:generation_id
                  AND e.source_version=:source_version
                  AND e.lifecycle_status='CANDIDATE'
                  AND e.source='COHORT'
                ON CONFLICT ON CONSTRAINT
                  uq_winner_estimate_evidence_members_estimate_outcome_revision DO NOTHING
                """
            ),
            {
                "generation_id": GENERATION_ID,
                "source_version": SOURCE_VERSION,
                "reviewed_hash": expected_hash,
            },
        )
        db.flush()
        if _serving_ids(db) != serving_before:
            raise RuntimeError("candidate write changed the serving estimate ID set")
        checks = _candidate_checks(db, artifact)
        checks["candidate_rows_match_manifest"] = _persisted_candidates_match(db, artifact)
        checks["candidate_memberships_match_manifest"] = _persisted_memberships_match(db, artifact)
        if not all(checks.values()):
            raise RuntimeError(f"candidate write verification failed: {checks}")
        payload: dict[str, Any] = {
            "schema": "swinglens-winner-candidate-estimate-write-v1",
            "reviewed_manifest_hash": expected_hash,
            "actor": actor,
            "request_key": request_key,
            "inserted": inserted,
            "candidate_count": candidate_count,
            "candidate_memberships": _candidate_membership_count(db),
            "checks": checks,
            "serving_after": _serving_snapshot(db),
        }
        payload["artifact_hash"] = _artifact_hash(payload)
        db.commit()
        path = output_dir / f"candidate_write_{payload['artifact_hash']}.json"
        _write(path, payload)
    print(json.dumps(_summary(path, payload), indent=2, sort_keys=True))
    return path


def verify(*, artifact_path: Path, expected_hash: str, output_dir: Path) -> Path:
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    if artifact.get("artifact_hash") != expected_hash or _artifact_hash(artifact) != expected_hash:
        raise ValueError("reviewed candidate manifest hash mismatch")
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        _preflight(db)
        checks = _candidate_checks(db, artifact)
        checks["candidate_rows_match_manifest"] = _persisted_candidates_match(db, artifact)
        checks["candidate_memberships_match_manifest"] = _persisted_memberships_match(db, artifact)
        checks["serving_set_unchanged"] = _serving_snapshot(db) == artifact["serving_before"]
        checks["original_estimates_preserved"] = (
            _original_estimate_hash(db, max_id=artifact["original_estimate_max_id"])
            == artifact["original_estimate_business_hash"]
        )
        checks["original_memberships_preserved"] = (
            _original_membership_hash(db, max_id=artifact["original_membership_max_id"])
            == artifact["original_membership_hash"]
        )
        if not all(checks.values()):
            raise RuntimeError(f"candidate verification failed: {checks}")
        distribution = _distribution(db)
        payload: dict[str, Any] = {
            "schema": "swinglens-winner-candidate-estimate-verification-v1",
            "reviewed_manifest_hash": expected_hash,
            "checks": checks,
            "candidate_count": len(artifact["records"]),
            "candidate_memberships": _candidate_membership_count(db),
            "distribution": distribution,
            "fallback_counts": _fallback_counts(db),
            "largest_changes": _largest_changes(db),
            "historically_contaminated": _contaminated_reconciliation(db),
            "calibration": _calibration(db),
            "protected": _protected_state(db),
        }
        payload["artifact_hash"] = _artifact_hash(payload)
        path = output_dir / f"candidate_verification_{payload['artifact_hash']}.json"
        _write(path, payload)
        db.rollback()
    print(json.dumps(_summary(path, payload), indent=2, sort_keys=True))
    return path


def _source_rows(db) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in db.execute(
            text(
                """
                WITH live AS (
                  SELECT e.* FROM winner_probability_estimates e
                  LEFT JOIN winner_cohort_generations g ON g.id=e.cohort_generation_id
                  WHERE e.lifecycle_status='PUBLISHED' AND e.source='COHORT'
                    AND (e.cohort_generation_id IS NULL OR g.status='PUBLISHED')
                ), latest AS (
                  SELECT DISTINCT ON(prediction_id,outcome_definition_id)
                    * FROM live WHERE estimate_kind='LATEST_RESCORE'
                  ORDER BY prediction_id,outcome_definition_id,
                    training_cutoff_at DESC,created_at DESC,id DESC
                )
                SELECT id,prediction_id,outcome_definition_id,estimate_kind,source_version,
                  cohort_definition_id,evidence_manifest_id,cohort_generation_id,
                  training_cutoff_at,point_probability,sample_n,effective_n,evidence_grade,
                  created_at
                FROM live WHERE estimate_kind='DECISION_TIME'
                UNION ALL
                SELECT id,prediction_id,outcome_definition_id,estimate_kind,source_version,
                  cohort_definition_id,evidence_manifest_id,cohort_generation_id,
                  training_cutoff_at,point_probability,sample_n,effective_n,evidence_grade,
                  created_at FROM latest
                ORDER BY estimate_kind,id
                """
            )
        ).mappings()
    ]


def _statistics(db) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in db.execute(
            select(
                WinnerCohortStatistic.id,
                WinnerCohortStatistic.cohort_definition_id,
                WinnerCohortStatistic.evidence_manifest_id,
                WinnerCohortStatistic.sample_n,
                WinnerCohortStatistic.effective_n,
                WinnerCohortStatistic.wins,
                WinnerCohortStatistic.posterior_probability,
                WinnerCohortStatistic.lower_bound,
                WinnerCohortStatistic.upper_bound,
                WinnerCohortStatistic.median_return_pct,
                WinnerCohortStatistic.median_mfe_pct,
                WinnerCohortStatistic.median_mae_pct,
                WinnerCohortStatistic.evidence_grade,
                WinnerCohortStatistic.evidence_manifest_hash,
                WinnerCohortStatistic.metadata_json,
                WinnerCohortDefinition.cohort_key,
                WinnerCohortDefinition.level,
                WinnerCohortDefinition.dimensions_json,
            )
            .join(
                WinnerCohortDefinition,
                WinnerCohortDefinition.id == WinnerCohortStatistic.cohort_definition_id,
            )
            .where(WinnerCohortStatistic.generation_id == GENERATION_ID)
            .order_by(WinnerCohortStatistic.id)
        ).mappings()
    ]


def _manifest_members(db, manifest_ids: list[int]) -> dict[int, tuple[dict[str, Any], ...]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in db.execute(
        text(
            "SELECT manifest_id,member_ordinal,prediction_id,forward_outcome_id,episode_id "
            "FROM winner_evidence_manifest_members WHERE manifest_id=ANY(:ids) "
            "ORDER BY manifest_id,member_ordinal"
        ),
        {"ids": manifest_ids},
    ).mappings():
        grouped[int(row["manifest_id"])].append(dict(row))
    return {key: tuple(value) for key, value in grouped.items()}


def _old_members(db, estimate_ids: list[int]) -> dict[int, tuple[int, ...]]:
    grouped: dict[int, list[int]] = defaultdict(list)
    for row in db.execute(
        text(
            "SELECT estimate_id,id FROM winner_estimate_evidence_members "
            "WHERE estimate_id=ANY(:ids) ORDER BY estimate_id,id"
        ),
        {"ids": estimate_ids},
    ):
        grouped[int(row[0])].append(int(row[1]))
    return {key: tuple(value) for key, value in grouped.items()}


def _predictions(db, prediction_ids: list[int]) -> dict[int, dict[str, Any]]:
    return {
        int(row["id"]): dict(row)
        for row in db.execute(
            text(
                "SELECT id,ticker,episode_id,feature_json FROM winner_prediction_snapshots "
                "WHERE id=ANY(:ids) ORDER BY id"
            ),
            {"ids": sorted(set(prediction_ids))},
        ).mappings()
    }


def _decision_record(old, old_ids, prediction, generation):
    return {
        "existing_estimate_id": old["id"],
        "prediction_id": old["prediction_id"],
        "outcome_definition_id": old["outcome_definition_id"],
        "ticker": prediction["ticker"],
        "estimate_kind": old["estimate_kind"],
        "existing_source_version": old["source_version"],
        "existing_generation_id": old["cohort_generation_id"],
        "existing_cohort_definition_id": old["cohort_definition_id"],
        "existing_evidence_member_ids": list(old_ids),
        "existing_evidence_hash": _id_hash(old_ids),
        "existing_probability": old["point_probability"],
        "candidate_generation_id": GENERATION_ID,
        "candidate_generation_key": generation["generation_key"],
        "candidate_training_cutoff_at": old["training_cutoff_at"],
        "candidate_source": "INSUFFICIENT",
        "candidate_cohort_statistic_id": None,
        "candidate_cohort_definition_id": None,
        "candidate_requested_level": "L0",
        "candidate_selected_level": None,
        "candidate_selected_key": None,
        "candidate_evidence_manifest_id": None,
        "candidate_evidence_manifest_hash": _hash_payload({"members": []}),
        "candidate_clean_evidence_ids": [],
        "candidate_clean_evidence_hash": _id_hash(()),
        "candidate_probability": None,
        "candidate_lower_bound": None,
        "candidate_upper_bound": None,
        "candidate_interval_width": None,
        "candidate_sample_n": 0,
        "candidate_effective_n": "0",
        "candidate_evidence_grade": EvidenceGrade.INSUFFICIENT,
        "candidate_wins": "0",
        "candidate_fallback_path": "NO_CLEAN_EVIDENCE_AT_ORIGINAL_DECISION_CUTOFF",
        "scope_classification": "NO_CLEAN_REPLACEMENT",
        "replacement_reason": "POINT_IN_TIME_CLEAN_EVIDENCE_CHANGED",
    }


def _latest_record(old, old_ids, prediction, generation, stats_by_key, members, config):
    keys = CohortDefinitionService().cohort_keys_for_features(
        prediction["feature_json"] or {}, config
    )
    selected_key = None
    selected = None
    for level, key in zip(config.cohort.hierarchy, keys, strict=True):
        statistic = stats_by_key.get(key.key)
        if statistic is not None and _eligible_statistic(statistic, level.min_effective_n, config):
            selected_key, selected = key, statistic
            break
    broadest = stats_by_key.get(keys[-1].key) if keys else None
    statistic = selected or broadest
    fallback = (
        "SELECTED_REQUESTED_LEVEL"
        if selected_key and selected_key.level == "L0"
        else "SPARSE_COHORT_FALLBACK"
    )
    if selected is not None and _contains_subject(
        members[int(selected["evidence_manifest_id"])], prediction
    ):
        selected_key = None
        selected = None
        fallback = "HISTORICAL_SELF_EXCLUSION_REQUIRED"
    evidence_rows = members.get(int(statistic["evidence_manifest_id"]), ()) if statistic else ()
    evidence_ids = tuple(int(row["forward_outcome_id"]) for row in evidence_rows)
    probability = selected["posterior_probability"] if selected is not None else None
    interval = (
        Decimal(str(selected["upper_bound"])) - Decimal(str(selected["lower_bound"]))
        if selected is not None
        and selected["upper_bound"] is not None
        and selected["lower_bound"] is not None
        else None
    )
    return {
        "existing_estimate_id": old["id"],
        "prediction_id": old["prediction_id"],
        "outcome_definition_id": old["outcome_definition_id"],
        "ticker": prediction["ticker"],
        "estimate_kind": old["estimate_kind"],
        "existing_source_version": old["source_version"],
        "existing_generation_id": old["cohort_generation_id"],
        "existing_cohort_definition_id": old["cohort_definition_id"],
        "existing_evidence_member_ids": list(old_ids),
        "existing_evidence_hash": _id_hash(old_ids),
        "existing_probability": old["point_probability"],
        "candidate_generation_id": GENERATION_ID,
        "candidate_generation_key": generation["generation_key"],
        "candidate_training_cutoff_at": generation["training_cutoff_at"],
        "candidate_source": "COHORT" if selected is not None else "INSUFFICIENT",
        "candidate_cohort_statistic_id": selected["id"] if selected is not None else None,
        "candidate_cohort_definition_id": selected["cohort_definition_id"]
        if selected is not None
        else None,
        "candidate_requested_level": "L0",
        "candidate_selected_level": selected_key.level if selected_key else None,
        "candidate_selected_key": selected_key.key if selected_key else None,
        "candidate_evidence_manifest_id": statistic["evidence_manifest_id"] if statistic else None,
        "candidate_evidence_manifest_hash": statistic["evidence_manifest_hash"]
        if statistic
        else _hash_payload({"members": []}),
        "candidate_clean_evidence_ids": list(evidence_ids),
        "candidate_clean_evidence_hash": _id_hash(evidence_ids),
        "candidate_probability": probability,
        "candidate_lower_bound": selected["lower_bound"] if selected is not None else None,
        "candidate_upper_bound": selected["upper_bound"] if selected is not None else None,
        "candidate_interval_width": interval,
        "candidate_sample_n": statistic["sample_n"] if statistic else 0,
        "candidate_effective_n": statistic["effective_n"] if statistic else "0",
        "candidate_evidence_grade": selected["evidence_grade"]
        if selected is not None
        else EvidenceGrade.INSUFFICIENT,
        "candidate_wins": statistic["wins"] if statistic else "0",
        "candidate_fallback_path": fallback,
        "scope_classification": "GENERATION_CHANGED",
        "replacement_reason": "LATEST_RESCORE_CLEAN_GENERATION_CHANGED",
    }


def _independent_latest_statistic(prediction, statistics, members, config):
    features = prediction["feature_json"] or {}
    by_level = {level.level: level for level in config.cohort.hierarchy}
    matches = []
    for row in statistics:
        dims = row["dimensions_json"] or {}
        if all(
            ("all" if key == "global" else _normalized(features.get(key))) == value
            for key, value in dims.items()
        ):
            matches.append(row)
    by_name = {row["level"]: row for row in matches}
    for level_name in [level.level for level in config.cohort.hierarchy]:
        row = by_name.get(level_name)
        if row is None or not _eligible_statistic(
            row, by_level[level_name].min_effective_n, config
        ):
            continue
        if _contains_subject(members[int(row["evidence_manifest_id"])], prediction):
            return None
        return int(row["id"])
    return None


def _eligible_statistic(row, minimum, config) -> bool:
    if row["lower_bound"] is None or row["upper_bound"] is None:
        return False
    width = Decimal(str(row["upper_bound"])) - Decimal(str(row["lower_bound"]))
    return (
        Decimal(str(row["effective_n"])) >= Decimal(str(minimum))
        and width <= Decimal(str(config.cohort.max_interval_width))
        and row["evidence_grade"] != EvidenceGrade.INSUFFICIENT
    )


def _contains_subject(rows, prediction) -> bool:
    return any(
        int(row["prediction_id"]) == int(prediction["id"])
        or (
            prediction["episode_id"] is not None
            and row["episode_id"] is not None
            and int(row["episode_id"]) == int(prediction["episode_id"])
        )
        for row in rows
    )


def _normalized(value):
    return "__MISSING__" if value is None or value == "" else value


def _insert_value(record, *, empty_manifest_id, actor, request_key, reviewed_hash):
    manifest_id = record["candidate_evidence_manifest_id"] or empty_manifest_id
    probability = _decimal(record["candidate_probability"])
    metadata = {
        "candidate_generation_id": GENERATION_ID,
        "candidate_generation_key": record["candidate_generation_key"],
        "candidate_cohort_statistic_id": record["candidate_cohort_statistic_id"],
        "selected_cohort_level": record["candidate_selected_level"],
        "selected_cohort_key": record["candidate_selected_key"],
        "requested_cohort_level": record["candidate_requested_level"],
        "fallback_path": record["candidate_fallback_path"],
        "existing_estimate_id": record["existing_estimate_id"],
        "scope_classification": record["scope_classification"],
        "reviewed_manifest_hash": reviewed_hash,
        "candidate_actor": actor,
        "candidate_request_key": request_key,
        "wins": str(record["candidate_wins"]),
    }
    return {
        "prediction_id": int(record["prediction_id"]),
        "outcome_definition_id": int(record["outcome_definition_id"]),
        "estimate_kind": record["estimate_kind"],
        "source": record["candidate_source"],
        "source_version": SOURCE_VERSION,
        "cohort_definition_id": record["candidate_cohort_definition_id"],
        "model_version_id": None,
        "evidence_manifest_id": manifest_id,
        "cohort_generation_id": GENERATION_ID,
        "training_cutoff_at": datetime.fromisoformat(record["candidate_training_cutoff_at"]),
        "lifecycle_status": EstimateLifecycleStatus.CANDIDATE,
        "published_at": None,
        "superseded_at": None,
        "supersedes_estimate_id": int(record["existing_estimate_id"]),
        "point_probability": probability,
        "lower_bound": _decimal(record["candidate_lower_bound"]),
        "upper_bound": _decimal(record["candidate_upper_bound"]),
        "interval_width": _decimal(record["candidate_interval_width"]),
        "sample_n": int(record["candidate_sample_n"]),
        "effective_n": _decimal(record["candidate_effective_n"]),
        "evidence_grade": record["candidate_evidence_grade"],
        "insufficient_reasons_json": []
        if probability is not None
        else [record["candidate_fallback_path"].lower()],
        "expected_return_pct": None,
        "median_return_pct": None,
        "median_mfe_pct": None,
        "median_mae_pct": None,
        "target_first_rate": None,
        "config_hash": artifact_config_hash(record),
        "feature_schema_version": artifact_feature_version(record),
        "evidence_manifest_hash": record["candidate_evidence_manifest_hash"],
        "metadata_json": metadata,
    }


def artifact_config_hash(_record):
    return load_winner_probability_config().config_hash


def artifact_feature_version(_record):
    return load_winner_probability_config().feature_schema.version


def _ensure_empty_manifest(db):
    digest = _hash_payload({"members": []})
    row = db.scalar(
        select(WinnerEvidenceManifest).where(WinnerEvidenceManifest.manifest_hash == digest)
    )
    if row is None:
        row = WinnerEvidenceManifest(
            manifest_hash=digest,
            hash_algorithm="sha256",
            content_encoding="json",
            member_count=0,
            payload_json={"members": []},
        )
        db.add(row)
        db.flush()
    return row


def _candidate_checks(db, artifact) -> dict[str, bool]:
    count = int(
        db.scalar(
            text(
                "SELECT count(*) FROM winner_probability_estimates WHERE cohort_generation_id=11 AND source_version=:v AND lifecycle_status='CANDIDATE'"
            ),
            {"v": SOURCE_VERSION},
        )
        or 0
    )
    exposure = int(
        db.scalar(
            select(text("count(*)"))
            .select_from(WinnerProbabilityEstimate)
            .where(WinnerProbabilityEstimate.cohort_generation_id == 11)
            .where(WinnerProbabilityEstimate.source_version == SOURCE_VERSION)
            .where(estimate_is_serving())
        )
        or 0
    )
    purity = db.execute(
        text("""
      WITH latest AS (SELECT DISTINCT ON(prediction_id) prediction_id,evidence_eligible FROM winner_temporal_validity_decisions ORDER BY prediction_id,validation_sequence DESC)
      SELECT
        count(*) FILTER(WHERE l.prediction_id IS NULL OR NOT l.evidence_eligible) bad_temporal,
        count(*) FILTER(WHERE upper(p.ticker)='CLBK') clbk
      FROM winner_estimate_evidence_members m
      JOIN winner_probability_estimates e ON e.id=m.estimate_id
      JOIN winner_prediction_snapshots p ON p.id=m.prediction_id
      LEFT JOIN latest l ON l.prediction_id=m.prediction_id
      WHERE e.cohort_generation_id=11 AND e.source_version=:v
    """),
        {"v": SOURCE_VERSION},
    ).one()
    generation = _generation_state(db)
    return {
        "candidate_count": count == int(artifact["candidate_count"]),
        "candidate_membership_count": _candidate_membership_count(db)
        == sum(
            len(record["candidate_clean_evidence_ids"])
            for record in artifact["records"]
            if record["candidate_source"] == "COHORT"
        ),
        "candidate_exposure_zero": exposure == 0,
        "candidate_temporal_impurity_zero": int(purity[0] or 0) == 0,
        "candidate_clbk_zero": int(purity[1] or 0) == 0,
        "generation_11_ready": generation["status"] == "READY"
        and generation["published_at"] is None,
        "generation_10_published": db.scalar(
            text("SELECT status FROM winner_cohort_generations WHERE id=10")
        )
        == "PUBLISHED",
        "quarantine_unchanged": int(
            db.scalar(
                text(
                    "WITH latest AS (SELECT DISTINCT ON(prediction_id) * FROM winner_temporal_validity_decisions ORDER BY prediction_id,validation_sequence DESC) SELECT count(*) FROM latest WHERE NOT evidence_eligible"
                )
            )
            or 0
        )
        == 1292,
        "active_winner_jobs_zero": _active_jobs(db) == 0,
    }


def _persisted_candidates_match(db, artifact) -> bool:
    expected = {int(row["existing_estimate_id"]): row for row in artifact["records"]}
    rows = db.execute(
        text(
            """
            SELECT id,prediction_id,outcome_definition_id,estimate_kind,source,
              training_cutoff_at,point_probability,lower_bound,upper_bound,
              interval_width,sample_n,effective_n,evidence_grade,evidence_manifest_id,
              metadata_json
            FROM winner_probability_estimates
            WHERE cohort_generation_id=11 AND source_version=:v
            ORDER BY id
            """
        ),
        {"v": SOURCE_VERSION},
    ).mappings()
    seen = set()
    for row in rows:
        old_id = int(row["metadata_json"]["existing_estimate_id"])
        record = expected.get(old_id)
        if record is None:
            return False
        seen.add(old_id)
        comparisons = (
            int(row["prediction_id"]) == int(record["prediction_id"]),
            int(row["outcome_definition_id"]) == int(record["outcome_definition_id"]),
            row["estimate_kind"] == record["estimate_kind"],
            row["source"] == record["candidate_source"],
            row["training_cutoff_at"]
            == datetime.fromisoformat(record["candidate_training_cutoff_at"]),
            _decimal(row["point_probability"]) == _decimal(record["candidate_probability"]),
            _decimal(row["lower_bound"]) == _decimal(record["candidate_lower_bound"]),
            _decimal(row["upper_bound"]) == _decimal(record["candidate_upper_bound"]),
            _decimal(row["interval_width"]) == _decimal(record["candidate_interval_width"]),
            int(row["sample_n"] or 0) == int(record["candidate_sample_n"]),
            _decimal(row["effective_n"]) == _decimal(record["candidate_effective_n"]),
            row["evidence_grade"] == record["candidate_evidence_grade"],
        )
        if not all(comparisons):
            return False
    return seen == set(expected)


def _persisted_memberships_match(db, artifact) -> bool:
    expected = {
        int(row["existing_estimate_id"]): tuple(row["candidate_clean_evidence_ids"])
        if row["candidate_source"] == "COHORT"
        else ()
        for row in artifact["records"]
    }
    grouped: dict[int, list[int]] = defaultdict(list)
    rows = db.execute(
        text(
            """
            SELECT (e.metadata_json->>'existing_estimate_id')::bigint old_id,m.outcome_id
            FROM winner_probability_estimates e
            JOIN winner_estimate_evidence_members m ON m.estimate_id=e.id
            WHERE e.cohort_generation_id=11 AND e.source_version=:v
            ORDER BY old_id,m.outcome_id
            """
        ),
        {"v": SOURCE_VERSION},
    )
    for old_id, outcome_id in rows:
        grouped[int(old_id)].append(int(outcome_id))
    return all(
        tuple(grouped.get(old_id, ())) == tuple(sorted(ids)) for old_id, ids in expected.items()
    )


def _distribution(db):
    rows = [
        Decimal(str(value))
        for value in db.scalars(
            text("""
      SELECT abs(c.point_probability-o.point_probability)
      FROM winner_probability_estimates c
      JOIN winner_probability_estimates o ON o.id=(c.metadata_json->>'existing_estimate_id')::bigint
      WHERE c.cohort_generation_id=11 AND c.source_version=:v
        AND c.point_probability IS NOT NULL AND o.point_probability IS NOT NULL
    """),
            {"v": SOURCE_VERSION},
        )
    ]
    rows.sort()
    if not rows:
        return {"comparable_count": 0}

    def pct(p):
        return rows[min(len(rows) - 1, int((len(rows) - 1) * p))]

    buckets = Counter()
    for value in rows:
        pp = value * 100
        buckets[
            "<1 pp"
            if pp < 1
            else "1-3 pp"
            if pp < 3
            else "3-5 pp"
            if pp < 5
            else "5-10 pp"
            if pp < 10
            else "10-20 pp"
            if pp < 20
            else ">20 pp"
        ] += 1
    signed = db.execute(
        text("""
      SELECT count(*) FILTER(WHERE c.point_probability>o.point_probability),
             count(*) FILTER(WHERE c.point_probability<o.point_probability),
             count(*) FILTER(WHERE c.point_probability=o.point_probability)
      FROM winner_probability_estimates c JOIN winner_probability_estimates o ON o.id=(c.metadata_json->>'existing_estimate_id')::bigint
      WHERE c.cohort_generation_id=11 AND c.source_version=:v AND c.point_probability IS NOT NULL AND o.point_probability IS NOT NULL
    """),
        {"v": SOURCE_VERSION},
    ).one()
    signed_metrics = db.execute(
        text("""
      SELECT avg(c.point_probability-o.point_probability),
        percentile_cont(.5) within group(order by c.point_probability-o.point_probability),
        min(c.point_probability-o.point_probability),max(c.point_probability-o.point_probability)
      FROM winner_probability_estimates c JOIN winner_probability_estimates o ON o.id=(c.metadata_json->>'existing_estimate_id')::bigint
      WHERE c.cohort_generation_id=11 AND c.source_version=:v AND c.point_probability IS NOT NULL AND o.point_probability IS NOT NULL
    """),
        {"v": SOURCE_VERSION},
    ).one()
    return canonicalize_manifest_value(
        {
            "comparable_count": len(rows),
            "mean_absolute_delta": sum(rows) / len(rows),
            "median_absolute_delta": pct(0.5),
            "p90_absolute_delta": pct(0.9),
            "maximum_absolute_delta": rows[-1],
            "buckets": dict(buckets),
            "signed": {
                "up": signed[0],
                "down": signed[1],
                "equal": signed[2],
                "mean": signed_metrics[0],
                "median": signed_metrics[1],
                "minimum": signed_metrics[2],
                "maximum": signed_metrics[3],
            },
        }
    )


def _calibration(db):
    rows = [
        dict(row)
        for row in db.execute(
            text("""
      WITH latest AS (
        SELECT DISTINCT ON(prediction_id) prediction_id,evidence_eligible
        FROM winner_temporal_validity_decisions ORDER BY prediction_id,validation_sequence DESC
      )
      SELECT c.point_probability probability,(o.primary_winner::int) label
      FROM winner_probability_estimates c
      JOIN winner_target_stop_outcomes o ON o.prediction_id=c.prediction_id
        AND o.outcome_definition_id=c.outcome_definition_id
        AND o.is_current_revision AND o.status='MATURED'
      JOIN latest l ON l.prediction_id=c.prediction_id AND l.evidence_eligible
      WHERE c.cohort_generation_id=11 AND c.source_version=:v
        AND c.point_probability IS NOT NULL
    """),
            {"v": SOURCE_VERSION},
        ).mappings()
    ]
    if len(rows) < 100:
        return {"status": "CALIBRATION_INSUFFICIENT", "sample_n": len(rows)}
    brier = sum(
        (Decimal(str(row["probability"])) - Decimal(row["label"])) ** 2 for row in rows
    ) / len(rows)
    bins = []
    for floor in (
        Decimal("0.0"),
        Decimal("0.1"),
        Decimal("0.2"),
        Decimal("0.3"),
        Decimal("0.4"),
        Decimal("0.5"),
        Decimal("0.6"),
        Decimal("0.7"),
        Decimal("0.8"),
        Decimal("0.9"),
    ):
        ceiling = floor + Decimal("0.1")
        members = [row for row in rows if floor <= Decimal(str(row["probability"])) < ceiling]
        if members:
            bins.append(
                {
                    "floor": floor,
                    "ceiling": ceiling,
                    "sample_n": len(members),
                    "mean_predicted": sum(Decimal(str(row["probability"])) for row in members)
                    / len(members),
                    "observed_rate": sum(Decimal(row["label"]) for row in members) / len(members),
                }
            )
    return canonicalize_manifest_value(
        {
            "status": "DESCRIPTIVE_CANDIDATE_ONLY",
            "sample_n": len(rows),
            "brier_score": brier,
            "bins": bins,
        }
    )


def _fallback_counts(db):
    return dict(
        db.execute(
            text(
                "SELECT metadata_json->>'fallback_path',count(*) FROM winner_probability_estimates WHERE cohort_generation_id=11 AND source_version=:v GROUP BY 1 ORDER BY 1"
            ),
            {"v": SOURCE_VERSION},
        ).all()
    )


def _largest_changes(db):
    return [
        canonicalize_manifest_value(dict(row))
        for row in db.execute(
            text("""
      SELECT c.prediction_id,p.ticker,o.point_probability old_probability,c.point_probability candidate_probability,
        c.point_probability-o.point_probability delta,o.sample_n old_sample_n,c.sample_n candidate_sample_n,
        c.metadata_json->>'selected_cohort_level' candidate_level,
        c.metadata_json->>'fallback_path' fallback_path,
        c.metadata_json->>'existing_estimate_id' existing_estimate_id
      FROM winner_probability_estimates c JOIN winner_probability_estimates o ON o.id=(c.metadata_json->>'existing_estimate_id')::bigint
      JOIN winner_prediction_snapshots p ON p.id=c.prediction_id
      WHERE c.cohort_generation_id=11 AND c.source_version=:v AND c.point_probability IS NOT NULL AND o.point_probability IS NOT NULL
      ORDER BY abs(c.point_probability-o.point_probability) DESC,c.id LIMIT 25
    """),
            {"v": SOURCE_VERSION},
        ).mappings()
    ]


def _contaminated_reconciliation(db):
    return dict(
        db.execute(
            text("""
      WITH latest AS (SELECT DISTINCT ON(prediction_id) prediction_id,evidence_eligible FROM winner_temporal_validity_decisions ORDER BY prediction_id,validation_sequence DESC), bad AS (
        SELECT DISTINCT m.estimate_id FROM winner_estimate_evidence_members m JOIN latest l ON l.prediction_id=m.prediction_id WHERE NOT l.evidence_eligible
      )
      SELECT count(*) original_count,
        count(*) FILTER(WHERE c.id IS NOT NULL) candidate_created,
        count(*) FILTER(WHERE c.point_probability IS NULL) no_clean_probability,
        count(*) FILTER(WHERE EXISTS(SELECT 1 FROM winner_estimate_evidence_members cm LEFT JOIN latest cl ON cl.prediction_id=cm.prediction_id WHERE cm.estimate_id=c.id AND (cl.prediction_id IS NULL OR NOT cl.evidence_eligible))) candidate_with_invalid
      FROM bad JOIN winner_probability_estimates o ON o.id=bad.estimate_id
      LEFT JOIN winner_probability_estimates c ON (c.metadata_json->>'existing_estimate_id')::bigint=o.id AND c.cohort_generation_id=11 AND c.source_version=:v
    """),
            {"v": SOURCE_VERSION},
        )
        .mappings()
        .one()
    )


def _clean_window(db):
    return canonicalize_manifest_value(
        dict(
            db.execute(
                text("""
      SELECT min(o.matured_at) min_matured_at,max(o.matured_at) max_matured_at,count(*) clean_evidence_count
      FROM winner_cohort_generations g JOIN winner_evidence_manifests em ON em.manifest_hash=g.root_manifest_hash
      JOIN winner_evidence_manifest_members m ON m.manifest_id=em.id
      JOIN winner_forward_outcomes o ON o.id=m.forward_outcome_id WHERE g.id=11
    """)
            )
            .mappings()
            .one()
        )
    )


def _contaminated_ids(db):
    return set(
        int(value)
        for value in db.scalars(
            text("""
      WITH latest AS (SELECT DISTINCT ON(prediction_id) prediction_id,evidence_eligible FROM winner_temporal_validity_decisions ORDER BY prediction_id,validation_sequence DESC)
      SELECT DISTINCT m.estimate_id FROM winner_estimate_evidence_members m JOIN latest l ON l.prediction_id=m.prediction_id WHERE NOT l.evidence_eligible ORDER BY 1
    """)
        )
    )


def _generation_state(db):
    row = (
        db.execute(
            text(
                "SELECT id,generation_key,status,published_at,training_cutoff_at,watermark_hash,watermark_json,root_manifest_hash,evidence_row_count,planned_group_count,completed_group_count FROM winner_cohort_generations WHERE id=11"
            )
        )
        .mappings()
        .one()
    )
    return canonicalize_manifest_value(dict(row))


def _protected_state(db):
    return {
        "generation_10": canonicalize_manifest_value(
            dict(
                db.execute(
                    text(
                        "SELECT id,generation_key,status,published_at,root_manifest_hash,evidence_row_count FROM winner_cohort_generations WHERE id=10"
                    )
                )
                .mappings()
                .one()
            )
        ),
        "generation_11": _generation_state(db),
        "outcomes": _hash_query(
            db, "SELECT row_to_json(t)::text FROM winner_forward_outcomes t ORDER BY id"
        ),
        "target_stop": _hash_query(
            db, "SELECT row_to_json(t)::text FROM winner_target_stop_outcomes t ORDER BY id"
        ),
        "temporal": _hash_query(
            db, "SELECT row_to_json(t)::text FROM winner_temporal_validity_decisions t ORDER BY id"
        ),
        "prices": _hash_query(db, "SELECT row_to_json(t)::text FROM price_bars t ORDER BY id"),
        "price_versions": _hash_query(
            db, "SELECT row_to_json(t)::text FROM price_series_versions t ORDER BY id"
        ),
        "obligations": _hash_query(
            db, "SELECT row_to_json(t)::text FROM winner_market_data_obligations t ORDER BY id"
        ),
    }


def _serving_ids(db):
    return tuple(
        db.scalars(
            select(WinnerProbabilityEstimate.id)
            .where(estimate_is_serving())
            .order_by(WinnerProbabilityEstimate.id)
        )
    )


def _serving_snapshot(db):
    ids = _serving_ids(db)
    return {"count": len(ids), "sha256": _id_hash(ids)}


def _original_estimate_hash(db, max_id=None):
    where = " WHERE id<=:max_id" if max_id is not None else ""
    return _hash_query(
        db,
        "SELECT (to_jsonb(e)-ARRAY['lifecycle_status','published_at','superseded_at','supersedes_estimate_id','reconstruction_category'])::text FROM winner_probability_estimates e"
        + where
        + " ORDER BY id",
        {"max_id": max_id} if max_id is not None else None,
    )


def _original_membership_hash(db, max_id=None):
    where = " WHERE id<=:max_id" if max_id is not None else ""
    return _hash_query(
        db,
        "SELECT row_to_json(t)::text FROM winner_estimate_evidence_members t"
        + where
        + " ORDER BY id",
        {"max_id": max_id} if max_id is not None else None,
    )


def _candidate_membership_count(db):
    return int(
        db.scalar(
            text(
                "SELECT count(*) FROM winner_estimate_evidence_members m JOIN winner_probability_estimates e ON e.id=m.estimate_id WHERE e.cohort_generation_id=11 AND e.source_version=:v"
            ),
            {"v": SOURCE_VERSION},
        )
        or 0
    )


def _preflight(db):
    heads = tuple(db.scalars(text("SELECT version_num FROM alembic_version ORDER BY 1")))
    if heads != (EXPECTED_HEAD,):
        raise RuntimeError(f"expected head {EXPECTED_HEAD}, found {heads}")
    if _active_jobs(db):
        raise RuntimeError("active Winner jobs detected")
    generation = _generation_state(db)
    if (
        generation["generation_key"] != EXPECTED_GENERATION_KEY
        or generation["root_manifest_hash"] != EXPECTED_ROOT_HASH
        or generation["status"] != "READY"
        or generation["published_at"] is not None
    ):
        raise RuntimeError("Generation 11 is not the reviewed READY artifact")


def _active_jobs(db):
    return int(
        db.scalar(
            text(
                "SELECT count(*) FROM background_jobs WHERE job_type LIKE 'WINNER%' AND status IN ('QUEUED','RUNNING','RECOVERING')"
            )
        )
        or 0
    )


def _hash_query(db, query, params=None):
    digest = hashlib.sha256()
    count = 0
    values = db.scalars(
        text(query).execution_options(stream_results=True, yield_per=5000), params or {}
    )
    for value in values:
        digest.update(str(value).encode())
        digest.update(b"\n")
        count += 1
    return {"count": count, "sha256": digest.hexdigest()}


def _id_hash(values):
    digest = hashlib.sha256()
    for value in values:
        digest.update(str(int(value)).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _decimal(value):
    return Decimal(str(value)) if value is not None else None


def _artifact_hash(payload):
    return hashlib.sha256(
        canonical_manifest_bytes({k: v for k, v in payload.items() if k != "artifact_hash"})
    ).hexdigest()


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = canonical_manifest_bytes(payload) + b"\n"
    if path.exists():
        if path.read_bytes() != encoded:
            raise FileExistsError(path)
        return
    path.write_bytes(encoded)


def _summary(path, payload):
    return {
        "path": str(path.resolve()),
        "artifact_hash": payload["artifact_hash"],
        "candidate_count": payload.get("candidate_count"),
        "decision_time_count": payload.get("decision_time_count"),
        "latest_rescore_count": payload.get("latest_rescore_count"),
        "candidate_memberships": payload.get("candidate_memberships"),
        "checks": payload.get("checks"),
    }


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    plan_parser = sub.add_parser("plan")
    plan_parser.add_argument("--output-dir", type=Path, required=True)
    write_parser = sub.add_parser("write")
    write_parser.add_argument("--artifact", type=Path, required=True)
    write_parser.add_argument("--expected-hash", required=True)
    write_parser.add_argument("--actor", required=True)
    write_parser.add_argument("--request-key", required=True)
    write_parser.add_argument("--approve-write", action="store_true")
    write_parser.add_argument("--output-dir", type=Path, required=True)
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--artifact", type=Path, required=True)
    verify_parser.add_argument("--expected-hash", required=True)
    verify_parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "plan":
        plan(output_dir=args.output_dir)
    elif args.command == "write":
        write(
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
            expected_hash=args.expected_hash,
            output_dir=args.output_dir,
        )


if __name__ == "__main__":
    main()
