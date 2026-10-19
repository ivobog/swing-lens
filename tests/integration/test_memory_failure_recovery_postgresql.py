from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from test_queue_fairness_postgresql import _seed_pipeline
from test_worker_progress_reliability_postgresql import _migrate

from app.models.tables import BackgroundJob, BackgroundWorker, PipelineRun, PipelineStep
from app.services.background_job_service import (
    JobLeaseLost,
    claim_next_job,
    enqueue_job,
    reconcile_jobs_for_worker_loss,
    record_job_progress,
    requeue_stalled_jobs,
)
from app.services.runtime_mutation_authority import RecoveryAuthority


def test_native_memory_fences_stop_replay_and_reject_late_owner(disposable_postgres_database):
    _migrate(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    authority = RecoveryAuthority.normal("test.native_memory_fence")
    with Session(engine) as db:
        _seed_pipeline(db, 43)
        db.add(BackgroundWorker(worker_id="memory-worker", instance_id="first", generation=1))
        db.add_all(
            [
                PipelineStep(
                    pipeline_run_id=43, step_name="VALIDATING_RUN", step_order=1, status="COMPLETED"
                ),
                PipelineStep(
                    pipeline_run_id=43,
                    step_name="CAPTURING_SETUP_SIGNALS",
                    step_order=11,
                    status="RUNNING",
                ),
            ]
        )
        job = enqueue_job(db, "FULL_PIPELINE", {"pipeline_run_id": 43}, related_run_id=43)
        db.commit()
        job_id = job.id
    for attempt in range(1, 4):
        with Session(engine) as db:
            worker = db.get(BackgroundWorker, "memory-worker")
            worker.instance_id = f"generation-{attempt}"
            worker.generation = attempt
            db.commit()
            job = claim_next_job(db, "memory-worker", worker_instance_id=f"generation-{attempt}")
            assert job.id == job_id
            token = job.execution_token
            db.commit()
            record_job_progress(
                db,
                job_id=job_id,
                execution_token=token,
                stage="CAPTURING_SETUP_SIGNALS",
                current_item=f"REPLAY:{attempt}",
                processed=attempt * 25,
            )
            db.commit()
        with Session(engine) as recovery:
            result = reconcile_jobs_for_worker_loss(
                recovery,
                authority=authority,
                worker_id="memory-worker",
                worker_instance_id=f"generation-{attempt}",
                reason="Worker exceeded 6144 MB memory budget.",
                failure_code="WORKER_MEMORY_CRITICAL",
                now=datetime.now(UTC),
            )
            assert result.fenced_job_ids == (job_id,)
            assert requeue_stalled_jobs(
                recovery, authority=authority, job_ids=[job_id], max_identical_recoveries=3
            ) == (attempt < 3)
            recovery.commit()
        with Session(engine) as late:
            with pytest.raises(JobLeaseLost):
                record_job_progress(
                    late, job_id=job_id, execution_token=token, stage="CAPTURING_SETUP_SIGNALS"
                )
            late.rollback()
    with Session(engine) as verify:
        job = verify.get(BackgroundJob, job_id)
        assert job.status == verify.get(PipelineRun, 43).status == "BLOCKED"
        assert job.retry_count == 0 and job.recovery_count == 2
        assert job.execution_token is None
        assert (
            job.operational_metadata_json["blocked"]["reason_code"]
            == "REPEATED_MEMORY_CRITICAL_LIMIT_REACHED"
        )
        assert "6144 MB" in job.error_message
        assert (
            verify.scalar(select(PipelineStep).where(PipelineStep.step_order == 11)).status
            == "BLOCKED"
        )
    engine.dispose()
