"""Deployment fence preserves history while native new-job control keeps working."""

from datetime import UTC, datetime, timedelta

from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from test_pipeline_authority_certification_postgresql import _seed_dependency, _seed_pipeline

from app.database_safety import run_guarded_alembic_upgrade
from app.models.tables import BackgroundJob, PipelineDependency, PipelineRun, UploadRun
from app.services.background_job_service import (
    claim_next_job,
    fence_stalled_jobs,
    heartbeat_job,
    mark_job_completed,
    mark_job_failed_or_retry,
    reconcile_jobs_for_worker_loss,
    record_job_progress,
    recover_abandoned_jobs_for_worker,
    recover_stale_jobs,
)
from app.services.pipeline_dependency_service import (
    reconcile_pipeline_job,
    reconcile_safe_pipeline_invariants,
)
from app.services.pipeline_execution_authority import job_execution_authority_predicate
from app.services.runtime_mutation_authority import RecoveryAuthority
from app.services.worker_registry import register_worker
from app.settings import Settings


def _fence(monkeypatch, job_min, pipeline_min):
    settings = Settings(
        _env_file=None,
        job_activation_min_id=job_min,
        pipeline_activation_min_id=pipeline_min,
    )
    monkeypatch.setattr("app.services.pipeline_execution_authority.get_settings", lambda: settings)


def _register(db):
    register_worker(
        db,
        worker_id="activation-worker",
        instance_id="fresh-instance",
        queues=("background", "interactive"),
        heartbeat_timeout_seconds=30,
        hostname="disposable",
        process_id=12345,
    )


def _claim(db):
    return claim_next_job(db, "activation-worker", worker_instance_id="fresh-instance")


