"""Resume only the diagnosed Run 41 after the recorded verification gates pass.

Uses the supported operator-resume entry point. It does not change anchors,
snapshot ownership, formulas, other jobs, or runtime configuration.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
OUT = Path(__file__).resolve().parent


def main():
    from collect_recovery_proof import NATIVE_PUBLICATION_FIELDS, fingerprint
    from compare_setup_evidence import read_pins
    from sqlalchemy import create_engine, select, text
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import Session

    from app.models.tables import PipelineRun, PipelineStep
    from app.services.configuration_delivery import binding_reference, load_configuration_delivery
    from app.services.pipeline_service import resume_pipeline
    from app.settings import get_settings
    from scripts.forensics.review_resumed_pipeline_evidence import review_resumed_pipeline_evidence
    from scripts.forensics.run41_recovery_contract import validate_combined_rows

    clone = json.loads((OUT / "evidence/production-clone-result.json").read_text())
    assert clone["pipeline_status"] == "PARTIAL" and clone["job_status"] == "COMPLETED"
    assert clone["recovery_contract"]["contract"] == "run41-preserved-gap-recovery-v1"
    assert clone["terminal_contract_verified_read_only"]
    assert clone["snapshot_creator_and_evidence_pins_preserved"]
    assert clone["winner_predictions"] == 82 and clone["production_written"] is False
    assert all(step["status"] == "COMPLETED" for step in clone["steps"])
    assert clone["frozen_handoff_enabled"] is False
    preservation = json.loads(
        (OUT / "evidence/production-clone-preservation-proof.json").read_text()
    )
    assert preservation["pipeline_status"] == "PARTIAL"
    assert preservation["recovery_contract"] == clone["recovery_contract"]
    assert preservation["snapshot_immutable_rows_preserved"]
    assert preservation["ceri_identities_match_original_handoff"]
    assert preservation["configuration_anchor_preserved"]
    assert preservation["snapshot_creators"] == [58]
    evidence_preservation = json.loads(
        (OUT / "evidence/clone-setup-evidence-preservation.json").read_text()
    )
    assert evidence_preservation["complete_evidence_row_hashes_match"]
    assert evidence_preservation["complete_evidence_rows"] == 88
    root = ElementTree.parse(OUT / "full-pipeline-clean.xml").getroot()
    cases = list(root.iter("testcase"))
    expected_cases = ("chromium-False-88", "chromium-True-10")
    for expected in expected_cases:
        matches = [case for case in cases if expected in case.attrib.get("name", "")]
        assert len(matches) == 1, expected
        assert all(matches[0].find(kind) is None for kind in ("failure", "error", "skipped"))
    source = json.loads((OUT / "evidence/certification-worktree.json").read_text())
    assert source["all_app_config_sources_match_after_lf_normalization"]
    for name, expected in source["production_all_source_sha256"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, name
    expected_pins = json.loads((OUT / "evidence/clone-preserved-snapshot-pins.json").read_text())
    url = make_url(get_settings().database_url)
    assert url.database == "swinglens", "Recovery is scoped to the diagnosed production database"
    assert read_pins(url) == json.loads(
        (OUT / "evidence/original-setup-evidence-full-row-pins.json").read_text()
    )
    engine = create_engine(url)
    try:
        with Session(engine) as db:
            assert db.scalar(text("select current_database()")) == "swinglens"
            pipeline = db.get(PipelineRun, 41)
            assert pipeline.status == "FAILED"
            assert pipeline.current_step == "EVALUATING_SETUP_LIFECYCLES"
            reference = binding_reference(db, pipeline_run_id=41)
            assert reference["anchor_id"] == clone["frozen_anchor"]
            delivery = load_configuration_delivery(db, reference)
            assert (
                delivery.configurations["execution.settings"].values[
                    "setup_capture_handoff_enabled"
                ]
                is False
            )
            capture_step = db.scalar(
                select(PipelineStep).where(
                    PipelineStep.pipeline_run_id == 41,
                    PipelineStep.step_name == "CAPTURING_SETUP_SIGNALS",
                )
            )
            handoff = capture_step.result_json["setup_capture_handoff"]
            assert capture_step.status == "COMPLETED" and handoff["evaluation_run_id"] == 58
            assert set(handoff["snapshot_ids"]) == set(range(3498, 3586))
            actual_pins = [
                list(row)
                for row in db.execute(
                    text(
                        "select s.id,s.evaluation_run_id,s.evidence_id,e.payload_fingerprint "
                        "from setup_signal_snapshots s "
                        "join core_calculation_evidence e on e.id=s.evidence_id "
                        "where s.run_id=41 order by s.id"
                    )
                )
            ]
            assert actual_pins == expected_pins
            original_coverage = json.loads(
                (OUT / "evidence/preexisting-coverage-gaps.json").read_text()
            )
            validate_combined_rows(db, original_coverage["all_combined_row_sha256"])
            immutable_baseline = json.loads(
                (OUT / "evidence/production-snapshot-source-row-pins.json").read_text()
            )
            assert immutable_baseline["original_full_rows_match_forensic_hashes"]
            current_rows = list(
                db.scalars(
                    text(
                        "select to_jsonb(s) from setup_signal_snapshots s "
                        "where run_id=41 order by id"
                    )
                )
            )
            assert {
                str(row["id"]): fingerprint(
                    {
                        key: value
                        for key, value in row.items()
                        if key not in NATIVE_PUBLICATION_FIELDS
                    }
                )
                for row in current_rows
            } == immutable_baseline["immutable_row_sha256"]
            before = {
                "pipeline_status": pipeline.status,
                "current_step": pipeline.current_step,
                "anchor": reference,
                "capture_evaluation_run_id": 58,
                "snapshot_count": len(actual_pins),
                "source_verified": True,
                "immutable_snapshot_rows_verified": True,
                "immutable_setup_evidence_rows_verified": True,
            }
            pipeline = resume_pipeline(db, 41, resume_from_step="EVALUATING_SETUP_LIFECYCLES")
            job_id = pipeline.result_json["background_job_id"]
            review = review_resumed_pipeline_evidence(
                db,
                41,
                expected_handoff_fingerprint=clone["evidence_review"][
                    "handoff_manifest_fingerprint"
                ],
                incident_reference=f"run41-lifecycle-remediation:{source['commit']}",
            )
            db.commit()
            result = {
                "operation": "SUPPORTED_OPERATOR_RESUME",
                "pipeline_run_id": 41,
                "job_id": job_id,
                "queued_at_utc": datetime.now(UTC).isoformat(),
                "verification_commit": source["commit"],
                "before": before,
                "evidence_review": review,
                "raw_production_sql_updates": False,
            }
            (OUT / "evidence/production-recovery-enqueue.json").write_text(
                json.dumps(result, indent=2), encoding="utf-8"
            )
            print(json.dumps(result))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
