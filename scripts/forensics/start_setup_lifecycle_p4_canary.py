"""Start one production canary through the supported upload and pipeline routes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import create_app
from app.models.tables import BackgroundJob, PipelineRun
from app.settings import get_settings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve(strict=True)

    with SessionLocal() as db:
        active = list(
            db.scalars(
                select(BackgroundJob).where(
                    BackgroundJob.job_type == "FULL_PIPELINE",
                    BackgroundJob.status.in_(("QUEUED", "RUNNING", "RECOVERING", "STALLED")),
                )
            )
        )
        if active:
            raise RuntimeError(
                "ACTIVE_FULL_PIPELINE_EXISTS:" + ",".join(str(job.id) for job in active)
            )

    client = TestClient(create_app(get_settings()))
    with source.open("rb") as stream:
        uploaded = client.post(
            "/uploads",
            files={"file": (source.name, stream, "text/csv")},
            follow_redirects=False,
        )
    if uploaded.status_code != 303:
        raise RuntimeError(f"UPLOAD_REJECTED:{uploaded.status_code}")
    run_id = int(uploaded.headers["location"].split("?", 1)[0].rsplit("/", 1)[1])

    started = client.post(
        f"/runs/{run_id}/pipeline",
        data={"market_data_policy": "ALLOW_CACHE_FALLBACK"},
        follow_redirects=False,
    )
    if started.status_code != 303:
        raise RuntimeError(f"PIPELINE_REJECTED:{started.status_code}")

    with SessionLocal() as db:
        pipeline = db.scalar(select(PipelineRun).where(PipelineRun.upload_run_id == run_id))
        if pipeline is None:
            raise RuntimeError("PIPELINE_NOT_CREATED")
        job_id = int((pipeline.result_json or {})["background_job_id"])
        report = {
            "upload_run_id": run_id,
            "pipeline_id": pipeline.id,
            "background_job_id": job_id,
            "market_data_policy": "ALLOW_CACHE_FALLBACK",
            "upload_status_code": uploaded.status_code,
            "pipeline_status_code": started.status_code,
            "pipeline_location": started.headers["location"],
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
