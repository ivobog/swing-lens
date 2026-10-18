"""Read-only Run 41 recovery and historical snapshot-preservation proof."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent / "evidence"
sys.path.insert(0, str(ROOT))
NATIVE_PUBLICATION_FIELDS = {
    "is_canonical",
    "canonical_reason",
    "canonical_decision_json",
    "canonicalized_at",
    "primary_setup_family",
    "primary_phase",
    "lifecycle_state_candidate",
    "actionability_candidate",
    "confidence_score",
    "confidence_label",
}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def main(scope, label, require_terminal, record_baseline):
    from sqlalchemy import create_engine, select, text
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import Session

    from app.database_safety import assert_disposable_database
    from app.models.ceri_tables import CeriScoreSnapshot
    from app.models.tables import PipelineRun
    from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
    from app.services.decision_handoff_contract import handoff_artifact_identity
    from app.services.setup_lifecycle.decision_evidence import _SETUP_PROJECTION_FIELDS
    from app.settings import get_settings
    from scripts.forensics.run41_recovery_contract import validate_recovery

    original = json.loads(
        (ROOT / "docs/forensics/run41-lifecycle-failure/database-before.json").read_text()
    )
    assert NATIVE_PUBLICATION_FIELDS <= _SETUP_PROJECTION_FIELDS
    assert (
        not {"evaluation_run_id", "evidence_id", "captured_at", "source_data_hash"}
        & NATIVE_PUBLICATION_FIELDS
    )
    source = make_url(get_settings().database_url)
    assert source.database == "swinglens"
    target = source
    if scope == "clone":
        metadata = json.loads((OUT / "production-clone.json").read_text())
        target = source.set(database=metadata["database"])
        assert_disposable_database(target, active_database_url=source)
    engine = create_engine(
        target,
        connect_args={"options": "-c default_transaction_read_only=on -c statement_timeout=60000"},
        isolation_level="REPEATABLE READ",
    )
    try:
        with Session(engine) as db:
            assert db.scalar(text("show transaction_read_only")) == "on"
            pipeline = db.scalar(text("select to_jsonb(p) from pipeline_runs p where id=41"))
            steps = list(
                db.scalars(
                    text(
                        "select to_jsonb(s) from pipeline_steps s "
                        "where pipeline_run_id=41 order by step_order"
                    )
                )
            )
            snapshots = list(
                db.scalars(
                    text(
                        "select to_jsonb(s) from setup_signal_snapshots s "
                        "where run_id=41 order by id"
                    )
                )
            )
            actual_hashes = {
                row["id"]: hashlib.sha256(
                    json.dumps(row, sort_keys=True, default=str).encode()
                ).hexdigest()
                for row in snapshots
            }
            expected_hashes = {
                row["id"]: row["complete_row_sha256"] for row in original["snapshots"]
            }
            immutable_hashes = {
                str(row["id"]): fingerprint(
                    {
                        key: value
                        for key, value in row.items()
                        if key not in NATIVE_PUBLICATION_FIELDS
                    }
                )
                for row in snapshots
            }
            baseline_path = OUT / "production-snapshot-source-row-pins.json"
            if record_baseline:
                assert scope == "live" and pipeline["status"] == "FAILED"
                assert actual_hashes == expected_hashes
                assert not baseline_path.exists(), "Never overwrite the pre-recovery baseline"
                baseline_path.write_text(
                    json.dumps(
                        {
                            "original_full_rows_match_forensic_hashes": True,
                            "native_publication_fields": sorted(NATIVE_PUBLICATION_FIELDS),
                            "immutable_row_sha256": immutable_hashes,
                            "original_native_publication_metadata": {
                                str(row["id"]): {
                                    key: row.get(key) for key in NATIVE_PUBLICATION_FIELDS
                                }
                                for row in snapshots
                            },
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            baseline = json.loads(baseline_path.read_text())
            metadata_changes = {
                str(row["id"]): [
                    key
                    for key in sorted(NATIVE_PUBLICATION_FIELDS)
                    if row.get(key)
                    != baseline["original_native_publication_metadata"][str(row["id"])][key]
                ]
                for row in snapshots
            }
            handoff = db.scalar(
                text(
                    "select to_jsonb(h) from transition_decision_handoff_manifests h "
                    "where pipeline_run_id=41"
                )
            )
            ceri_pins = [
                handoff_artifact_identity(row)
                for row in db.scalars(
                    select(CeriScoreSnapshot)
                    .where(CeriScoreSnapshot.run_id == 41)
                    .order_by(CeriScoreSnapshot.ticker, CeriScoreSnapshot.id)
                )
            ]
            counts = {
                name: db.scalar(text(query))
                for name, query in original["impact-count-queries"].items()
            }
            reference = db.scalar(
                text(
                    "select anchor_id from execution_configuration_bindings "
                    "where pipeline_run_id=41 order by binding_key limit 1"
                )
            )
            old_job = db.scalar(
                text("select to_jsonb(j)-'execution_token' from background_jobs j where id=1416")
            )
            original_job = next(row for row in original["jobs"] if row["id"] == 1416)
            recovery_job = dict(
                db.execute(
                    text(
                        "select id,status,parent_job_id,root_job_id,error_message,"
                        "started_at,completed_at "
                        "from background_jobs where id=:job_id"
                    ),
                    {"job_id": pipeline["result_json"]["background_job_id"]},
                )
                .mappings()
                .one()
            )
            evaluations = [
                dict(row)
                for row in db.execute(
                    text(
                        "select id,status,current_phase,captured_count,canonical_count,"
                        "changed_count,transitioned_count,alerted_count,"
                        "audit_json->'capture_snapshot_membership' "
                        "as membership from setup_lifecycle_evaluation_runs "
                        "where source_run_id=41 order by id"
                    )
                ).mappings()
            ]
            memberships = []
            for evaluation in evaluations:
                membership = evaluation.pop("membership")
                if membership is None:
                    continue
                body = {key: value for key, value in membership.items() if key != "fingerprint"}
                memberships.append(
                    {
                        "capture_attempt_id": evaluation["id"],
                        "status": evaluation["status"],
                        "snapshot_count": len(membership["snapshots"]),
                        "original_creator_ids": sorted(
                            {
                                pin["creator_evaluation_run_id"]
                                for pin in membership["snapshots"].values()
                            }
                        ),
                        "original_exact_ids": set(membership["snapshots"])
                        == {str(value) for value in range(3498, 3586)},
                        "membership_fingerprint": membership["fingerprint"],
                        "membership_fingerprint_valid": Canonical.fingerprint(body)
                        == membership["fingerprint"],
                    }
                )
            result = {
                "captured_at_utc": datetime.now(UTC).isoformat(),
                "scope": scope,
                "database": target.database,
                "read_only_enforced": True,
                "pipeline_status": pipeline["status"],
                "current_step": pipeline["current_step"],
                "pipeline_error": pipeline.get("error_message"),
                "pipeline_result": pipeline.get("result_json"),
                "recovery_job": recovery_job,
                "lifecycle_evaluations": evaluations,
                "capture_memberships": memberships,
                "original_failed_job_status": old_job["status"],
                "original_failed_job_history_preserved": old_job == original_job,
                "steps": [
                    {key: row.get(key) for key in ("step_name", "status", "error_message")}
                    for row in steps
                ],
                "impact_counts": counts,
                "snapshot_full_rows_preserved": actual_hashes == expected_hashes,
                "snapshot_immutable_rows_preserved": immutable_hashes
                == baseline["immutable_row_sha256"],
                "preservation_contract": "setup-source-and-creator-preservation-v1",
                "native_publication_fields": sorted(NATIVE_PUBLICATION_FIELDS),
                "native_publication_field_changes": metadata_changes,
                "snapshot_row_sha256": actual_hashes,
                "snapshot_creators": sorted({row["evaluation_run_id"] for row in snapshots}),
                "ceri_identities_match_original_handoff": ceri_pins
                == handoff["manifest_json"]["ceri_score_snapshots"],
                "handoff_manifest_id": handoff["id"],
                "handoff_manifest_fingerprint": handoff["manifest_fingerprint"],
                "configuration_anchor_preserved": reference
                == original["configuration-anchor"]["anchor_id"],
            }
            if require_terminal:
                coverage = json.loads((OUT / "preexisting-coverage-gaps.json").read_text())
                result["recovery_contract"] = validate_recovery(
                    db, db.get(PipelineRun, 41), coverage["all_combined_row_sha256"]
                )
            (OUT / f"{label}.json").write_text(
                json.dumps(result, indent=2, default=str), encoding="utf-8"
            )
            print(
                json.dumps(
                    {
                        key: value
                        for key, value in result.items()
                        if key
                        not in {
                            "pipeline_result",
                            "snapshot_row_sha256",
                            "steps",
                            "native_publication_field_changes",
                        }
                    },
                    default=str,
                )
            )
            assert result["snapshot_immutable_rows_preserved"]
            assert result["ceri_identities_match_original_handoff"]
            assert result["configuration_anchor_preserved"]
            if require_terminal:
                assert pipeline["status"] == "PARTIAL"
                assert recovery_job["status"] == "COMPLETED"
                assert memberships and all(
                    row["status"] == "COMPLETED"
                    and row["snapshot_count"] == 88
                    and row["original_creator_ids"] == [58]
                    and row["original_exact_ids"]
                    and row["membership_fingerprint_valid"]
                    for row in memberships
                )
                assert result["original_failed_job_history_preserved"]
                assert len(steps) == 13 and all(row["status"] == "COMPLETED" for row in steps)
                assert counts["winner_predictions"] == 82 and counts["setup_snapshots"] == 88
                assert counts["canonical_selections"] == counts["canonical_selection_events"] == 88
    finally:
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=("live", "clone"), required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--require-terminal", "--require-completed", action="store_true")
    parser.add_argument("--record-baseline", action="store_true")
    args = parser.parse_args()
    if not args.label.replace("-", "").isalnum():
        parser.error("label must contain only alphanumeric characters and hyphens")
    main(args.scope, args.label, args.require_terminal, args.record_baseline)