def test_exact_50_old_jobs_excluded_but_new_claim_retry_progress_and_recovery_work(
    disposable_postgres_database,
    monkeypatch,
):
    run_guarded_alembic_upgrade(Config("alembic.ini"), disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    now = datetime.now(UTC)
    expired = now - timedelta(hours=1)
    old_ids = [*range(182, 231), 1384]
    with Session(engine) as db:
        upload = UploadRun(filename="activation.csv", status="COMPLETED")
        db.add(upload)
        db.flush()
        db.add_all(
            [
                PipelineRun(id=1, upload_run_id=upload.id, status="RUNNING"),
                PipelineRun(id=50, upload_run_id=upload.id, status="RUNNING"),
            ]
        )
        db.flush()
        for job_id in old_ids:
            db.add(
                BackgroundJob(
                    id=job_id,
                    job_type="ACTIVATION_TEST",
                    status="RUNNING" if job_id == 182 else "QUEUED",
                    payload_json={},
                    run_after=expired,
                    retry_count=0,
                    max_retries=3,
                    worker_id="old-worker" if job_id == 182 else None,
                    worker_instance_id="old-instance" if job_id == 182 else None,
                    execution_token="old-token" if job_id == 182 else None,
                    heartbeat_at=expired,
                    lease_expires_at=expired,
                    last_progress_at=expired,
                    operational_metadata_json={},
                )
            )
        for job_id, extra in [
            (2000, {}),
            (2001, {"payload_json": {"pipeline_run_id": 50}}),
            (2002, {"payload_json": {"pipeline_run_id": 1}}),
            (2003, {"root_job_id": 182}),
            (2004, {"parent_job_id": 183}),
            (2005, {"triggered_by_job_id": 184}),
            (2006, {"status": "RUNNING", "lease_expires_at": expired}),
        ]:
            values = dict(
                id=job_id,
                job_type="ACTIVATION_TEST",
                status="QUEUED",
                payload_json={},
                run_after=expired,
                retry_count=0,
                max_retries=3,
                operational_metadata_json={},
            )
            values.update(extra)
            db.add(BackgroundJob(**values))
        _register(db)
        db.commit()
        before = (
            db.execute(
                text(
                    "SELECT row_to_json(j)::text FROM background_jobs j WHERE id < 2000 ORDER BY id"
                )
            )
            .scalars()
            .all()
        )
        _fence(monkeypatch, 2000, 50)
        assert set(
            db.scalars(select(BackgroundJob.id).where(job_execution_authority_predicate()))
        ) == {2000, 2001, 2006}
        authority = RecoveryAuthority.normal("activation.disposable")
        assert recover_stale_jobs(db, 30, authority=authority) == 1
        assert (
            recover_abandoned_jobs_for_worker(
                db, authority=authority, worker_id="old-worker", heartbeat_timeout_seconds=30
            )
            == 0
        )
        assert (
            fence_stalled_jobs(
                db, authority=authority, default_timeout_seconds=30, market_data_timeout_seconds=30
            )
            == []
        )
        lost = reconcile_jobs_for_worker_loss(
            db, authority=authority, worker_id="old-worker", reason="disposable"
        )
        assert lost.untouched_job_ids == (182,)
        assert not lost.fenced_job_ids
        job = _claim(db)
        assert job.id == 2000
        token = job.execution_token
        heartbeat_job(db, job, execution_token=token)
        record_job_progress(
            db, job_id=job.id, execution_token=token, stage="TEST", processed=1, total=2
        )
        mark_job_failed_or_retry(
            db,
            job,
            RuntimeError("temporary"),
            retry_delay=lambda _: timedelta(0),
            execution_token=token,
        )
        db.commit()
        db.refresh(job)
        assert job.status == "QUEUED"
        claimed = []
        for _ in range(3):
            job = _claim(db)
            assert job is not None
            claimed.append(job.id)
            if job.id == 2000:
                assert job.execution_token != token
            mark_job_completed(db, job, execution_token=job.execution_token)
        assert set(claimed) == {2000, 2001, 2006}
        assert _claim(db) is None
        db.commit()
        after = (
            db.execute(
                text(
                    "SELECT row_to_json(j)::text FROM background_jobs j WHERE id < 2000 ORDER BY id"
                )
            )
            .scalars()
            .all()
        )
        assert after == before
    engine.dispose()


def test_historical_handoffs_untouched_and_new_native_continuation_is_idempotent(
    disposable_postgres_database,
    monkeypatch,
):
    run_guarded_alembic_upgrade(Config("alembic.ini"), disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    old_pending, _ = _seed_pipeline(engine)
    _seed_dependency(engine, old_pending, enqueue=False)
    old_complete, _ = _seed_pipeline(engine)
    _, old_child = _seed_dependency(engine, old_complete)
    with Session(engine) as db:
        db.get(BackgroundJob, old_child).status = "COMPLETED"
        db.commit()
        old_max = db.scalar(select(BackgroundJob.id).order_by(BackgroundJob.id.desc()).limit(1))
    new_pipeline, new_root = _seed_pipeline(engine)
    dependency_id, child_id = _seed_dependency(engine, new_pipeline)
    with Session(engine) as db:
        db.get(BackgroundJob, child_id).status = "COMPLETED"
        db.commit()
        before = {}
        for table in ("background_jobs", "pipeline_runs", "pipeline_dependencies"):
            where = (
                "id <= :boundary"
                if table != "pipeline_dependencies"
                else "pipeline_run_id <= :boundary"
            )
            boundary = old_max if table == "background_jobs" else old_complete
            before[table] = (
                db.execute(
                    text(f"SELECT row_to_json(t)::text FROM {table} t WHERE {where} ORDER BY id"),
                    {"boundary": boundary},
                )
                .scalars()
                .all()
            )
        _fence(monkeypatch, old_max + 1, old_complete + 1)
        outcome = reconcile_safe_pipeline_invariants(db)
        continuation = db.get(PipelineDependency, dependency_id).continuation_job_id
        assert outcome["continuations_enqueued"] == (continuation,)
        assert db.get(BackgroundJob, continuation).root_job_id == new_root
        reconcile_pipeline_job(db, old_child)
        assert reconcile_safe_pipeline_invariants(db)["continuations_enqueued"] == ()
        _register(db)
        db.commit()
        job = _claim(db)
        assert job.id == continuation
        heartbeat_job(db, job, execution_token=job.execution_token)
        mark_job_completed(db, job, execution_token=job.execution_token)
        db.commit()
        for table, expected in before.items():
            where = (
                "id <= :boundary"
                if table != "pipeline_dependencies"
                else "pipeline_run_id <= :boundary"
            )
            boundary = old_max if table == "background_jobs" else old_complete
            assert (
                db.execute(
                    text(f"SELECT row_to_json(t)::text FROM {table} t WHERE {where} ORDER BY id"),
                    {"boundary": boundary},
                )
                .scalars()
                .all()
                == expected
            )
    engine.dispose()
