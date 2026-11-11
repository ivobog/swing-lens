"""Native queue regressions for the pipeline 59/60 starvation incident.

Financial/provider outputs are deliberately outside this scheduler qualification.
Claims, barriers, ownership, terminalization and progress updates use real PG rows.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from integration.test_queue_fairness_postgresql import _fair_claim, _seed_pipeline, _upgrade
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.tables import BackgroundJob, PipelineRun
from app.routers.run_routes import _pipeline_observability_job
from app.services.background_job_service import (
    fence_stalled_jobs,
    mark_job_completed,
    mark_job_deferred,
    record_job_progress,
)
from app.services.background_queue import WorkerClaimState
from app.services.ceri.batched_claims import STAGE_PREREQUISITES, ceri_dependency_wait
from app.services.ceri.batched_job_handlers import _require_terminal_stage
from app.services.ceri.batched_workflow import build_ceri_batched_workflow_plan
from app.services.ceri.enums import CeriDataset
from app.services.pipeline_prerequisites import CeriUpstreamStageBlockedError
from app.services.runtime_mutation_authority import RecoveryAuthority
from app.settings import Settings


def _job(kind, *, workflow="old-workflow", priority=1, payload=None, status="QUEUED"):
    old = datetime.now(UTC) - timedelta(hours=12)
    return BackgroundJob(
        job_type=kind,
        workflow_key=workflow,
        priority=priority,
        status=status,
        payload_json=payload or {},
        created_at=old,
        run_after=old,
    )


@pytest.mark.parametrize("companies", [150, 466])
def test_aged_workflow_and_new_sec_child_advance_without_barrier_claims(
    disposable_postgres_database,
    companies,
):
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    plan = build_ceri_batched_workflow_plan(
        run_id=59,
        tickers=[f"T{index:04}" for index in range(companies)],
        config_hash="scheduler-regression",
        settings=Settings(
            _env_file=None,
            ceri_provider_batch_size=25,
            ceri_normalization_batch_size=50,
            ceri_feature_batch_size=50,
        ),
        provider_datasets={
            "eodhd": (CeriDataset.ESTIMATES, CeriDataset.EARNINGS, CeriDataset.CATALYSTS),
            "sec": (CeriDataset.GUIDANCE,),
        },
    )
    state = WorkerClaimState()
    with Session(engine, expire_on_commit=False) as db:
        _seed_pipeline(db, 59)
        _seed_pipeline(db, 60)
        for spec in plan.jobs:
            # Legacy queued work must also recover without rewriting priorities.
            priority = 81 if spec.job_type == "CERI_NORMALIZE_BATCH" else spec.priority
            row = _job(
                spec.job_type,
                workflow=plan.workflow_key,
                priority=priority,
                payload={**spec.payload, "pipeline_run_id": 59},
            )
            row.related_run_id = 59
            db.add(row)
        db.commit()
        first = _fair_claim(db, state, worker_id="single-worker")
        assert first.job_type == "CERI_PROVIDER_INGEST_BATCH"
        observed = _pipeline_observability_job(
            db,
            SimpleNamespace(
                status="WAITING_FOR_CERI_COMPLETION",
                result_json={"ceri_provider_workflow_key": plan.workflow_key},
            ),
            None,
        )
        assert observed.id == first.id
        mark_job_completed(db, first, {}, execution_token=first.execution_token)
        sec = _job(
            "SEC_READINESS_REPAIR", workflow=None, priority=70, payload={"pipeline_run_id": 60}
        )
        sec.created_at = datetime.now(UTC)
        db.add(sec)
        db.commit()
        child = _fair_claim(db, state, worker_id="single-worker")
        assert child.id == sec.id
        mark_job_completed(db, child, {}, execution_token=child.execution_token)
        db.commit()
        completed = 1
        while (job := _fair_claim(db, state, worker_id="single-worker")) is not None:
            prerequisite = STAGE_PREREQUISITES.get(job.job_type)
            if prerequisite:
                stage, expected_field = prerequisite
                _require_terminal_stage(
                    db,
                    job.workflow_key,
                    stage,
                    expected=(
                        int(job.payload_json.get(expected_field) or 0) if expected_field else 0
                    ),
                )
            assert job.last_progress_at is None
            assert job.progress_sequence == 0
            mark_job_completed(db, job, {}, execution_token=job.execution_token)
            db.commit()
            completed += 1
        assert completed == len(plan.jobs)
        assert all(
            row.status == "COMPLETED"
            for row in db.scalars(
                select(BackgroundJob).where(BackgroundJob.workflow_key == plan.workflow_key),
            )
        )
    engine.dispose()


def test_missing_partial_failed_and_cross_workflow_barriers(disposable_postgres_database):
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine, expire_on_commit=False) as db:
        downstream = _job("CERI_FEATURE_BATCH", payload={"expected_normalization_batches": 2})
        db.add(downstream)
        # Terminal work in another workflow must not release this barrier.
        db.add(_job("CERI_NORMALIZE_BATCH", workflow="unrelated", status="COMPLETED"))
        db.commit()
        state = WorkerClaimState()
        assert _fair_claim(db, state, worker_id="single-worker") is None
        waiting = ceri_dependency_wait(db, downstream)
        assert waiting["expected_batches"] == 2
        assert waiting["total_batches"] == 0
        first = _job("CERI_NORMALIZE_BATCH", status="PARTIAL")
        db.add(first)
        db.commit()
        assert _fair_claim(db, state, worker_id="single-worker") is None
        # An unsuccessful terminal stage must reach its deterministic error even
        # when the expected count differs. It must never spin on a creation wait.
        first.status = "FAILED"
        db.commit()
        claimed = _fair_claim(db, state, worker_id="single-worker")
        assert claimed.id == downstream.id
        with pytest.raises(CeriUpstreamStageBlockedError):
            _require_terminal_stage(db, claimed.workflow_key, "CERI_NORMALIZE_BATCH", expected=2)
        db.rollback()
        downstream.status = "CANCELLED"
        first.status = "PARTIAL"
        # Older deliveries may carry workflow identity only in their payload.
        # Malformed expected counts must not make the queue-wide SQL cast fail.
        legacy = _job(
            "CERI_FEATURE_BATCH",
            workflow=None,
            payload={
                "workflow_key": "old-workflow",
                "expected_normalization_batches": "invalid",
            },
        )
        db.add(legacy)
        db.commit()
        assert _fair_claim(db, state, worker_id="single-worker").id == legacy.id
        db.rollback()
    engine.dispose()


def test_deferred_reclaim_preserves_progress_and_fences_only_current_attempt(
    disposable_postgres_database,
):
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine, expire_on_commit=False) as db:
        db.add(_job("CERI_CAPTURE_RUN"))
        db.commit()
        state = WorkerClaimState()
        job = _fair_claim(db, state, worker_id="single-worker")
        token = job.execution_token
        record_job_progress(
            db, job_id=job.id, execution_token=token, stage="CERI_CAPTURE_RUN", processed=1, total=2
        )
        db.refresh(job)
        useful_at, sequence = job.last_progress_at, job.progress_sequence
        mark_job_deferred(
            db,
            job,
            delay=timedelta(seconds=0),
            reason="waiting for dependency",
            execution_token=token,
        )
        db.commit()
        job = _fair_claim(db, state, worker_id="single-worker")
        assert job.last_progress_at == useful_at
        assert job.progress_sequence == sequence
        assert job.operational_metadata_json["dependency_wait"]["deferral_count"] == 1
        authority = RecoveryAuthority.normal("test.scheduler-readiness")
        assert (
            fence_stalled_jobs(
                db,
                authority=authority,
                default_timeout_seconds=300,
                market_data_timeout_seconds=300,
                long_stage_timeout_seconds=300,
                now=job.locked_at + timedelta(seconds=299),
            )
            == []
        )
        assert fence_stalled_jobs(
            db,
            authority=authority,
            default_timeout_seconds=300,
            market_data_timeout_seconds=300,
            long_stage_timeout_seconds=300,
            now=job.locked_at + timedelta(seconds=301),
        ) == [job.id]
        db.add(_job("CERI_CAPTURE_RUN", workflow="no-progress-yet"))
        db.commit()
        fresh = _fair_claim(db, state, worker_id="single-worker")
        assert fresh.last_progress_at is None and fresh.progress_sequence == 0
        assert fence_stalled_jobs(
            db,
            authority=authority,
            default_timeout_seconds=300,
            market_data_timeout_seconds=300,
            long_stage_timeout_seconds=300,
            now=fresh.locked_at + timedelta(seconds=301),
        ) == [fresh.id]
        db.rollback()
    engine.dispose()


def test_sec_fairness_and_cancelled_parent_authority_remain_enforced(disposable_postgres_database):
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine, expire_on_commit=False) as db:
        for pipeline_id in (59, 60):
            _seed_pipeline(db, pipeline_id)
        for index in range(5):
            db.add(
                _job(
                    "SEC_READINESS_REPAIR",
                    workflow=None,
                    payload={"pipeline_run_id": 60, "index": index},
                )
            )
        db.add(_job("CERI_PROVIDER_INGEST_BATCH", payload={"pipeline_run_id": 59}))
        db.commit()
        state = WorkerClaimState(consecutive_interactive_claims=4)
        first = _fair_claim(db, state, worker_id="single-worker")
        assert first.job_type == "CERI_PROVIDER_INGEST_BATCH"
        mark_job_completed(db, first, {}, execution_token=first.execution_token)
        db.commit()
        assert _fair_claim(db, state, worker_id="single-worker").job_type == "SEC_READINESS_REPAIR"
        db.rollback()
        for pipeline in db.scalars(select(PipelineRun)):
            pipeline.status = "CANCELLED"
        db.commit()
        assert _fair_claim(db, state, worker_id="single-worker") is None
    engine.dispose()
