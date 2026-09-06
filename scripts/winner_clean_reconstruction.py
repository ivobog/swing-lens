# ruff: noqa: E501
"""Controlled clean Winner evidence and non-serving cohort reconstruction.

The command deliberately has separate read-only planning and explicitly approved
candidate-build phases.  It never publishes a generation, creates estimates,
or invokes outcome maturation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from typing import Any

from sqlalchemy import select, text

from app.db import SessionLocal
from app.models.tables import (
    WinnerCohortGeneration,
    WinnerOutcomeDefinition,
)
from app.services.winner_probability.cohort_definition import CohortDefinitionService
from app.services.winner_probability.cohort_generation_service import (
    CohortGenerationService,
    CohortGenerationStatus,
    EvidenceWatermark,
    EvidenceWatermarkService,
    canonical_generation_key,
    canonical_watermark_hash,
    contract_for,
)
from app.services.winner_probability.cohort_materialization_service import (
    CohortMaterializationService,
)
from app.services.winner_probability.cohort_statistics import CohortStatisticsService
from app.services.winner_probability.config import load_winner_probability_config
from app.services.winner_probability.evidence_manifest_service import (
    _hash_payload,
    _manifest_payload,
)
from app.services.winner_probability.evidence_service import EvidenceService
from app.services.winner_probability.temporal_manifest_canonicalization import (
    canonical_manifest_bytes,
    canonicalize_manifest_value,
)
from app.services.winner_probability.trading_session_service import latest_completed_session

EXPECTED_ALEMBIC_HEAD = "0059_winner_market_data_obligations"
EXPECTED_VALID_LEDGER = 3989
EXPECTED_QUARANTINED = 1292
EXPECTED_DUE = 0
PROTECTED_TABLES = (
    "winner_probability_estimates",
    "winner_estimate_evidence_members",
    "winner_temporal_validity_decisions",
    "winner_prediction_snapshots",
    "winner_forward_outcomes",
    "winner_target_stop_outcomes",
    "winner_market_data_obligations",
    "price_bars",
    "price_series_versions",
)


def plan(*, output_dir: Path) -> Path:
    started = perf_counter()
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        snapshot_at = db.scalar(text("SELECT transaction_timestamp()"))
        controls = _control_state(db)
        _assert_controls(controls)
        definition = _active_definition(db)
        config = load_winner_probability_config()
        watermark = EvidenceWatermarkService().current_material_watermark(
            db, outcome_definition_id=definition.id
        )
        watermark_json = watermark.as_dict()
        census_rows = _census_rows(db)
        census = dict(Counter(row["temporal_class"] for row in census_rows))
        independent_census = _independent_census(db)
        if census != independent_census:
            raise RuntimeError(
                f"service/independent temporal census differs: {census} != {independent_census}"
            )
        if sum(census.values()) != len(census_rows):
            raise RuntimeError("temporal census did not reconcile")
        certifiable = [
            row for row in census_rows if row["temporal_class"] == "HISTORICAL_CERTIFIABLE_VALID"
        ]
        # Historical rows without a durable semantic-input timestamp are
        # intentionally not certified from captured_at or non-quarantine alone.
        certification_manifest_hash = _canonical_hash(certifiable)

        completed_session = latest_completed_session(snapshot_at)
        rolling_start = snapshot_at.date().replace(
            year=snapshot_at.year - config.cohort.rolling_window_years
        )
        first_started = perf_counter()
        universe_a = EvidenceService().load_generation_evidence(
            db,
            outcome_definition=definition,
            training_cutoff_at=snapshot_at,
            config=config,
            watermark=watermark_json,
        )
        first_seconds = perf_counter() - first_started
        second_started = perf_counter()
        universe_b = EvidenceService().load_generation_evidence(
            db,
            outcome_definition=definition,
            training_cutoff_at=snapshot_at,
            config=config,
            watermark=watermark_json,
        )
        second_seconds = perf_counter() - second_started
        service_ids_a = tuple(int(row.forward_outcome.id) for row in universe_a.evidence)
        service_ids_b = tuple(int(row.forward_outcome.id) for row in universe_b.evidence)
        if service_ids_a != service_ids_b:
            raise RuntimeError("clean evidence service is nondeterministic")
        independent_ids = _independent_clean_ids(
            db,
            definition_id=int(definition.id),
            cutoff=snapshot_at,
            completed_session=completed_session,
            rolling_start=rolling_start,
            watermark=watermark,
            feature_schema_version=config.feature_schema.version,
            calculation_version=config.engine.calculation_version,
            config_hash=config.config_hash,
        )
        if service_ids_a != independent_ids:
            service_set = set(service_ids_a)
            sql_set = set(independent_ids)
            raise RuntimeError(
                "service/SQL evidence sets differ: "
                f"service_only={sorted(service_set - sql_set)[:20]} "
                f"sql_only={sorted(sql_set - service_set)[:20]}"
            )
        origins = Counter(row.evidence_origin for row in universe_a.evidence)
        if any(origin != "NATIVE_1_1" for origin in origins):
            raise RuntimeError(f"unexpected compatibility evidence in clean universe: {origins}")
        clean_records = _clean_records(db, service_ids_a)
        clean_manifest_hash = _canonical_hash(clean_records)
        if clean_manifest_hash != _canonical_hash(_clean_records(db, service_ids_b)):
            raise RuntimeError("clean audit manifest is not byte deterministic")
        root_manifest_hash = _hash_payload(_manifest_payload(universe_a.evidence))
        if root_manifest_hash != _hash_payload(_manifest_payload(universe_b.evidence)):
            raise RuntimeError("root evidence manifest is not deterministic")

        group_a = _group_plan(universe_a.evidence, config)
        group_b = _group_plan(universe_b.evidence, config)
        if canonical_manifest_bytes(group_a) != canonical_manifest_bytes(group_b):
            raise RuntimeError("candidate cohort statistics are nondeterministic")
        group_plan_hash = _canonical_hash(group_a)
        generation_10 = _generation_state(db, 10)
        generation_10_ids = _generation_root_ids(db, 10)
        clean_id_set = set(service_ids_a)
        generation_10_set = set(generation_10_ids)
        known_invalid_10 = _generation_invalid_ids(db, 10)
        if clean_id_set & set(known_invalid_10):
            raise RuntimeError("known invalid generation-10 evidence entered clean manifest")
        exclusions = _clean_exclusion_checks(db, service_ids_a)
        if any(exclusions.values()):
            raise RuntimeError(f"clean evidence exclusion invariant failed: {exclusions}")

        protected = {table: _table_hash(db, table) for table in PROTECTED_TABLES}
        old_generation_hash = _hash_query(
            db,
            "SELECT row_to_json(t)::text FROM winner_cohort_generations t "
            "WHERE id <= 10 ORDER BY id",
        )
        old_statistic_hash = _hash_query(
            db,
            "SELECT row_to_json(t)::text FROM winner_cohort_statistics t "
            "WHERE generation_id <= 10 ORDER BY id",
        )
        old_manifest_member_hash = _hash_query(
            db,
            "SELECT row_to_json(m)::text FROM winner_evidence_manifest_members m "
            "WHERE EXISTS (SELECT 1 FROM winner_cohort_statistics s "
            "WHERE s.generation_id <= 10 AND s.evidence_manifest_id=m.manifest_id) ORDER BY m.id",
        )
        repair_23_hash = _repair_23_hash(db)
        contract = contract_for(definition, config)
        expected_generation_key = canonical_generation_key(contract, watermark)
        payload: dict[str, Any] = {
            "schema": "swinglens-winner-clean-reconstruction-plan-v1",
            "snapshot_at": snapshot_at,
            "controls": controls,
            "definition_id": int(definition.id),
            "contract": contract.as_dict(),
            "watermark": watermark_json,
            "watermark_hash": canonical_watermark_hash(watermark),
            "expected_generation_key": expected_generation_key,
            "temporal_census": census,
            "temporal_census_count": len(census_rows),
            "temporal_census_hash": _canonical_hash(census_rows),
            "historical_certification_count": len(certifiable),
            "historical_certification_manifest_hash": certification_manifest_hash,
            "historical_certification_policy": "FULL_SEMANTIC_LINEAGE_REQUIRED_FAIL_CLOSED",
            "clean_evidence_count": len(clean_records),
            "clean_evidence_manifest_hash": clean_manifest_hash,
            "root_manifest_hash": root_manifest_hash,
            "clean_records": clean_records,
            "evidence_funnel": universe_a.counts(),
            "evidence_origins": dict(origins),
            "independent_sql_id_set_equal": True,
            "exclusion_checks": exclusions,
            "candidate_group_count": len(group_a),
            "candidate_group_plan_hash": group_plan_hash,
            "candidate_groups": group_a,
            "generation_10": generation_10,
            "generation_10_comparison": {
                "generation_10_distinct_outcomes": len(generation_10_set),
                "clean_distinct_outcomes": len(clean_id_set),
                "intersection": len(clean_id_set & generation_10_set),
                "removed": len(generation_10_set - clean_id_set),
                "added": len(clean_id_set - generation_10_set),
                "known_invalid_generation_10": len(known_invalid_10),
                "known_invalid_clean_overlap": 0,
            },
            "protected_before": protected,
            "historical_generation_hash": old_generation_hash,
            "historical_statistic_hash": old_statistic_hash,
            "historical_manifest_member_hash": old_manifest_member_hash,
            "repair_23_hash": repair_23_hash,
            "performance": {
                "first_evidence_load_seconds": first_seconds,
                "second_evidence_load_seconds": second_seconds,
                "plan_wall_seconds": perf_counter() - started,
            },
        }
        payload["artifact_hash"] = _artifact_hash(payload)
        path = output_dir / f"clean_plan_{payload['artifact_hash']}.json"
        _write(path, payload)
        db.rollback()
    print(json.dumps(_summary(path, payload), indent=2, sort_keys=True))
    return path


def build_candidate(
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
        raise RuntimeError("reviewed artifact hash mismatch")
    with SessionLocal() as db:
        controls = _control_state(db)
        _assert_controls(controls)
        current_protected = {table: _table_hash(db, table) for table in PROTECTED_TABLES}
        if current_protected != artifact["protected_before"]:
            raise RuntimeError("protected production state changed after review")
        definition = _active_definition(db)
        config = load_winner_probability_config()
        watermark_service = EvidenceWatermarkService()
        current_watermark = watermark_service.current_material_watermark(
            db, outcome_definition_id=definition.id
        )
        if current_watermark.as_dict() != artifact["watermark"]:
            raise RuntimeError("material evidence watermark changed after review")
        observed_at = datetime.fromisoformat(artifact["snapshot_at"].replace("Z", "+00:00"))
        advance = watermark_service.advance_to_current_material_evidence(
            db,
            outcome_definition=definition,
            config=config,
            observed_at=observed_at,
        )
        generation = CohortGenerationService().capture_or_resume(
            db,
            state=advance.state,
            contract=contract_for(definition, config),
            requested_at=observed_at,
        )
        if generation.generation_key != artifact["expected_generation_key"]:
            raise RuntimeError("candidate generation identity differs from reviewed plan")
        if generation.status == CohortGenerationStatus.PUBLISHED:
            raise RuntimeError("candidate generation is unexpectedly published")
        materializer = CohortMaterializationService()
        started = perf_counter()
        while generation.status == CohortGenerationStatus.BUILDING:
            result = materializer.materialize_slice(
                db,
                generation=generation,
                outcome_definition=definition,
                config=config,
                lease_guard=db.flush,
                should_cancel=lambda: False,
                max_groups=10_000,
                max_wall_seconds=600,
                publish_when_ready=False,
            )
            if result.continuation_required and result.groups_in_slice == 0:
                raise RuntimeError("candidate materialization made no progress")
        if generation.status != CohortGenerationStatus.READY:
            raise RuntimeError(f"candidate stopped in unexpected state {generation.status}")
        if generation.root_manifest_hash != artifact["root_manifest_hash"]:
            raise RuntimeError("materialized root manifest differs from reviewed plan")
        actual_groups = _materialized_group_plan(db, int(generation.id))
        if _canonical_hash(actual_groups) != artifact["candidate_group_plan_hash"]:
            raise RuntimeError("materialized cohort statistics differ from reviewed plan")
        audit = CohortGenerationService.audit_temporal_integrity(db, generation=generation)
        if not audit.clean:
            raise RuntimeError("candidate generation contains temporally ineligible evidence")
        generation.metrics_json = {
            **(generation.metrics_json or {}),
            "maintenance_actor": actor,
            "maintenance_request_key": request_key,
            "reviewed_artifact_hash": expected_hash,
            "publication_authorized": False,
        }
        generation_id = int(generation.id)
        db.flush()
        db.commit()
        elapsed = perf_counter() - started

    result_payload: dict[str, Any] = {
        "schema": "swinglens-winner-clean-candidate-result-v1",
        "reviewed_artifact_hash": expected_hash,
        "actor": actor,
        "request_key": request_key,
        "generation_id": generation_id,
        "generation_key": artifact["expected_generation_key"],
        "status": CohortGenerationStatus.READY,
        "root_manifest_hash": artifact["root_manifest_hash"],
        "group_plan_hash": artifact["candidate_group_plan_hash"],
        "materialization_wall_seconds": elapsed,
    }
    result_payload["artifact_hash"] = _artifact_hash(result_payload)
    path = output_dir / f"candidate_result_{result_payload['artifact_hash']}.json"
    _write(path, result_payload)
    print(json.dumps(_summary(path, result_payload), indent=2, sort_keys=True))
    return path


def verify(*, plan_path: Path, result_path: Path, output_dir: Path) -> Path:
    plan_artifact = json.loads(plan_path.read_text(encoding="utf-8"))
    result_artifact = json.loads(result_path.read_text(encoding="utf-8"))
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        controls = _control_state(db)
        _assert_controls(controls)
        generation = db.get(WinnerCohortGeneration, int(result_artifact["generation_id"]))
        if generation is None or generation.status != CohortGenerationStatus.READY:
            raise RuntimeError("candidate generation is missing or serving")
        checks = {
            "candidate_not_published": generation.published_at is None,
            "candidate_root_manifest_matches": (
                generation.root_manifest_hash == plan_artifact["root_manifest_hash"]
            ),
            "generation_1_10_unchanged": _hash_query(
                db,
                "SELECT row_to_json(t)::text FROM winner_cohort_generations t "
                "WHERE id <= 10 ORDER BY id",
            )
            == plan_artifact["historical_generation_hash"],
            "generation_1_10_statistics_unchanged": _hash_query(
                db,
                "SELECT row_to_json(t)::text FROM winner_cohort_statistics t "
                "WHERE generation_id <= 10 ORDER BY id",
            )
            == plan_artifact["historical_statistic_hash"],
            "generation_1_10_members_unchanged": _hash_query(
                db,
                "SELECT row_to_json(m)::text FROM winner_evidence_manifest_members m "
                "WHERE EXISTS (SELECT 1 FROM winner_cohort_statistics s "
                "WHERE s.generation_id <= 10 AND s.evidence_manifest_id=m.manifest_id) "
                "ORDER BY m.id",
            )
            == plan_artifact["historical_manifest_member_hash"],
            "repair_23_unchanged": _repair_23_hash(db) == plan_artifact["repair_23_hash"],
            "publication_pointer_unchanged": _generation_state(db, 10)["status"] == "PUBLISHED",
            "temporal_audit_clean": CohortGenerationService.audit_temporal_integrity(
                db, generation=generation
            ).clean,
        }
        for table in PROTECTED_TABLES:
            checks[f"protected_{table}_unchanged"] = (
                _table_hash(db, table) == plan_artifact["protected_before"][table]
            )
        if not all(checks.values()):
            raise RuntimeError(f"post-build verification failed: {checks}")
        payload: dict[str, Any] = {
            "schema": "swinglens-winner-clean-candidate-verification-v1",
            "plan_artifact_hash": plan_artifact["artifact_hash"],
            "result_artifact_hash": result_artifact["artifact_hash"],
            "generation_id": int(generation.id),
            "status": generation.status,
            "checks": checks,
            "controls": controls,
            "candidate_group_summary": _level_summary(db, int(generation.id)),
            "shadow_estimate_gate": _shadow_estimate_gate(db),
        }
        payload["artifact_hash"] = _artifact_hash(payload)
        path = output_dir / f"candidate_verification_{payload['artifact_hash']}.json"
        _write(path, payload)
        db.rollback()
    print(json.dumps(_summary(path, payload), indent=2, sort_keys=True))
    return path


def report(*, plan_path: Path, result_path: Path, output_dir: Path) -> Path:
    plan_artifact = json.loads(plan_path.read_text(encoding="utf-8"))
    result_artifact = json.loads(result_path.read_text(encoding="utf-8"))
    generation_id = int(result_artifact["generation_id"])
    config = load_winner_probability_config()
    thresholds = {level.level: level.min_effective_n for level in config.cohort.hierarchy}
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        controls = _control_state(db)
        _assert_controls(controls)
        generation = db.get(WinnerCohortGeneration, generation_id)
        if generation is None or generation.status != CohortGenerationStatus.READY:
            raise RuntimeError("candidate generation is not a non-serving READY artifact")
        old_metrics = _generation_population_metrics(db, 10)
        clean_metrics = _generation_population_metrics(db, generation_id)
        level_rows = _cohort_level_statistics(db, generation_id, thresholds)
        invalid_prediction_ids = list(
            db.scalars(
                text(
                    "WITH latest AS (SELECT DISTINCT ON(prediction_id) * "
                    "FROM winner_temporal_validity_decisions "
                    "ORDER BY prediction_id,validation_sequence DESC) "
                    "SELECT prediction_id FROM latest WHERE NOT evidence_eligible ORDER BY 1"
                )
            )
        )
        affected_estimate_ids = list(
            db.scalars(
                text(
                    "SELECT DISTINCT estimate_id FROM winner_estimate_evidence_members "
                    "WHERE prediction_id=ANY(:ids) ORDER BY estimate_id"
                ),
                {"ids": invalid_prediction_ids},
            )
        )
        generation_10_estimates = list(
            db.scalars(
                text(
                    "SELECT id FROM winner_probability_estimates "
                    "WHERE cohort_generation_id=10 ORDER BY id"
                )
            )
        )
        estimate_scope = sorted(set(affected_estimate_ids) | set(generation_10_estimates))
        payload: dict[str, Any] = {
            "schema": "swinglens-winner-clean-candidate-report-v1",
            "plan_artifact_hash": plan_artifact["artifact_hash"],
            "result_artifact_hash": result_artifact["artifact_hash"],
            "generation_id": generation_id,
            "generation_key": generation.generation_key,
            "generation_status": generation.status,
            "published_at": generation.published_at,
            "root_manifest_hash": generation.root_manifest_hash,
            "temporal_census": plan_artifact["temporal_census"],
            "historical_certification_count": plan_artifact["historical_certification_count"],
            "historical_certification_manifest_hash": plan_artifact[
                "historical_certification_manifest_hash"
            ],
            "clean_evidence_count": plan_artifact["clean_evidence_count"],
            "clean_evidence_manifest_hash": plan_artifact["clean_evidence_manifest_hash"],
            "generation_10_comparison": plan_artifact["generation_10_comparison"],
            "old_generation_metrics": old_metrics,
            "clean_generation_metrics": clean_metrics,
            "descriptive_delta": _metric_delta(old_metrics, clean_metrics),
            "candidate_level_statistics": level_rows,
            "previously_contaminated_estimate_count": len(affected_estimate_ids),
            "generation_10_estimate_count": len(generation_10_estimates),
            "minimum_shadow_replacement_scope_count": len(estimate_scope),
            "candidate_estimate_count": 0,
            "shadow_estimate_gate": _shadow_estimate_gate(db),
            "controls": controls,
        }
        payload["artifact_hash"] = _artifact_hash(payload)
        path = output_dir / f"candidate_report_{payload['artifact_hash']}.json"
        _write(path, payload)
        db.rollback()
    print(json.dumps(_summary(path, payload), indent=2, sort_keys=True))
    return path


def _census_rows(db) -> list[dict[str, Any]]:
    sql = text(
        """
        WITH latest AS (
          SELECT DISTINCT ON (prediction_id) *
          FROM winner_temporal_validity_decisions
          ORDER BY prediction_id, validation_sequence DESC
        )
        SELECT p.id AS prediction_id, o.id AS outcome_id, p.ticker,
          CASE
            WHEN d.id IS NOT NULL AND d.status='VALID' AND d.evidence_eligible
              THEN 'EXPLICIT_VALID_LEDGER'
            WHEN d.id IS NOT NULL THEN 'EXPLICIT_INVALID_LEDGER'
            WHEN p.decision_at IS NOT NULL
              AND p.lineage_json#>>'{point_in_time_validation,semantic_input_time}'='VALID'
              AND p.lineage_json->>'point_in_time_validated'='true'
              AND p.source_data_cutoff_at <= p.decision_at
              AND p.decision_at < ((p.planned_entry_session::timestamp + time '09:30')
                  AT TIME ZONE 'America/New_York')
              THEN 'NATIVE_TEMPORAL_VALID'
            WHEN p.captured_at >= ((p.planned_entry_session::timestamp + time '09:30')
                  AT TIME ZONE 'America/New_York') THEN 'EXECUTION_INVALID'
            WHEN EXISTS (
              SELECT 1 FROM winner_market_data_obligations ob
              WHERE ob.prediction_id=p.id AND ob.status='IDENTITY_BLOCKED'
            ) THEN 'IDENTITY_BLOCKED'
            WHEN p.lineage_json#>>'{point_in_time_validation,semantic_input_time}' IN
                 ('INVALID','LOOKAHEAD_INVALID') THEN 'LOOKAHEAD_INVALID'
            ELSE 'TEMPORAL_LINEAGE_UNRESOLVED'
          END AS temporal_class
        FROM winner_forward_outcomes o
        JOIN winner_prediction_snapshots p ON p.id=o.prediction_id
        LEFT JOIN latest d ON d.prediction_id=p.id
        WHERE o.is_current_revision AND o.entry_model='NEXT_OPEN'
          AND o.horizon_sessions=5 AND o.status='MATURED'
        ORDER BY p.id
        """
    )
    return [dict(row) for row in db.execute(sql).mappings()]


def _independent_census(db) -> dict[str, int]:
    # This verifier independently aggregates in SQL and never consumes the
    # service-side per-row classification produced by ``_census_rows``.
    rows = db.execute(
        text(
            """
            WITH current_outcomes AS (
              SELECT f.id AS outcome_id,p.*,
                (SELECT d.id FROM winner_temporal_validity_decisions d
                 WHERE d.prediction_id=p.id
                 ORDER BY d.validation_sequence DESC LIMIT 1) AS temporal_id
              FROM winner_forward_outcomes f
              JOIN winner_prediction_snapshots p ON p.id=f.prediction_id
              WHERE f.is_current_revision AND f.entry_model='NEXT_OPEN'
                AND f.horizon_sessions=5 AND f.status='MATURED'
            ), classified AS (
              SELECT c.outcome_id,
                CASE
                  WHEN d.id IS NOT NULL AND d.status='VALID' AND d.evidence_eligible
                    THEN 'EXPLICIT_VALID_LEDGER'
                  WHEN d.id IS NOT NULL THEN 'EXPLICIT_INVALID_LEDGER'
                  WHEN c.decision_at IS NOT NULL
                    AND c.lineage_json#>>'{point_in_time_validation,semantic_input_time}'='VALID'
                    AND c.lineage_json->>'point_in_time_validated'='true'
                    AND c.source_data_cutoff_at <= c.decision_at
                    AND c.decision_at < ((c.planned_entry_session::timestamp + time '09:30')
                        AT TIME ZONE 'America/New_York')
                    THEN 'NATIVE_TEMPORAL_VALID'
                  WHEN c.captured_at >= ((c.planned_entry_session::timestamp + time '09:30')
                        AT TIME ZONE 'America/New_York') THEN 'EXECUTION_INVALID'
                  WHEN EXISTS (SELECT 1 FROM winner_market_data_obligations ob
                    WHERE ob.prediction_id=c.id AND ob.status='IDENTITY_BLOCKED')
                    THEN 'IDENTITY_BLOCKED'
                  WHEN c.lineage_json#>>'{point_in_time_validation,semantic_input_time}' IN
                       ('INVALID','LOOKAHEAD_INVALID') THEN 'LOOKAHEAD_INVALID'
                  ELSE 'TEMPORAL_LINEAGE_UNRESOLVED'
                END AS temporal_class
              FROM current_outcomes c
              LEFT JOIN winner_temporal_validity_decisions d ON d.id=c.temporal_id
            )
            SELECT temporal_class,count(*) FROM classified GROUP BY temporal_class
            """
        )
    ).all()
    return {str(name): int(count) for name, count in rows}


def _independent_clean_ids(
    db,
    *,
    definition_id: int,
    cutoff: datetime,
    completed_session,
    rolling_start,
    watermark: EvidenceWatermark,
    feature_schema_version: str,
    calculation_version: str,
    config_hash: str,
) -> tuple[int, ...]:
    rows = db.scalars(
        text(
            """
            WITH latest_temporal AS (
              SELECT DISTINCT ON (prediction_id) *
              FROM winner_temporal_validity_decisions
              WHERE id <= :temporal_max
              ORDER BY prediction_id, validation_sequence DESC
            ), eligible AS (
              SELECT f.id AS outcome_id, p.id AS prediction_id,
                row_number() OVER (
                  PARTITION BY coalesce(p.episode_id, -p.id)
                  ORDER BY p.prediction_as_of_date,p.id,f.revision,t.revision
                ) AS episode_rank
              FROM winner_prediction_snapshots p
              JOIN winner_forward_outcomes f ON f.prediction_id=p.id
              JOIN winner_target_stop_outcomes t ON t.forward_outcome_id=f.id
              JOIN latest_temporal d ON d.prediction_id=p.id
              WHERE f.id <= :forward_max AND t.id <= :target_max
                AND t.outcome_definition_id=:definition_id
                AND p.source_data_cutoff_at < :cutoff
                AND (p.superseded_at IS NULL OR p.superseded_at >= :cutoff)
                AND f.due_session <= :completed_session
                AND f.status='MATURED' AND f.matured_at < :cutoff
                AND (f.superseded_at IS NULL OR f.superseded_at >= :cutoff)
                AND f.entry_model='NEXT_OPEN' AND f.horizon_sessions=5
                AND t.status='MATURED' AND t.evaluated_at < :cutoff
                AND (t.superseded_at IS NULL OR t.superseded_at >= :cutoff)
                AND t.entry_model='NEXT_OPEN' AND t.horizon_sessions=5
                AND t.primary_winner IS NOT NULL
                AND p.eligibility_status='ELIGIBLE'
                AND p.lineage_json->>'point_in_time_validated'='true'
                AND d.status='VALID' AND d.evidence_eligible
                AND d.entry_timing_valid AND d.source_cutoff_valid
                AND d.semantic_input_time_valid IS TRUE
                AND d.decision_at < d.entry_open_at
                AND p.reconstruction_method IS NULL
                AND p.lineage_json->>'capture_training_candidate'='true'
                AND p.feature_schema_version=:feature_schema_version
                AND p.calculation_version=:calculation_version
                AND p.config_hash=:config_hash
                AND NOT coalesce(p.lineage_json->'source_quality_flags','[]'::jsonb)
                    ?| ARRAY['quality_blocking','invalid_source','exclude_from_production_training']
                AND NOT coalesce(p.warning_flags_json,'[]'::jsonb)
                    ?| ARRAY['quality_blocking','invalid_source','exclude_from_production_training']
                AND p.prediction_as_of_date >= :rolling_start
                AND f.source_revision_cutoff_at IS NOT NULL
                AND f.source_revision_cutoff_at <= :cutoff
                AND coalesce((p.lineage_json->>'dependent_episode')::boolean,false)=false
            )
            SELECT outcome_id FROM eligible WHERE episode_rank=1 ORDER BY outcome_id
            """
        ),
        {
            "temporal_max": watermark.temporal_validity_decision_id,
            "forward_max": watermark.forward_revision_id,
            "target_max": watermark.target_stop_revision_id,
            "definition_id": definition_id,
            "cutoff": cutoff,
            "completed_session": completed_session,
            "rolling_start": rolling_start,
            "feature_schema_version": feature_schema_version,
            "calculation_version": calculation_version,
            "config_hash": config_hash,
        },
    )
    return tuple(sorted(int(value) for value in rows))


def _clean_records(db, outcome_ids: tuple[int, ...]) -> list[dict[str, Any]]:
    if not outcome_ids:
        return []
    sql = text(
        """
        WITH latest AS (
          SELECT DISTINCT ON (prediction_id) * FROM winner_temporal_validity_decisions
          ORDER BY prediction_id, validation_sequence DESC
        )
        SELECT f.id AS forward_outcome_id,p.id AS prediction_id,p.ticker,p.calculation_version,
          d.id AS temporal_validity_decision_id,f.entry_model,f.horizon_sessions,
          p.setup_family,p.setup_classification,p.ranking_profile,p.market_regime,
          p.market_risk_state,p.sector_state,f.entry_session,f.due_session,f.matured_at,
          t.primary_winner,t.first_event,f.close_return_pct,f.mfe_pct,f.mae_pct,
          f.source_bar_lineage_hash,f.source_revision_cutoff_at,p.feature_vector_hash,
          p.source_ids_json,p.feature_json
        FROM winner_forward_outcomes f
        JOIN winner_prediction_snapshots p ON p.id=f.prediction_id
        JOIN winner_target_stop_outcomes t ON t.forward_outcome_id=f.id
        JOIN latest d ON d.prediction_id=p.id
        WHERE f.id=ANY(:ids) AND t.outcome_definition_id=3 AND t.is_current_revision
        ORDER BY f.id
        """
    )
    return [
        canonicalize_manifest_value(dict(row))
        for row in db.execute(sql, {"ids": list(outcome_ids)}).mappings()
    ]


def _group_plan(evidence, config) -> list[dict[str, Any]]:
    definitions = CohortDefinitionService()
    statistics = CohortStatisticsService()
    mutable: dict[str, tuple[Any, list[Any]]] = {}
    for row in evidence:
        for key in definitions.cohort_keys_for_features(row.prediction.feature_json or {}, config):
            mutable.setdefault(key.key, (key, []))[1].append(row)
    for key in definitions.cohort_keys_for_features({}, config):
        if key.level == config.cohort.hierarchy[-1].level:
            mutable.setdefault(key.key, (key, []))
    rank = {level.level: index for index, level in enumerate(config.cohort.hierarchy)}
    ordered = sorted(mutable.values(), key=lambda item: (-rank[item[0].level], item[0].key))
    result = []
    for key, rows in ordered:
        frozen = tuple(rows)
        stats = statistics.calculate(frozen, config)
        positive = sum(
            Decimal(str(row.inclusion_weight))
            for row in frozen
            if row.forward_outcome.close_return_pct is not None
            and row.forward_outcome.close_return_pct > 0
        )
        result.append(
            canonicalize_manifest_value(
                {
                    "cohort_key": key.key,
                    "level": key.level,
                    "dimensions": key.dimensions,
                    "member_count": stats.sample_n,
                    "effective_n": stats.effective_n,
                    "winner_count": stats.wins,
                    "weighted_winner_rate": stats.raw_rate,
                    "positive_return_rate": (
                        positive / stats.effective_n if stats.effective_n else None
                    ),
                    "mean_return": stats.mean_return_pct,
                    "median_return": stats.median_return_pct,
                    "median_mfe": stats.median_mfe_pct,
                    "median_mae": stats.median_mae_pct,
                    "posterior_probability": stats.posterior_probability,
                    "lower_bound": stats.lower_bound,
                    "upper_bound": stats.upper_bound,
                    "evidence_grade": stats.evidence_grade,
                    "manifest_hash": _hash_payload(_manifest_payload(frozen)),
                }
            )
        )
    return result


def _materialized_group_plan(db, generation_id: int) -> list[dict[str, Any]]:
    rows = list(
        db.execute(
            text(
                """
            SELECT d.cohort_key,d.level,d.dimensions_json AS dimensions,
              s.sample_n AS member_count,
              s.effective_n,s.wins AS winner_count,s.raw_rate AS weighted_winner_rate,
              s.metadata_json->>'mean_return_pct' AS mean_return,
              s.median_return_pct AS median_return,s.median_mfe_pct AS median_mfe,
              s.median_mae_pct AS median_mae,s.posterior_probability,s.lower_bound,s.upper_bound,
              s.evidence_grade,s.evidence_manifest_hash AS manifest_hash,
              s.evidence_manifest_id
            FROM winner_cohort_statistics s
            JOIN winner_cohort_definitions d ON d.id=s.cohort_definition_id
            WHERE s.generation_id=:generation_id
            ORDER BY CASE d.level WHEN 'L5' THEN 0 WHEN 'L4' THEN 1 WHEN 'L3' THEN 2
              WHEN 'L2' THEN 3 WHEN 'L1' THEN 4 ELSE 5 END,d.cohort_key
            """
            ),
            {"generation_id": generation_id},
        ).mappings()
    )
    positive_by_manifest = {
        int(manifest_id): (int(positive_count), int(member_count))
        for manifest_id, positive_count, member_count in db.execute(
            text(
                """
                SELECT s.evidence_manifest_id,
                  count(*) FILTER (WHERE f.close_return_pct > 0) AS positive_count,
                  count(*) AS member_count
                FROM winner_cohort_statistics s
                LEFT JOIN winner_evidence_manifest_members m
                  ON m.manifest_id=s.evidence_manifest_id
                LEFT JOIN winner_forward_outcomes f ON f.id=m.forward_outcome_id
                WHERE s.generation_id=:generation_id
                GROUP BY s.evidence_manifest_id
                """
            ),
            {"generation_id": generation_id},
        )
    }
    result = []
    for row in rows:
        record = dict(row)
        manifest_id = int(record.pop("evidence_manifest_id"))
        positive, member_count = positive_by_manifest.get(manifest_id, (0, 0))
        record["positive_return_rate"] = (
            Decimal(positive) / Decimal(member_count) if member_count else None
        )
        result.append(canonicalize_manifest_value(record))
    return result


def _clean_exclusion_checks(db, outcome_ids: tuple[int, ...]) -> dict[str, int]:
    if not outcome_ids:
        return {"quarantined": 0, "clbk": 0, "temporal_invalid": 0, "unresolved": 0}
    params = {"ids": list(outcome_ids)}
    base = (
        "FROM winner_forward_outcomes f JOIN winner_prediction_snapshots p ON p.id=f.prediction_id "
        "WHERE f.id=ANY(:ids)"
    )
    return {
        "quarantined": int(
            db.scalar(
                text(
                    "WITH latest AS (SELECT DISTINCT ON(prediction_id) * FROM winner_temporal_validity_decisions ORDER BY prediction_id,validation_sequence DESC) SELECT count(*) "
                    + base
                    + " AND EXISTS (SELECT 1 FROM latest d WHERE d.prediction_id=p.id AND NOT d.evidence_eligible)"
                ),
                params,
            )
            or 0
        ),
        "clbk": int(
            db.scalar(text("SELECT count(*) " + base + " AND p.ticker='CLBK'"), params) or 0
        ),
        "temporal_invalid": int(
            db.scalar(
                text(
                    "WITH latest AS (SELECT DISTINCT ON(prediction_id) * FROM winner_temporal_validity_decisions ORDER BY prediction_id,validation_sequence DESC) SELECT count(*) "
                    + base
                    + " AND NOT EXISTS (SELECT 1 FROM latest d WHERE d.prediction_id=p.id AND d.status='VALID' AND d.evidence_eligible)"
                ),
                params,
            )
            or 0
        ),
        "unresolved": int(
            db.scalar(
                text(
                    "WITH latest AS (SELECT DISTINCT ON(prediction_id) * FROM winner_temporal_validity_decisions ORDER BY prediction_id,validation_sequence DESC) SELECT count(*) "
                    + base
                    + " AND NOT EXISTS (SELECT 1 FROM latest d WHERE d.prediction_id=p.id)"
                ),
                params,
            )
            or 0
        ),
    }


def _control_state(db) -> dict[str, Any]:
    from app.services.winner_probability.outcome_orchestration_service import (
        H5NextOpenOrchestrationService,
    )

    queue = H5NextOpenOrchestrationService().queue_state(db)
    return {
        "alembic_head": db.scalar(text("SELECT version_num FROM alembic_version")),
        "active_winner_jobs": int(
            db.scalar(
                text(
                    "SELECT count(*) FROM background_jobs WHERE status IN ('PENDING','RUNNING','RETRYING') AND job_type LIKE 'WINNER%'"
                )
            )
            or 0
        ),
        "due_total": int(queue.due_total),
        "retry_eligible_now": int(queue.retry_eligible_now),
        "retry_deferred": int(queue.retry_deferred),
        "valid_ledger": int(
            db.scalar(
                text(
                    "WITH latest AS (SELECT DISTINCT ON(prediction_id) * FROM winner_temporal_validity_decisions ORDER BY prediction_id,validation_sequence DESC) SELECT count(*) FROM latest WHERE status='VALID' AND evidence_eligible"
                )
            )
            or 0
        ),
        "quarantined": int(
            db.scalar(
                text(
                    "WITH latest AS (SELECT DISTINCT ON(prediction_id) * FROM winner_temporal_validity_decisions ORDER BY prediction_id,validation_sequence DESC) SELECT count(*) FROM latest WHERE NOT evidence_eligible"
                )
            )
            or 0
        ),
        "clbk_total": int(
            db.scalar(
                text(
                    "SELECT count(*) FROM winner_forward_outcomes f JOIN winner_prediction_snapshots p ON p.id=f.prediction_id WHERE f.is_current_revision AND f.entry_model='NEXT_OPEN' AND f.horizon_sessions=5 AND p.ticker='CLBK'"
                )
            )
            or 0
        ),
    }


def _assert_controls(controls: dict[str, Any]) -> None:
    expected = {
        "alembic_head": EXPECTED_ALEMBIC_HEAD,
        "active_winner_jobs": 0,
        "due_total": EXPECTED_DUE,
        "retry_eligible_now": 0,
        "retry_deferred": 0,
        "valid_ledger": EXPECTED_VALID_LEDGER,
        "quarantined": EXPECTED_QUARANTINED,
    }
    mismatches = {
        key: (controls.get(key), value)
        for key, value in expected.items()
        if controls.get(key) != value
    }
    if mismatches:
        raise RuntimeError(f"production control state mismatch: {mismatches}")


def _active_definition(db) -> WinnerOutcomeDefinition:
    row = db.scalar(
        select(WinnerOutcomeDefinition)
        .where(WinnerOutcomeDefinition.definition_id == "T2_5_S2_0_H5_NEXT_OPEN")
        .where(WinnerOutcomeDefinition.is_active.is_(True))
    )
    if row is None:
        raise RuntimeError("active H5 NEXT_OPEN definition not found")
    return row


def _generation_state(db, generation_id: int) -> dict[str, Any]:
    row = db.get(WinnerCohortGeneration, generation_id)
    if row is None:
        raise RuntimeError(f"generation {generation_id} not found")
    return canonicalize_manifest_value(
        {
            "id": int(row.id),
            "generation_key": row.generation_key,
            "status": row.status,
            "root_manifest_hash": row.root_manifest_hash,
            "watermark_hash": row.watermark_hash,
            "evidence_row_count": row.evidence_row_count,
            "published_at": row.published_at,
        }
    )


def _generation_root_ids(db, generation_id: int) -> tuple[int, ...]:
    return tuple(
        int(value)
        for value in db.scalars(
            text(
                "SELECT m.forward_outcome_id FROM winner_evidence_manifest_members m "
                "JOIN winner_cohort_statistics s ON s.evidence_manifest_id=m.manifest_id "
                "WHERE s.generation_id=:generation_id AND s.metadata_json->>'cohort_level'='L5' "
                "ORDER BY m.forward_outcome_id"
            ),
            {"generation_id": generation_id},
        )
    )


def _generation_invalid_ids(db, generation_id: int) -> tuple[int, ...]:
    return tuple(
        int(value)
        for value in db.scalars(
            text(
                "WITH latest AS (SELECT DISTINCT ON(prediction_id) * FROM winner_temporal_validity_decisions ORDER BY prediction_id,validation_sequence DESC) "
                "SELECT DISTINCT m.forward_outcome_id FROM winner_evidence_manifest_members m "
                "JOIN winner_cohort_statistics s ON s.evidence_manifest_id=m.manifest_id "
                "JOIN latest d ON d.prediction_id=m.prediction_id "
                "WHERE s.generation_id=:generation_id AND NOT d.evidence_eligible ORDER BY 1"
            ),
            {"generation_id": generation_id},
        )
    )


def _level_summary(db, generation_id: int) -> list[dict[str, Any]]:
    return [
        canonicalize_manifest_value(dict(row))
        for row in db.execute(
            text(
                "SELECT d.level,count(*) AS cohort_count,min(s.sample_n) AS min_members,max(s.sample_n) AS max_members,sum(s.sample_n) AS membership_occurrences FROM winner_cohort_statistics s JOIN winner_cohort_definitions d ON d.id=s.cohort_definition_id WHERE s.generation_id=:generation_id GROUP BY d.level ORDER BY d.level DESC"
            ),
            {"generation_id": generation_id},
        ).mappings()
    ]


def _generation_population_metrics(db, generation_id: int) -> dict[str, Any]:
    row = (
        db.execute(
            text(
                """
            WITH root_manifest AS (
              SELECT s.evidence_manifest_id
              FROM winner_cohort_statistics s
              WHERE s.generation_id=:generation_id
                AND s.metadata_json->>'cohort_level'='L5'
            )
            SELECT count(*) AS member_count,
              count(*) FILTER (WHERE m.primary_winner) AS winner_count,
              avg(m.inclusion_weight) AS mean_weight,
              sum(m.inclusion_weight) FILTER (WHERE m.primary_winner)
                / nullif(sum(m.inclusion_weight),0) AS weighted_winner_rate,
              count(*) FILTER (WHERE f.close_return_pct>0)::numeric
                / nullif(count(*),0) AS positive_return_rate,
              avg(f.close_return_pct) AS mean_return,
              percentile_cont(0.5) WITHIN GROUP (ORDER BY f.close_return_pct)
                AS median_return,
              percentile_cont(0.5) WITHIN GROUP (ORDER BY f.mfe_pct) AS median_mfe,
              percentile_cont(0.5) WITHIN GROUP (ORDER BY f.mae_pct) AS median_mae
            FROM winner_evidence_manifest_members m
            JOIN root_manifest r ON r.evidence_manifest_id=m.manifest_id
            JOIN winner_forward_outcomes f ON f.id=m.forward_outcome_id
            """
            ),
            {"generation_id": generation_id},
        )
        .mappings()
        .one()
    )
    return canonicalize_manifest_value(dict(row))


def _cohort_level_statistics(
    db, generation_id: int, thresholds: dict[str, int]
) -> list[dict[str, Any]]:
    rows = list(
        db.execute(
            text(
                """
                SELECT d.level,count(*) AS cohort_count,min(s.sample_n) AS min_cohort_size,
                  max(s.sample_n) AS max_cohort_size,sum(s.sample_n) AS membership_occurrences,
                  sum(s.wins) AS winner_count,
                  sum(s.wins)/nullif(sum(s.effective_n),0) AS weighted_winner_rate,
                  count(*) FILTER (WHERE s.evidence_grade='Insufficient')
                    AS insufficient_grade_count
                FROM winner_cohort_statistics s
                JOIN winner_cohort_definitions d ON d.id=s.cohort_definition_id
                WHERE s.generation_id=:generation_id
                GROUP BY d.level ORDER BY d.level DESC
                """
            ),
            {"generation_id": generation_id},
        ).mappings()
    )
    global_metrics = _generation_population_metrics(db, generation_id)
    result = []
    for row in rows:
        record = dict(row)
        threshold = thresholds[str(record["level"])]
        record["minimum_effective_n"] = threshold
        record["below_level_minimum_count"] = int(
            db.scalar(
                text(
                    "SELECT count(*) FROM winner_cohort_statistics s "
                    "JOIN winner_cohort_definitions d ON d.id=s.cohort_definition_id "
                    "WHERE s.generation_id=:generation_id AND d.level=:level "
                    "AND s.effective_n<:threshold"
                ),
                {
                    "generation_id": generation_id,
                    "level": record["level"],
                    "threshold": threshold,
                },
            )
            or 0
        )
        for key in (
            "positive_return_rate",
            "mean_return",
            "median_return",
            "median_mfe",
            "median_mae",
        ):
            record[key] = global_metrics[key]
        result.append(canonicalize_manifest_value(record))
    return result


def _metric_delta(old: dict[str, Any], clean: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in (
        "weighted_winner_rate",
        "positive_return_rate",
        "mean_return",
        "median_return",
        "median_mfe",
        "median_mae",
    ):
        old_value = Decimal(str(old[key]))
        clean_value = Decimal(str(clean[key]))
        result[key] = canonicalize_manifest_value(clean_value - old_value)
    return result


def _shadow_estimate_gate(db) -> dict[str, Any]:
    # The IB research journal has a direct newest-created estimate lookup and
    # does not filter cohort generation publication state. Appending READY-
    # generation estimates could therefore change a serving consumer.
    return {
        "safe_to_persist_shadow_estimates": False,
        "blocking_consumer": "app.services.ib_market_intelligence.journal",
        "reason": "latest estimate lookup is not publication-state filtered",
        "existing_estimate_count": int(
            db.scalar(text("SELECT count(*) FROM winner_probability_estimates")) or 0
        ),
        "required_design": "durable estimate lifecycle status plus serving-only predicates for every consumer",
    }


def _repair_23_hash(db) -> dict[str, Any]:
    return _hash_query(
        db,
        "SELECT row_to_json(t)::text FROM winner_target_stop_outcomes t "
        "WHERE t.metadata_json->>'repair_type'='MATURATION_SCOPE_LEAK_CORRECTION' ORDER BY t.id",
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


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_manifest_bytes(value)).hexdigest()


def _artifact_hash(payload: dict[str, Any]) -> str:
    return _canonical_hash({key: value for key, value in payload.items() if key != "artifact_hash"})


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(path)
    path.write_bytes(canonical_manifest_bytes(payload) + b"\n")


def _summary(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "artifact_hash",
        "temporal_census",
        "clean_evidence_count",
        "root_manifest_hash",
        "candidate_group_count",
        "generation_id",
        "generation_key",
        "status",
        "checks",
        "shadow_estimate_gate",
    )
    return {"path": str(path.resolve()), **{key: payload[key] for key in keys if key in payload}}


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    plan_parser = sub.add_parser("plan")
    plan_parser.add_argument("--output-dir", type=Path, required=True)
    build_parser = sub.add_parser("build-candidate")
    build_parser.add_argument("--artifact", type=Path, required=True)
    build_parser.add_argument("--expected-hash", required=True)
    build_parser.add_argument("--actor", required=True)
    build_parser.add_argument("--request-key", required=True)
    build_parser.add_argument("--approve-write", action="store_true")
    build_parser.add_argument("--output-dir", type=Path, required=True)
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--plan", type=Path, required=True)
    verify_parser.add_argument("--result", type=Path, required=True)
    verify_parser.add_argument("--output-dir", type=Path, required=True)
    report_parser = sub.add_parser("report")
    report_parser.add_argument("--plan", type=Path, required=True)
    report_parser.add_argument("--result", type=Path, required=True)
    report_parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "plan":
        plan(output_dir=args.output_dir)
    elif args.command == "build-candidate":
        build_candidate(
            artifact_path=args.artifact,
            expected_hash=args.expected_hash,
            actor=args.actor,
            request_key=args.request_key,
            approve_write=args.approve_write,
            output_dir=args.output_dir,
        )
    elif args.command == "verify":
        verify(plan_path=args.plan, result_path=args.result, output_dir=args.output_dir)
    else:
        report(plan_path=args.plan, result_path=args.result, output_dir=args.output_dir)


if __name__ == "__main__":
    main()
