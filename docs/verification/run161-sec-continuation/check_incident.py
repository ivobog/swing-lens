"""Read-only execution, configuration, SEC and queue recovery checks."""

import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import engine
from app.models.tables import BackgroundJob, PipelineRun, PipelineStep, UploadRun
from app.services.configuration_delivery import (
    ANCHOR_KEY,
    binding_reference,
    configuration_delivery_scope,
    load_configuration_delivery,
)
from app.services.ceri.sec.pipeline_preflight import validate_sec_pipeline_preflight
from app.services.ib_gateway_health_service import check_status
from app.services.pipeline_executor import _tickers_for_run
from app.services.pipeline_service import get_pipeline_status


def main():
    with Session(engine) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        pipeline = db.get(PipelineRun, 151)
        upload = db.get(UploadRun, 161)
        reference = binding_reference(db, pipeline_run_id=151)
        assert pipeline.upload_run_id == upload.id == 161
        assert reference == {
            "anchor_id": "c3a654bf956deacdda507957a963743c15162eb6b6955004a377ba78c393e1f2",
            "fingerprint": "7bc8ee0c7b8ac8c44a770023afc0cd41155d2ca7f5b4afd555044319be30f77d",
        }
        delivery = load_configuration_delivery(db, reference)
        with configuration_delivery_scope(delivery):
            preflight = validate_sec_pipeline_preflight(db, tickers=_tickers_for_run(db, 161))
        assert preflight["readiness"]["ready_tickers"] == 86
        jobs = list(
            db.scalars(
                select(BackgroundJob)
                .where(BackgroundJob.related_run_id == 161)
                .order_by(BackgroundJob.id)
            )
        )

        def job_view(job):
            return {
                "id": job.id,
                "type": job.job_type,
                "status": job.status,
                "request_key": job.request_key,
                "workflow_key": job.workflow_key,
                "parent": job.parent_job_id,
                "root": job.root_job_id,
                "retry_count": job.retry_count,
                "error": job.error_message,
                "lease_held": job.execution_token is not None,
                "anchor": (job.payload_json or {}).get(ANCHOR_KEY),
                "context": (job.payload_json or {}).get("market_calculation_context_id"),
            }

        steps = list(
            db.scalars(
                select(PipelineStep)
                .where(PipelineStep.pipeline_run_id == 151)
                .order_by(PipelineStep.step_order)
            )
        )
        view = get_pipeline_status(db, pipeline.id)
        data = {
            "captured_at": str(db.execute(text("select now()")).scalar_one()),
            "upload": {"id": upload.id, "status": upload.status, "rows": upload.row_count},
            "pipeline": {
                "id": pipeline.id,
                "status": pipeline.status,
                "current_step": pipeline.current_step,
                "message": pipeline.message,
                "error": pipeline.error_message,
                "result": pipeline.result_json,
            },
            "application_status_using_fixed_code": {
                "status": view.status,
                "message": view.message,
                "error": view.error_message,
            },
            "configuration": reference,
            "readiness": preflight,
            "jobs": [job_view(job) for job in jobs],
            "steps": [
                {
                    "step": s.step_name,
                    "status": s.status,
                    "message": s.message,
                    "error": s.error_message,
                }
                for s in steps
            ],
            "all_running": [
                job_view(job)
                for job in db.scalars(
                    select(BackgroundJob).where(BackgroundJob.status == "RUNNING")
                )
            ],
            "market_context": [
                dict(row)
                for row in db.execute(
                    text(
                        "select to_jsonb(t) as context from market_calculation_contexts t where id=13"
                    )
                ).mappings()
            ],
            "migration": db.execute(text("select version_num from alembic_version")).scalar_one(),
            "worker_quiescence": [
                dict(row)
                for row in db.execute(
                    text(
                        "select process_id,quiesce_requested_at,quiesced_at from background_workers "
                        "where process_id=4000"
                    )
                ).mappings()
            ],
            "jobs_after_baseline": db.execute(
                text("select count(*) from background_jobs where id>:ceiling"),
                {
                    "ceiling": json.loads(
                        Path(__file__).with_name("integrity-before.json").read_text()
                    )["ceilings"]["jobs"]
                },
            ).scalar_one(),
        }
    health = check_status()
    data["ib_health"] = asdict(health)
    output = Path(__file__).with_name(sys.argv[1] if len(sys.argv) > 1 else "pre-recovery.json")
    output.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    print(
        {
            "pipeline": data["pipeline"]["status"],
            "ready": 86,
            "jobs": [(j["id"], j["type"], j["status"]) for j in data["jobs"]],
            "ib_api_ready": health.api_ready,
            "output": str(output),
        }
    )


if __name__ == "__main__":
    main()
