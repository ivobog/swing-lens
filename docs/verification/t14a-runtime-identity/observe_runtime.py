"""Read-only identity observations before recovery and after canonical shutdown."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import psutil
from sqlalchemy import text

from app.db import engine
from app.services.lifecycle_control import supervisor_state_path
from app.services.lifecycle_safety import read_runtime_state
from app.services.runtime_identity_recovery import verified_recovery_state
from scripts.ops import lifecycle_probe as lp

directory = Path(__file__).resolve().parent
mode = sys.argv[1]
before = json.loads((directory / "live-proof.json").read_text())
runtime_id = before["proposed_state"]["runtimeInstanceId"]
logs = []
for line in (ROOT / "logs/lifecycle/lifecycle.jsonl").read_text(encoding="utf-8").splitlines():
    try:
        row = json.loads(line)
    except ValueError:
        continue
    if row.get("runtime_instance_id") == runtime_id:
        if row.get("event") == "runtime.process_boot" or "shutdown" in row.get("event", ""):
            logs.append({key: row.get(key) for key in (
                "event", "timestamp", "component", "pid", "parent_pid", "git_sha",
                "runtime_instance_id", "config_fingerprint", "request_operation_id",
                "result", "shutdown_method",
            )})
result = {"capturedAt": datetime.now(UTC).isoformat(), "mode": mode, "logs": logs}
with engine.connect() as connection:
    connection.execute(text("SET TRANSACTION READ ONLY"))
    result["database"] = {
        "runningPipelines": connection.execute(text("select count(*) from pipeline_runs where status='RUNNING'")).scalar_one(),
        "activeJobs": connection.execute(text("select count(*) from background_jobs where status in ('RUNNING','RECOVERING')")).scalar_one(),
        "jobsAfter43269": connection.execute(text("select count(*) from background_jobs where id>43269")).scalar_one(),
        "worker": dict(connection.execute(text("select process_id,heartbeat_at,stopping_at,quiesce_requested_at,quiesced_at,instance_id,generation from background_workers where worker_id='local-worker-1'")).mappings().one()),
        "supervisor": dict(connection.execute(text("select process_id,heartbeat_at,stopping_at,instance_id,generation from background_supervisors where worker_id='local-worker-1'")).mappings().one()),
    }
assert result["database"]["runningPipelines"] == result["database"]["activeJobs"] == result["database"]["jobsAfter43269"] == 0
if mode == "before":
    evidence = lp._runtime_recovery_evidence()
    settings = lp._settings()
    state = verified_recovery_state(
        evidence, root=ROOT, port=settings.app_port, host=settings.app_host,
        worker_id=settings.job_worker_id,
        executables=[sys.executable, sys._base_executable, ROOT / '.venv/Scripts/python.exe'],
        now=datetime.now(UTC), max_age=min(60, settings.job_worker_heartbeat_timeout_seconds),
    )
    result.update(evidence=evidence, state=state, metadataMissing=not lp.RUNTIME_STATE.exists())
    assert state["runtimeInstanceId"] == runtime_id
    result["shutdownOwnership"] = lp._runtime_state_report(state["web"]["pid"], for_shutdown=True)
elif mode == "after":
    old_processes = before["evidence"]["processes"]
    old_pids = [int(pid) for pid, row in old_processes.items()
                if any(module in row.get("commandLine", []) for module in ('app.serve','app.worker','app.worker_supervisor'))]
    result["oldProcessesAlive"] = {pid: psutil.pid_exists(pid) for pid in old_pids}
    result["roles"] = lp._role_processes()
    result["listeners"] = lp._listeners_report()
    result["metadataMissing"] = not lp.RUNTIME_STATE.exists()
    result["registrations"] = lp._registrations_report()
    result["supervisorState"] = read_runtime_state(supervisor_state_path(ROOT))
    result["jobs"] = lp._jobs_report()
    assert not any(result["oldProcessesAlive"].values())
    assert not result["roles"] and result["metadataMissing"]
    assert not result["listeners"].get("error")
    assert not any(row["port"] in (8000, 9101, 9102) for row in result["listeners"]["listeners"])
    assert result["jobs"]["reachable"] and result["jobs"]["activeCount"] == 0
    assert all(result["database"][role]["stopping_at"] is not None for role in ('worker','supervisor'))
else:
    raise ValueError("before or after required")
output = directory / (sys.argv[2] if len(sys.argv) > 2 else f"runtime-{mode}.json")
output.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
print({"mode": mode, "verified": True, "output": str(output)})
