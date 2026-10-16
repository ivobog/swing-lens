"""Export deterministic read-only Phase 2 certification fixtures for pipeline runs.

The output is non-authoritative test evidence. The exporter opens PostgreSQL with
``default_transaction_read_only=on`` and never writes application state.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.settings import get_settings

FIXTURE_SCHEMA = "swinglens-readiness-phase2-baseline-v1"
BASELINE_REVISION = "6656c55dd59c294c2ea471571863046e02bf7c35"


def _query(connection: psycopg.Connection, sql: str, parameters: tuple[Any, ...]) -> list[dict]:
    return list(connection.execute(sql, parameters).fetchall())


def _compact_readiness(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "producer": value.get("producer"),
        "status": value.get("status"),
        "blocking_reasons": value.get("blocking_reasons") or [],
        "warning_reasons": value.get("warning_reasons") or [],
        "readiness_policy_version": value.get("readiness_policy_version"),
        "calculation_identity_fingerprint": value.get(
            "calculation_identity_fingerprint"
        ),
        "fingerprint": _section_fingerprint(value),
    }


def _compact_consumer_eligibility(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    compact: dict[str, Any] = {}
    for source, item in sorted(value.items()):
        if not isinstance(item, dict):
            continue
        decision = item.get("decision") or {}
        readiness = item.get("producer_readiness") or {}
        compact[source] = {
            "requirement": item.get("requirement"),
            "included": item.get("included"),
            "source_id": item.get("source_id"),
            "decision_status": decision.get("status"),
            "decision_reasons": decision.get("reasons") or [],
            "decision_policy_version": decision.get("policy_version"),
            "producer_readiness_status": readiness.get("status"),
            "producer_readiness_fingerprint": decision.get(
                "producer_readiness_fingerprint"
            ),
        }
    return compact


def _section_fingerprint(value: Any) -> str:
    return Canonical.fingerprint(value)


def export_run(connection: psycopg.Connection, run_id: int) -> dict[str, Any]:
    pipeline_rows = _query(
        connection,
        """
        SELECT id, upload_run_id, status, current_step, started_at, completed_at,
               message, error_message, result_json, execution_authority_state,
               execution_authority_reason
        FROM pipeline_runs WHERE id = %s
        """,
        (run_id,),
    )
    if len(pipeline_rows) != 1:
        raise ValueError(f"pipeline run {run_id} was not found exactly once")

    fundamentals = _query(
        connection,
        """
        SELECT f.id, f.run_id, f.ticker, f.evidence_id, f.missing_data_penalty,
               f.fundamental_score, f.fundamental_label, f.data_coverage_score,
               f.scoring_model_version, f.v2_warning_flags_json,
               f.debug_json->'coverage' AS coverage,
               f.debug_json->'component_coverage' AS component_coverage,
               f.debug_json->'parse_diagnostics' AS parse_diagnostics,
               e.calculation_identity_fingerprint, e.calculated_at AS decision_cutoff,
               e.payload_json->'producer_readiness' AS producer_readiness
        FROM fundamental_scores f
        LEFT JOIN core_calculation_evidence e ON e.id = f.evidence_id
        WHERE f.run_id = %s ORDER BY f.ticker, f.id
        """,
        (run_id,),
    )
    combined = _query(
        connection,
        """
        SELECT c.id, c.run_id, c.ticker, c.sector, c.evidence_id, c.final_score,
               c.fundamental_score, c.dual_score, c.is_complete,
               c.has_fundamental, c.has_technical, c.has_warning,
               c.warning_flags_json, c.combined_decision,
               e.calculation_identity_fingerprint,
               e.payload_json->'producer_readiness' AS producer_readiness
        FROM combined_results c
        LEFT JOIN core_calculation_evidence e ON e.id = c.evidence_id
        WHERE c.run_id = %s ORDER BY c.ticker, c.id
        """,
        (run_id,),
    )
    rankings = _query(
        connection,
        """
        SELECT r.id, r.run_id, r.ticker, r.ranking_profile, r.evidence_id,
               r.profile_score, r.is_complete, r.has_fundamental,
               r.has_technical, r.has_warning, r.warning_flags_json,
               e.calculation_identity_fingerprint,
               e.payload_json->'producer_readiness' AS producer_readiness
        FROM ranking_results r
        LEFT JOIN core_calculation_evidence e ON e.id = r.evidence_id
        WHERE r.run_id = %s
        ORDER BY r.ticker, r.ranking_profile, r.id
        """,
        (run_id,),
    )
    winners = _query(
        connection,
        """
        SELECT id, run_id, raw_row_id, ticker, combined_result_id,
               ranking_result_id, decision_at, prediction_as_of_date,
               source_data_cutoff_at, captured_at, eligibility_status,
               exclusion_reason, feature_schema_version, feature_vector_hash,
               calculation_version, config_hash, warning_flags_json,
               source_ids_json,
               lineage_json->'winner_consumer_eligibility' AS consumer_eligibility
        FROM winner_prediction_snapshots
        WHERE run_id = %s ORDER BY ticker, id
        """,
        (run_id,),
    )
    ceri = _query(
        connection,
        """
        SELECT s.id, s.run_id, s.ticker, s.evidence_id, s.as_of_session,
               s.cutoff_at, s.calculation_context_id, s.opportunity_score,
               s.opportunity_coverage_pct, s.opportunity_unrated_reason,
               s.event_risk_score, s.data_confidence, s.coverage_pct,
               s.posture, s.warnings_json, s.config_version, s.config_hash,
               s.calculation_version, s.evidence_contract_version,
               s.evidence_hash, s.hash_schema_version,
               e.calculation_identity_fingerprint,
               d.disposition, d.reason_code AS disposition_reason,
               d.incident_reference, d.actor_source
        FROM ceri_score_snapshots s
        LEFT JOIN core_calculation_evidence e ON e.id = s.evidence_id
        LEFT JOIN LATERAL (
            SELECT disposition, reason_code, incident_reference, actor_source
            FROM ceri_evidence_dispositions
            WHERE ceri_snapshot_id = s.id
            ORDER BY created_at DESC, id DESC LIMIT 1
        ) d ON TRUE
        WHERE s.run_id = %s ORDER BY s.ticker, s.id
        """,
        (run_id,),
    )

    for rows in (fundamentals, combined, rankings):
        for row in rows:
            row["producer_readiness"] = _compact_readiness(row["producer_readiness"])
    for row in winners:
        row["consumer_eligibility"] = _compact_consumer_eligibility(
            row["consumer_eligibility"]
        )

    pipeline = dict(pipeline_rows[0])
    result = pipeline.pop("result_json") or {}
    pipeline["result_summary"] = {
        key: result.get(key)
        for key in (
            "raw_rows",
            "fundamental_scores",
            "technical_scores",
            "combined_results",
            "incomplete_rows",
            "warning_rows",
            "ranking_results",
            "ib_success_count",
            "ib_failure_count",
            "winner_prediction_inserted",
            "winner_prediction_excluded",
            "winner_prediction_failed",
            "winner_prediction_exclusion_reasons",
            "ceri_certified_capture_count",
            "ceri_failed_count",
        )
        if key in result
    }
    sections = {
        "pipeline": pipeline,
        "fundamentals": fundamentals,
        "combined": combined,
        "rankings": rankings,
        "winner_predictions": winners,
        "ceri": ceri,
    }
    summary = {
        "fundamental_count": len(fundamentals),
        "fundamental_positive_penalty_count": sum(
            row["missing_data_penalty"] not in (None, 0) for row in fundamentals
        ),
        "combined_count": len(combined),
        "combined_incomplete_count": sum(not row["is_complete"] for row in combined),
        "combined_complete_warning_count": sum(
            row["is_complete"] and row["has_warning"] for row in combined
        ),
        "ranking_count": len(rankings),
        "winner_prediction_count": len(winners),
        "winner_excluded_count": int(result.get("winner_prediction_excluded", 0) or 0),
        "winner_exclusion_reasons": result.get("winner_prediction_exclusion_reasons") or {},
        "ceri_capture_count": len(ceri),
        "ceri_excluded_count": sum(row["disposition"] == "EXCLUDED" for row in ceri),
        "ceri_confidence_counts": {
            label: sum(row["data_confidence"] == label for row in ceri)
            for label in sorted({row["data_confidence"] for row in ceri})
        },
    }
    payload = {
        "fixture_schema": FIXTURE_SCHEMA,
        "baseline_revision": BASELINE_REVISION,
        "run_id": run_id,
        "summary": summary,
        **sections,
        "section_fingerprints": {
            name: _section_fingerprint(value) for name, value in sections.items()
        },
    }
    payload["fixture_fingerprint"] = _section_fingerprint(payload)
    return Canonical.canonicalize(payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=int, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    settings = get_settings()
    database_url = settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(
        database_url,
        options="-c default_transaction_read_only=on",
        row_factory=dict_row,
    ) as connection:
        read_only = connection.execute("SHOW transaction_read_only").fetchone()
        if read_only["transaction_read_only"] != "on":
            raise RuntimeError("baseline exporter requires a read-only transaction")
        for run_id in sorted(set(args.run)):
            payload = export_run(connection, run_id)
            target = args.output_dir / f"run_{run_id}.json"
            target.write_text(Canonical.dumps(payload) + "\n", encoding="utf-8")
            print(f"{target}: {payload['fixture_fingerprint']}")
        connection.rollback()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
