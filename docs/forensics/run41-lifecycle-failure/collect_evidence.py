"""Collect bounded Run 41 evidence. Production connections enforce read-only mode."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text
from app.settings import get_settings


def save(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def capture_database(label):
    engine = create_engine(get_settings().database_url, connect_args={
        "application_name": "run41-forensics-readonly",
        "options": "-c default_transaction_read_only=on -c statement_timeout=30000",
    }, isolation_level="REPEATABLE READ")
    queries = {
        "pipeline": "select to_jsonb(p) from pipeline_runs p where id=41",
        "steps": "select to_jsonb(s) from pipeline_steps s where pipeline_run_id=41 order by id",
        "jobs": "select to_jsonb(j)-'execution_token' from background_jobs j where related_run_id=41 or payload_json->>'pipeline_run_id'='41' order by id",
        "evaluations": "select to_jsonb(e) from setup_lifecycle_evaluation_runs e where source_run_id=41 order by id",
        "dependencies": "select to_jsonb(d) from pipeline_dependencies d where pipeline_run_id=41 order by id",
        "snapshots": "select to_jsonb(s) from setup_signal_snapshots s where run_id=41 order by id",
        "configuration-bindings": "select to_jsonb(b) from execution_configuration_bindings b where pipeline_run_id=41 or job_id=1416 order by binding_key",
    }
    state = {}
    try:
        with engine.connect() as db:
            assert db.scalar(text("show transaction_read_only")) == "on"
            for name, query in queries.items():
                state[name] = list(db.scalars(text(query)))
            # Keep full-row equality proof without duplicating large frozen
            # financial payloads in this investigation's artifacts.
            snapshot_fields = (
                "id", "run_id", "ticker", "evaluation_run_id", "evidence_id",
                "engine_version", "config_hash", "source_data_hash",
                "calculation_context_id", "calculation_cutoff_at", "input_as_of_session",
                "calendar_version", "is_canonical", "calculated_at", "captured_at",
            )
            state["snapshots"] = [
                {**{key: row.get(key) for key in snapshot_fields}, "complete_row_sha256": fingerprint(row)}
                for row in state["snapshots"]
            ]
            anchor_id = state["configuration-bindings"][0]["anchor_id"]
            anchor = db.scalar(text("select to_jsonb(a) from execution_configuration_anchors a where anchor_id=:id"), {"id": anchor_id})
            settings_id = anchor["payload_json"]["configurations"]["execution.settings"]["record"]
            settings = db.scalar(text("select to_jsonb(r) from effective_configuration_records r where resolution_hash=:id"), {"id": settings_id})
            state["configuration-anchor"] = anchor
            state["execution-settings"] = settings
            scope = "select id from setup_signal_snapshots where run_id=41"
            evidence_scope = "select evidence_id from setup_signal_snapshots where run_id=41"
            evaluation_scope = "select id from setup_lifecycle_evaluation_runs where source_run_id=41"
            counts = {
                "setup_snapshots": "select count(*) from setup_signal_snapshots where run_id=41",
                "setup_core_evidence": "select count(*) from core_calculation_evidence where run_id=41 and artifact_kind='SETUP'",
                "canonical_selections": f"select count(*) from setup_signal_snapshot_current_selections where selected_snapshot_id in ({scope})",
                "canonical_selection_events": f"select count(*) from setup_signal_snapshot_selection_events where selected_snapshot_id in ({scope})",
                "signal_changes": f"select count(*) from signal_change_events where evaluation_run_id in ({evaluation_scope}) or current_snapshot_id in ({scope})",
                "lifecycle_events": f"select count(*) from setup_lifecycle_events where snapshot_id in ({scope})",
                "lifecycle_evaluation_evidence": f"select count(*) from setup_lifecycle_evaluation_evidence where setup_evidence_id in ({evidence_scope})",
                "lifecycle_transition_evidence": f"select count(*) from setup_lifecycle_transition_evidence where setup_evidence_id in ({evidence_scope})",
                "episodes_currently_using_snapshots": f"select count(*) from setup_lifecycle_episodes where current_snapshot_id in ({scope})",
                "signal_alerts": f"select count(*) from signal_alert_events where evaluation_run_id in ({evaluation_scope})",
                "winner_predictions": "select count(*) from winner_prediction_snapshots where run_id=41",
            }
            state["impact-counts"] = {name: db.scalar(text(query)) for name, query in counts.items()}
            state["impact-count-queries"] = counts
            state["database-identity"] = dict(db.execute(text("select current_database() name, current_timestamp captured_at, version() version")).mappings().one())
    finally:
        engine.dispose()
    # Timestamp-independent state comparison covers full pipeline/job JSON and snapshot bytes.
    protected = {k: v for k, v in state.items() if k != "database-identity"}
    save(f"database-{label}.json", state)
    digest = fingerprint(protected)
    save(f"preservation-{label}.json", {"sha256": digest, "scope": list(protected), "read_only_enforced": True})
    print(json.dumps({"capture": label, "sha256": digest, "impact": state["impact-counts"]}))


def log_records(path):
    with path.open(encoding="utf-8", errors="replace") as source:
        for number, line in enumerate(source, 1):
            if "2026-10-18" not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            yield {"file": path.relative_to(ROOT).as_posix(), "line": number, **row}


def collect_logs():
    worker = []
    supervisor = []
    for row in log_records(ROOT / "logs/lifecycle-worker.log"):
        if (row.get("pipeline_run_id") == 41 or row.get("job_id") in (1385, 1416)
                or ("2026-10-18T11:44:30" <= row.get("timestamp", "") <= "2026-10-18T11:44:42")):
            worker.append(row)
        elif row.get("event") == "job.worker.startup_configuration" and row.get("process_id") == 69808:
            worker.append(row)
    for row in log_records(ROOT / "logs/lifecycle-supervisor.err.log"):
        if row.get("job_id") == 1416:
            supervisor.append(row)
    save("application-events.json", worker)
    save("supervisor-decisions.json", supervisor)

    path = ROOT / "logs/db-monitor/worker/sql-2026-10-18-p69808.6.jsonl"
    stats = Counter()
    callers = Counter()
    sql_ms = 0.0
    important = []
    transactions = []
    summary = []
    heartbeats = []
    rejected = []
    insert_tables = Counter()
    for row in log_records(path):
        stamp = row.get("timestamp", "")
        sql = row.get("normalized_sql", "")
        fn = row.get("python_caller", {}).get("function", "")
        in_evaluation = "2026-10-18T11:41:25.282" <= stamp <= "2026-10-18T11:44:30.524"
        if row.get("job_id") != 1416:
            continue
        if row.get("record_type") == "job_summary":
            summary.append(row)
        if row.get("record_type") == "long_transaction" and stamp >= "2026-10-18T11:31:55":
            transactions.append(row)
        if row.get("record_type") != "sql":
            continue
        if sql.startswith("UPDATE background_jobs SET heartbeat_at") and in_evaluation:
            heartbeats.append({key: row.get(key) for key in ("file", "line", "timestamp", "thread", "success", "duration_ms")})
        if in_evaluation:
            stats[row.get("operation")] += 1
            callers[fn] += 1
            sql_ms += row.get("duration_ms", 0)
            if not row.get("success"):
                rejected.append(row)
            table = re.match(r"INSERT INTO (\w+)", sql)
            if table:
                insert_tables[table[1]] += 1
        if "2026-10-18T11:31:55" <= stamp <= "2026-10-18T11:44:32":
            if sql.startswith(("INSERT INTO setup_lifecycle_evaluation_runs", "UPDATE setup_lifecycle_evaluation_runs", "INSERT INTO setup_signal_snapshots", "ROLLBACK", "SAVEPOINT", "RELEASE")) or fn in (
                "SetupLifecycleRepository.upsert_snapshots", "SetupLifecycleEvaluationService._completed_publication", "SetupLifecycleRepository.get_snapshots_by_ids"
            ):
                important.append(row)
    save("sql-publication-sequence.json", important)
    save("sql-statistics.json", {
        "evaluation_stage_operations": dict(stats), "evaluation_stage_callers": dict(callers),
        "evaluation_stage_sql_ms": round(sql_ms, 3), "evaluation_stage_insert_statements": dict(insert_tables),
        "evaluation_stage_failed_sql": rejected,
        "evaluation_stage_heartbeat_updates": heartbeats,
        "long_transactions": transactions, "continuation_job_summary": summary,
        "scope": {"job_id": 1416, "evaluation_start_utc": "2026-10-18T11:41:25.282", "evaluation_end_utc": "2026-10-18T11:44:30.524"},
        "note": "SQL timings are cursor timers, not CPU profiling. No SQL rejection is expected for a Python ValueError.",
    })

    postgres = []
    for path in sorted(Path("C:/Program Files/PostgreSQL/18/data/log").glob("postgresql-2026-10-18_*.log")):
        with path.open(encoding="utf-8", errors="replace") as stream:
            for line_no, line in enumerate(stream, 1):
                if re.match(r"2026-10-18 13:(?:3[1-9]|4[0-4]):", line) and re.search(r"\b(?:ERROR|FATAL|PANIC):", line):
                    postgres.append({"file": str(path), "line": line_no, "text": line.rstrip()})
    save("postgresql-errors-in-stage-window.json", postgres)
    print(json.dumps({"application_events": len(worker), "supervisor_decisions": len(supervisor), "evaluation_sql_operations": sum(stats.values()), "sql_failed": len(rejected), "server_errors_in_wider_window": len(postgres)}))


def pin_sources():
    names = [
        "app/services/setup_lifecycle/evaluation_service.py", "app/services/setup_lifecycle/snapshot_builder.py",
        "app/services/setup_lifecycle/repository.py", "app/services/setup_lifecycle/decision_evidence.py",
        "app/services/pipeline_executor.py", "app/services/configuration_delivery.py",
        "app/services/core_mutation_authority.py", "app/services/background_job_service.py",
        "app/services/background_worker.py", "app/settings.py",
        "tests/setup_lifecycle/test_evaluation_service.py", "tests/e2e/single_run_certification/test_single_run_certification.py",
    ]
    save("reviewed-source-sha256.json", {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names})
    difference = subprocess.run(["git", "diff", "--", *names], cwd=ROOT, capture_output=True, text=True, check=True)
    (OUT / "reviewed-working-tree.diff").write_text(difference.stdout, encoding="utf-8")


if __name__ == "__main__":
    label = sys.argv[1] if len(sys.argv) > 1 else "before"
    capture_database(label)
    if label == "before":
        collect_logs()
        pin_sources()
