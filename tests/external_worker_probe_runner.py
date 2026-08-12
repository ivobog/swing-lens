from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app.models.tables import BackgroundJob  # noqa: E402
from app.services.background_worker import run_worker  # noqa: E402


def execute_probe(db, job):
    while True:
        payload = db.scalar(
            select(BackgroundJob.payload_json).where(BackgroundJob.id == job.id)
        )
        if (payload or {}).get("release"):
            break
        time.sleep(0.1)
    return {"probe": "completed"}


if __name__ == "__main__":
    run_worker(
        worker_id="active-probe-worker",
        queues=("background",),
        handlers={"WORKER_RUNTIME_PROBE": execute_probe},
        stop_after_one=True,
    )
