"""Read-only verification of Run 40 and the original forensic evidence."""

import hashlib
import json
from pathlib import Path

from sqlalchemy import create_engine, text

from app.settings import get_settings

folder = Path(__file__).parent
root = folder.parents[2]
original = root / "docs/forensics/run40-lifecycle-failure"
baseline = json.loads((folder / "baseline-identity.json").read_text())
report = {"forensic_files": {}, "domain": {}, "control": {}}
for name, expected in json.loads((original / "evidence-sha256.json").read_text()).items():
    report["forensic_files"][name] = hashlib.sha256((original / name).read_bytes()).hexdigest() == expected

engine = create_engine(get_settings().database_url)
predicates = {
    "snapshots": ("setup_signal_snapshots", "run_id=40"),
    "changes": ("signal_change_events", "current_snapshot_id IN (SELECT id FROM setup_signal_snapshots WHERE run_id=40)"),
    "evaluations": ("setup_lifecycle_evaluation_runs", "source_run_id=40"),
    "lifecycle_events": ("setup_lifecycle_events", "snapshot_id IN (SELECT id FROM setup_signal_snapshots WHERE run_id=40)"),
    "selections": ("setup_signal_snapshot_current_selections", "selected_snapshot_id IN (SELECT id FROM setup_signal_snapshots WHERE run_id=40)"),
}
with engine.connect() as db:
    db.execute(text("SET TRANSACTION READ ONLY"))
    for label, (table, predicate) in predicates.items():
        rows = list(db.scalars(text(f"SELECT to_jsonb(t) FROM {table} t WHERE {predicate} ORDER BY id")))
        expected = baseline["run40_domain_sha256"][label]
        candidates = {}
        # Both are lossless serializations; retain the matching baseline format.
        for sorted_keys in (True, False):
            for compact in (True, False):
                payload = json.dumps(rows, sort_keys=sorted_keys, default=str, **({"separators": (",", ":")} if compact else {}))
                digest = hashlib.sha256(payload.encode()).hexdigest()
                candidates[f"sorted={sorted_keys},compact={compact}"] = digest
        report["domain"][label] = {
            "count": len(rows), "expected_count": expected["count"],
            "sha256_matches": expected["sha256"] in candidates.values(),
            "expected_sha256": expected["sha256"], "serializations": candidates,
        }
    for label, query in (
        ("pipeline", "SELECT id,status,current_step,execution_authority_state FROM pipeline_runs WHERE id=40"),
        ("job", "SELECT id,status,retry_count,execution_token FROM background_jobs WHERE id=1384"),
        ("evaluation", "SELECT id,status,changed_count,alerted_count FROM setup_lifecycle_evaluation_runs WHERE id=57"),
    ):
        report["control"][label] = [dict(row) for row in db.execute(text(query)).mappings()]
    forensic = json.loads((original / "database-summary.json").read_text())
    queries = {
        "pipeline": """SELECT id,status,current_step,started_at,completed_at,error_message,message,
            result_json->'performance' AS performance,
            result_json->>'ceri_completion_state' AS ceri_completion_state,
            result_json->>'decision_handoff_manifest_id' AS handoff_manifest_id
            FROM pipeline_runs WHERE id=40""",
        "steps": """SELECT step_name,step_order,status,started_at,completed_at,message,error_message
            FROM pipeline_steps WHERE pipeline_run_id=40 ORDER BY step_order""",
        "jobs": """SELECT id,job_type,status,retry_count,max_retries,started_at,completed_at,run_after,
            error_message,progress_stage,progress_current_item,progress_last_completed_item,
            progress_sequence,last_progress_at,worker_id,
            operational_metadata_json->'last_attempt' AS last_attempt,
            operational_metadata_json->'failure_classification' AS failure_classification,
            operational_metadata_json->'setup_lifecycle_evaluation_progress' AS setup_progress
            FROM background_jobs WHERE related_run_id=40 ORDER BY id""",
        "dependencies": """SELECT id,dependency_type,state,root_job_id,child_job_id,
            continuation_job_id,error_message FROM pipeline_dependencies
            WHERE pipeline_run_id=40 ORDER BY id""",
    }
    report["forensic_control_comparison"] = {}
    for label, query in queries.items():
        current = [dict(row) for row in db.execute(text(query)).mappings()]
        normalized = json.loads(json.dumps(current, default=str))
        report["forensic_control_comparison"][label] = {
            "rows": len(current), "equal": normalized == forensic[label],
        }
engine.dispose()
report["all_forensic_files_unchanged"] = all(report["forensic_files"].values())
report["all_domain_fingerprints_match"] = all(row["sha256_matches"] and row["count"] == row["expected_count"] for row in report["domain"].values())
report["all_forensic_control_columns_unchanged"] = all(row["equal"] for row in report["forensic_control_comparison"].values())
(folder / "evidence/preservation-check.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
print(json.dumps({key: report[key] for key in ("all_forensic_files_unchanged", "all_domain_fingerprints_match", "control")}, indent=2))
