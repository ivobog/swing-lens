"""Rebuild bounded Run 40 SQL evidence from existing logs; no database writes."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent


def records(path):
    with path.open(encoding="utf-8", errors="replace") as source:
        for number, line in enumerate(source, 1):
            if "2026-10-17T11:" not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            yield {"file": str(path.relative_to(ROOT)), "line": number, **row}


def compact(row):
    keys = ("file", "line", "timestamp", "record_type", "duration_ms", "success",
            "operation", "normalized_sql", "python_caller", "thread", "job_id",
            "run_id", "error_type", "parameter_digest", "transaction_start",
            "transaction_end", "pid", "state", "transaction_duration_ms",
            "wait_event_type", "wait_event", "application_name", "blocking_pids")
    return {key: row[key] for key in keys if key in row}


def save(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2), encoding="utf-8")


def main():
    callers = Counter()
    operations = Counter()
    sql_ms = 0.0
    query_count = 0
    change_insert_count = 0
    business_for_update_count = 0
    autonomous = []
    failure_window = []
    worker_paths = sorted((ROOT / "logs/db-monitor/worker").glob("*2026-10-17-p15132.[67].jsonl"))
    for path in worker_paths:
        for row in records(path):
            stamp = row.get("timestamp", "")
            statement = row.get("normalized_sql", "")
            if row.get("record_type") == "sql" and "2026-10-17T11:18:26.200" <= stamp <= "2026-10-17T11:39:24.703":
                if row.get("job_id") == 1384:
                    query_count += 1
                    sql_ms += row.get("duration_ms", 0)
                    callers[row.get("python_caller", {}).get("function", "unknown")] += 1
                    operations[row.get("operation", "unknown")] += 1
                    change_insert_count += statement.startswith("INSERT INTO signal_change_events")
                    business_for_update_count += "background_jobs" in statement and "FOR UPDATE" in statement
                if row.get("thread", {}).get("name") == "local-worker-1-job-control-heartbeat" and statement.startswith("UPDATE background_jobs") and row.get("success"):
                    autonomous.append(compact(row))
            if "2026-10-17T11:39:24.39" <= stamp <= "2026-10-17T11:39:44" and row.get("record_type") in {"sql", "long_transaction"}:
                failure_window.append(compact(row))

    supervisor = []
    path = ROOT / "logs/db-monitor/supervisor/sql-2026-10-17-p14432.jsonl"
    for row in records(path):
        if "2026-10-17T11:39:18" <= row.get("timestamp", "") <= "2026-10-17T11:39:41":
            if "background_jobs" in row.get("normalized_sql", "") or row.get("record_type") == "long_transaction":
                supervisor.append(compact(row))

    graph = []
    path = ROOT / "logs/db-monitor/web/sql-2026-10-17-p42152.1.jsonl"
    for row in records(path):
        if row.get("record_type") == "database_health" and "2026-10-17T11:39:18" <= row.get("timestamp", "") <= "2026-10-17T11:39:42":
            graph.append(compact(row))

    save("detector-sql-statistics.json", {
        "start_utc": "2026-10-17T11:18:26.200+00:00",
        "end_utc": "2026-10-17T11:39:24.703+00:00",
        "wall_seconds": 1258.503,
        "job_sql_records": query_count, "sql_duration_sum_ms": round(sql_ms, 3),
        "caller_counts": dict(callers), "operation_counts": dict(operations),
        "change_event_insert_statements": change_insert_count,
        "business_background_job_for_update_count": business_for_update_count,
        "successful_autonomous_heartbeat_updates": len(autonomous),
        "autonomous_first": autonomous[:1], "autonomous_last": autonomous[-3:],
        "duration_note": "SQL timers exclude Python/ORM work and do not constitute a CPU profile.",
    })
    save("sql-failure-window.json", failure_window)
    save("concurrent-job-locks.json", supervisor)
    save("blocking-graph.json", graph)

    pg_log = Path("C:/Program Files/PostgreSQL/18/data/log/postgresql-2026-10-17_131129.log")
    pg_lines = pg_log.read_text(encoding="utf-8", errors="replace").splitlines()
    save("postgresql-errors.json", [
        {"file": str(pg_log), "line": i + 1, "text": line}
        for i, line in enumerate(pg_lines) if 179 <= i <= 201
    ])
    code = ("app/services/background_job_service.py", "app/services/domain_write_fence.py",
            "app/services/background_worker.py", "app/worker_supervisor.py",
            "app/services/pipeline_executor.py", "app/services/pipeline_execution_authority.py",
            "app/services/setup_lifecycle/evaluation_service.py",
            "app/services/setup_lifecycle/change_detector.py",
            "app/services/setup_lifecycle/change_authority.py",
            "app/services/setup_lifecycle/repository.py", "app/observability/logging.py")
    save("reviewed-code-sha256.json", {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in code
    })
    print(json.dumps({"detector_queries": query_count, "change_inserts": change_insert_count,
                      "sql_ms": round(sql_ms, 3), "healthy_autonomous_updates": len(autonomous),
                      "business_job_for_update": business_for_update_count}, indent=2))


if __name__ == "__main__":
    main()
