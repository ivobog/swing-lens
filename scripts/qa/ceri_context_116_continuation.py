"""Finish only the genuine disposable pipeline-66 continuation after capture."""

from __future__ import annotations

import json
import os
import socket
import traceback
from threading import Event, Thread
from uuid import uuid4

from scripts.qa.ceri_validation_environment import REPORT, SOURCE_CLONE, credentials


def main():
    capture = json.loads((REPORT / "context-116-certification.json").read_text())
    assert capture["decision"] == "PASS"
    source = credentials()
    assert SOURCE_CLONE != source.database and SOURCE_CLONE.startswith("swinglens_qa_ceri_")
    target = source.set(database=SOURCE_CLONE).render_as_string(hide_password=False)
    os.environ.update(DATABASE_URL=target, SWINGLENS_DATABASE_SAFETY_CONTEXT="DISPOSABLE_TEST")
    from sqlalchemy import create_engine, func, select, text
    from sqlalchemy.orm import Session

    from app.database_safety import assert_disposable_database
    from app.models.tables import BackgroundJob, PipelineDependency, PipelineRun, PipelineStep
    from app.services.background_job_service import claim_next_job, mark_job_failed_or_retry
    from app.services.background_worker import (
        _active_pipeline_control_heartbeat_loop,
        default_job_handlers,
        execute_job,
        job_result_diagnostics,
        job_result_reason_code,
        job_result_status,
        mark_job_blocked,
        mark_job_completed,
        mark_job_partial,
        persist_detached_job_control,
        run_post_terminalization_actions,
    )
    from app.services.ceri.score_population import certified_scores
    from app.services.pipeline_dependency_service import mark_dependency_job_running
    from app.services.worker_registry import register_worker

    assert_disposable_database(target, active_database_url=source)
    engine = create_engine(target, hide_parameters=True)
    result = {"database": SOURCE_CLONE, "pipeline": 66, "jobs": []}
    try:
        with Session(engine) as db:
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(BackgroundJob)
                    .where(BackgroundJob.status == "RUNNING", BackgroundJob.id > 2546)
                )
                == 0
            )
            # A historical partial restore has no trustworthy sequence positions.
            # No writer is running; repair only counters, never retained rows.
            columns = db.execute(
                text("""SELECT table_name,column_name
                FROM information_schema.columns WHERE table_schema='public'
                AND (column_default LIKE 'nextval(%' OR is_identity='YES')""")
            ).all()
            quote = engine.dialect.identifier_preparer.quote
            for table, column in columns:
                sequence = db.scalar(
                    text("SELECT pg_get_serial_sequence(:table,:column)"),
                    {"table": table, "column": column},
                )
                if sequence:
                    maximum = db.scalar(
                        text("SELECT coalesce(max(" + quote(column) + "),1) FROM " + quote(table))
                    )
                    sequence_sql = ".".join(quote(part) for part in sequence.split("."))
                    previous = db.scalar(text("SELECT last_value FROM " + sequence_sql))
                    maximum = max(maximum, previous)
                    db.execute(
                        text("SELECT setval(CAST(:sequence AS regclass),:maximum,true)"),
                        {"sequence": sequence, "maximum": maximum},
                    )
            worker = register_worker(
                db,
                worker_id="ceri-context-116-continuation:" + uuid4().hex[:12],
                queues=("background", "interactive"),
                heartbeat_timeout_seconds=7200,
                hostname=socket.gethostname(),
                process_id=os.getpid(),
            )
            worker_id, instance = worker.worker_id, worker.instance_id
            dependency = db.get(PipelineDependency, capture["dependency_id"])
            assert dependency.continuation_job_id is not None
            continuation_id = dependency.continuation_job_id
            db.commit()

        handlers = default_job_handlers()

        def execute_existing(job_id):
            with Session(engine) as db:
                job = db.get(BackgroundJob, job_id)
                assert job is not None and job.status in {"PENDING", "QUEUED", "RETRY"}
                assert (job.payload_json or {}).get("pipeline_run_id") == 66
                job.priority = 0
                db.commit()
                claimed = claim_next_job(
                    db,
                    worker_id=worker_id,
                    worker_instance_id=instance,
                    included_job_types=(job.job_type,),
                    lease_seconds=3600,
                )
                assert claimed is not None and claimed.id == job_id
                db.commit()
                mark_dependency_job_running(db, claimed.id)
                db.commit()
                stop = Event()
                heartbeat = Thread(
                    target=_active_pipeline_control_heartbeat_loop,
                    kwargs={
                        "session_factory": lambda: Session(engine),
                        "job_id": claimed.id,
                        "execution_token": claimed.execution_token,
                        "worker_id": worker_id,
                        "worker_instance_id": instance,
                        "lease_seconds": 3600,
                        "interval_seconds": 20,
                        "stop_event": stop,
                    },
                    daemon=True,
                )
                heartbeat.start()

                def progress(**values):
                    return persist_detached_job_control(
                        session_factory=lambda: Session(engine),
                        job_id=claimed.id,
                        execution_token=claimed.execution_token,
                        worker_id=worker_id,
                        worker_instance_id=instance,
                        lease_seconds=3600,
                        progress=values,
                    )

                claimed._control_plane_progress = progress
                try:
                    values = execute_job(
                        db, claimed, handlers, execution_token=claimed.execution_token
                    )
                    status = job_result_status(claimed)
                    if status == "BLOCKED":
                        mark_job_blocked(
                            db,
                            claimed,
                            RuntimeError(job_result_reason_code(claimed)),
                            reason_code=job_result_reason_code(claimed),
                            diagnostics=job_result_diagnostics(claimed),
                            execution_token=claimed.execution_token,
                        )
                    elif status == "PARTIAL":
                        mark_job_partial(
                            db, claimed, values, execution_token=claimed.execution_token
                        )
                    else:
                        mark_job_completed(
                            db, claimed, values, execution_token=claimed.execution_token
                        )
                    run_post_terminalization_actions(db, claimed)
                    db.commit()
                    result["jobs"].append(
                        {
                            "id": claimed.id,
                            "type": claimed.job_type,
                            "status": claimed.status,
                            "result": values,
                        }
                    )
                    assert claimed.status in {"COMPLETED", "PARTIAL"}
                    print("Completed existing QA job", claimed.id, claimed.job_type, flush=True)
                except Exception as error:
                    db.rollback()
                    live = db.get(BackgroundJob, job_id)
                    if live.status == "RUNNING":
                        mark_job_failed_or_retry(
                            db, live, error, execution_token=live.execution_token
                        )
                        db.commit()
                    raise
                finally:
                    stop.set()
                    heartbeat.join(timeout=5)

        # Capture retries may coalesce with completed tails, or admit a new tail.
        # Finish admitted tails before the queued parent continuation.
        while True:
            with Session(engine) as db:
                tail_id = db.scalar(
                    select(BackgroundJob.id)
                    .where(
                        BackgroundJob.workflow_key == capture["workflow_key"],
                        BackgroundJob.job_type.in_(("CERI_CHANGE_DETECTION", "CERI_ALERT_REBUILD")),
                        BackgroundJob.status.in_(("PENDING", "QUEUED", "RETRY")),
                    )
                    .order_by(BackgroundJob.id)
                    .limit(1)
                )
            if tail_id is None:
                break
            execute_existing(tail_id)
        execute_existing(continuation_id)
        for _ in range(200):
            with Session(engine) as db:
                owner = db.get(PipelineRun, 66)
                if owner.status in {"COMPLETED", "PARTIAL", "FAILED", "BLOCKED", "CANCELLED"}:
                    break
                admitted = db.scalar(
                    select(BackgroundJob.id)
                    .where(
                        BackgroundJob.id > continuation_id,
                        BackgroundJob.payload_json["pipeline_run_id"].astext == "66",
                        BackgroundJob.status.in_(("PENDING", "QUEUED", "RETRY")),
                    )
                    .order_by(BackgroundJob.id)
                    .limit(1)
                )
                assert admitted is not None, "GENUINE_ADMITTED_CONTINUATION_UNAVAILABLE"
            execute_existing(admitted)
        with Session(engine) as db:
            owner = db.get(PipelineRun, 66)
            scores = certified_scores(
                db, 66, 116, expected_ids=capture["steps"]["handoff"]["score_ids"]
            )
            steps = list(db.scalars(select(PipelineStep).where(PipelineStep.pipeline_run_id == 66)))
            result.update(
                parent_status=owner.status,
                outcome=owner.result_json.get("pipeline_outcome_summary"),
                parent_result=owner.result_json,
                steps=[{"key": s.step_name, "status": s.status} for s in steps],
                certified_count=len(scores),
            )
            assert owner.status in {"PARTIAL", "COMPLETED"}
            assert steps and all(s.status in {"COMPLETED", "PARTIAL", "SKIPPED"} for s in steps)
            if owner.status == "PARTIAL":
                assert (
                    owner.result_json["pipeline_outcome_summary"]["integrity"]["status"]
                    == "NO_INTEGRITY_HAZARD"
                )
        result["decision"] = "PASS"
    except Exception as error:
        unavailable = (
            isinstance(error, MemoryError)
            or type(error).__name__
            in {"PipelineBlockedError", "WorkerMemoryCritical", "JobLeaseLost"}
            or "GENUINE_" in str(error)
        )
        result.update(
            decision="BLOCKED" if unavailable else "FAIL",
            error={"type": type(error).__name__, "message": str(error)},
        )
        traceback.print_exc()
        return_code = 1
    else:
        return_code = 0
    finally:
        (REPORT / "pipeline-66-continuation.json").write_text(
            json.dumps(result, indent=2, default=str), encoding="utf-8"
        )
        engine.dispose()
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
