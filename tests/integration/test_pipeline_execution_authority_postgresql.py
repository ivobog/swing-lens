from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.database_safety import run_guarded_alembic_upgrade
from app.models.tables import BackgroundJob, PipelineRun, UploadRun
from app.services.background_job_service import (
    JobStatus,
    claim_next_job,
    enqueue_job,
    mark_job_completed,
    reconcile_jobs_for_worker_loss,
    recover_stale_jobs,
)
from app.services.pipeline_execution_authority import claimable_pipeline_job_ids
from app.services.runtime_mutation_authority import RecoveryAuthority
from app.services.worker_registry import register_worker


def test_historical_pipeline_jobs_are_never_claimed_or_recovered_after_0089(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database, revision="0088_ceri_run_source_lineage")
    engine = create_engine(disposable_postgres_database)
    expired = datetime.now(UTC) - timedelta(hours=1)
    with engine.begin() as connection:
        connection.execute(
            text(
                "insert into upload_runs(id,filename,status,row_count) "
                "values (9,'historical.csv','COMPLETED',2)"
            )
        )
        connection.execute(
            text(
                "insert into pipeline_runs(id,upload_run_id,status,current_step) "
                "values (9,9,'WAITING_FOR_CERI_COMPLETION','CERI_PROVIDER_INGEST')"
            )
        )
        connection.execute(
            text(
                """
                insert into background_jobs(
                    id,job_type,related_run_id,status,payload_json,run_after,
                    worker_id,worker_instance_id,lease_owner,execution_token,
                    heartbeat_at,lease_expires_at
                ) values
                (901,'CERI_PROVIDER_INGEST_BATCH',9,'RUNNING',cast(:running_payload as jsonb),
                 :expired,'lost-worker','lost-instance','lost-worker','expired-token',
                 :expired,:expired),
                (902,'CERI_NORMALIZE_BATCH',9,'QUEUED',cast(:child_payload as jsonb),
                 :expired,null,null,null,null,null,null),
                (903,'FULL_PIPELINE',9,'QUEUED',cast(:continuation_payload as jsonb),
                 :expired,null,null,null,null,null,null)
                """
            ),
            {
                "expired": expired,
                "running_payload": json.dumps({"pipeline_run_id": 9, "tickers": ["AAPL", "ACLS"]}),
                "child_payload": json.dumps({"pipeline_run_id": 9}),
                "continuation_payload": json.dumps(
                    {
                        "pipeline_run_id": 9,
                        "resume_from_step": "CERI_PROVIDER_INGEST",
                    }
                ),
            },
        )
    engine.dispose()

    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        historical = db.get(PipelineRun, 9)
        assert historical.execution_authority_state is None
        register_worker(
            db,
            worker_id="authority-worker",
            queues=("background",),
            heartbeat_timeout_seconds=30,
            hostname="test-host",
            process_id=12345,
            instance_id="authority-instance",
        )
        db.commit()
        before = {
            row.id: (row.status, row.execution_token, row.progress_sequence)
            for row in db.scalars(select(BackgroundJob).where(BackgroundJob.related_run_id == 9))
        }
        assert (
            recover_stale_jobs(
                db,
                30,
                authority=RecoveryAuthority.normal("test.historical-recovery"),
            )
            == 0
        )
        reconciliation = reconcile_jobs_for_worker_loss(
            db,
            authority=RecoveryAuthority.normal("test.historical-supervisor"),
            worker_id="lost-worker",
            worker_instance_id="lost-instance",
            reason="worker exited",
        )
        assert reconciliation.fenced_job_ids == ()
        assert reconciliation.untouched_job_ids == (901,)
        assert (
            claim_next_job(
                db,
                "authority-worker",
                worker_instance_id="authority-instance",
                queues=("background",),
            )
            is None
        )
        assert claimable_pipeline_job_ids(db, upload_run_id=9) == ()
        after = {
            row.id: (row.status, row.execution_token, row.progress_sequence)
            for row in db.scalars(select(BackgroundJob).where(BackgroundJob.related_run_id == 9))
        }
        assert after == before
        db.rollback()

    with Session(engine) as db:
        run = UploadRun(filename="valid.csv", status="COMPLETED", row_count=2)
        db.add(run)
        db.flush()
        pipeline = PipelineRun(upload_run_id=run.id, status="RUNNING")
        db.add(pipeline)
        db.flush()
        assert pipeline.execution_authority_state == "ACTIVE"
        queued = enqueue_job(
            db,
            "CERI_PROVIDER_INGEST_BATCH",
            {"pipeline_run_id": pipeline.id, "tickers": ["AAPL", "ACLS"]},
            related_run_id=run.id,
            request_key="valid-provider-child",
        )
        continuation = enqueue_job(
            db,
            "FULL_PIPELINE",
            {"pipeline_run_id": pipeline.id, "resume_from_step": "CERI_PROVIDER_INGEST"},
            related_run_id=run.id,
            request_key="valid-continuation",
            parent_job_id=queued.id,
            priority=110,
        )
        expired_job = enqueue_job(
            db,
            "CERI_NORMALIZE_BATCH",
            {"pipeline_run_id": pipeline.id},
            related_run_id=run.id,
            request_key="valid-expired-child",
        )
        expired_job.status = JobStatus.RUNNING
        expired_job.worker_id = "crashed-worker"
        expired_job.worker_instance_id = "crashed-instance"
        expired_job.execution_token = "crashed-token"
        expired_job.lease_expires_at = expired
        expired_job.heartbeat_at = expired
        db.commit()

        assert (
            recover_stale_jobs(
                db,
                30,
                authority=RecoveryAuthority.normal("test.valid-recovery"),
            )
            == 1
        )
        assert set(claimable_pipeline_job_ids(db, upload_run_id=run.id)) == {
            continuation.id,
            expired_job.id,
            queued.id,
        }
        claimed_ids: list[int] = []
        for _ in range(2):
            claimed = claim_next_job(
                db,
                "authority-worker",
                worker_instance_id="authority-instance",
                queues=("background",),
            )
            assert claimed is not None, f"claim sequence stopped after {claimed_ids}"
            claimed_ids.append(claimed.id)
            mark_job_completed(db, claimed, execution_token=claimed.execution_token)
        claimed = claim_next_job(
            db,
            "authority-worker",
            worker_instance_id="authority-instance",
            queues=("interactive",),
        )
        assert claimed is not None
        claimed_ids.append(claimed.id)
        mark_job_completed(db, claimed, execution_token=claimed.execution_token)
        assert set(claimed_ids) == {queued.id, continuation.id, expired_job.id}
        assert len(claimed_ids) == len(set(claimed_ids))
        assert (
            claim_next_job(
                db,
                "authority-worker",
                worker_instance_id="authority-instance",
                queues=("background",),
            )
            is None
        )
        db.rollback()
    engine.dispose()


def _upgrade(database_url: str, *, revision: str = "head") -> None:
    config = Config("alembic.ini")
    run_guarded_alembic_upgrade(config, database_url, revision=revision)
