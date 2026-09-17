"""Read-only global heartbeat/lease and new-evidence checks after shutdown."""

import json
import sys
from pathlib import Path

directory = Path(__file__).resolve().parent
sys.path.insert(0, str(directory.parents[2]))

from sqlalchemy import text

from app.db import engine
from scripts.ops import lifecycle_probe as lp

before = json.loads((directory / "integrity-before.json").read_text())
runtime_after = json.loads((directory / "runtime-after.json").read_text())
settings = lp._settings()
with engine.connect() as connection:
    connection.execute(text("SET TRANSACTION READ ONLY"))
    result = {"maximumIds": {}, "freshRegistrations": {}}
    for table, ceiling in before["ceilings"].items():
        actual_table = {"jobs": "background_jobs", "pipelines": "pipeline_runs"}.get(table, table)
        maximum = connection.execute(text(f"select coalesce(max(id),0) from {actual_table}")).scalar_one()
        result["maximumIds"][table] = {"before": ceiling, "after": maximum}
        assert maximum == ceiling, (table, ceiling, maximum)
    for table in ("background_workers", "background_supervisors"):
        count = connection.execute(text(
            f"select count(*) from {table} where stopping_at is null and "
            "heartbeat_at >= now() - :seconds * interval '1 second'"
        ), {"seconds": settings.job_worker_heartbeat_timeout_seconds}).scalar_one()
        result["freshRegistrations"][table] = count
        assert count == 0, (table, count)
    result["freshJobLeases"] = connection.execute(text(
        "select count(*) from background_jobs where execution_token is not null and lease_expires_at > now()"
    )).scalar_one()
    assert result["freshJobLeases"] == 0
    result["heartbeatUnchanged"] = {}
    for role, table in (("worker", "background_workers"), ("supervisor", "background_supervisors")):
        row = connection.execute(text(
            f"select heartbeat_at,stopping_at from {table} where worker_id='local-worker-1'"
        )).mappings().one()
        unchanged = str(row["heartbeat_at"]) == runtime_after["database"][role]["heartbeat_at"]
        result["heartbeatUnchanged"][role] = unchanged
        assert unchanged and row["stopping_at"] is not None
    result["capturedAt"] = str(connection.execute(text("select now()")).scalar_one())
(directory / "post-stop-database-check.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(result)
