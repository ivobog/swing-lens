from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.database_safety import assert_disposable_database
from app.models.tables import (
    BackgroundJob,
    BackgroundWorker,
    PipelineDependency,
    PipelineRun,
    PipelineStep,
    RawCompanyRow,
    UploadRun,
)
from app.services.background_job_service import JobStatus, enqueue_job, heartbeat_job
from app.services.domain_write_fence import (
    assert_current_execution_ownership,
    control_plane_transaction,
    deferred_execution_ownership_lock,
    fence_domain_commits,
)
from app.services.ib_fetch_executor import _bounded_item_session
from app.services.pipeline_dependency_service import (
    enqueue_sec_readiness_dependency,
    prepare_sec_readiness_dependency,
    reconcile_pending_dependency_enqueues,
    reconcile_pipeline_job,
    reconcile_safe_pipeline_invariants,
)
from app.services.pipeline_invariant_service import inspect_pipeline_invariants
from app.services.pipeline_service import (
    PipelineCancellationContended,
    PipelineStatus,
    PipelineStepStatus,
    cancel_pipeline,
)
from app.services.pipeline_state_machine import PipelineTransitionError, transition_pipeline


@pytest.fixture(scope="module")
def authority_database_url() -> Iterator[str]:
    admin_url = os.environ.get(
        "SWINGLENS_TEST_POSTGRES_ADMIN_URL",
        "postgresql://postgres:postgres@127.0.0.1:5432/postgres",
    )
    name = f"swinglens_pytest_{uuid.uuid4().hex[:12]}"
    try:
        admin = psycopg.connect(admin_url, autocommit=True)
    except psycopg.Error as exc:
        pytest.skip(f"PostgreSQL admin database unavailable: {exc}")
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = make_url(admin_url.replace("postgresql://", "postgresql+psycopg://", 1)).set(
        database=name
    )
    database_url = url.render_as_string(hide_password=False)
    assert_disposable_database(database_url)
    previous = os.environ.get("SWINGLENS_DATABASE_SAFETY_CONTEXT")
    os.environ["SWINGLENS_DATABASE_SAFETY_CONTEXT"] = "DISPOSABLE_TEST"
    try:
        env = os.environ.copy()
        env["DATABASE_URL"] = database_url
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
            env=env,
            capture_output=True,
            text=True,
        )
        yield database_url
    finally:
        if previous is None:
            os.environ.pop("SWINGLENS_DATABASE_SAFETY_CONTEXT", None)
        else:
            os.environ["SWINGLENS_DATABASE_SAFETY_CONTEXT"] = previous
        admin.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
        )
        admin.close()


def test_04_cancel_while_waiting_for_dependency(authority_database_url: str) -> None:
    engine = create_engine(authority_database_url)
    pipeline_id, _root_id = _seed_pipeline(engine)
    dependency_id, child_id = _seed_dependency(engine, pipeline_id)
    with Session(engine) as db:
        cancelled = cancel_pipeline(db, pipeline_id)
        db.commit()
        assert cancelled.status == PipelineStatus.CANCELLED
        assert db.get(BackgroundJob, child_id).status == JobStatus.CANCELLED
        assert db.get(PipelineDependency, dependency_id).state == "CANCELLED"
    engine.dispose()


def test_05_cancel_while_child_running(authority_database_url: str) -> None:
    engine = create_engine(authority_database_url)
    pipeline_id, _root_id = _seed_pipeline(engine)
    _dependency_id, child_id = _seed_dependency(engine, pipeline_id)
    with Session(engine) as db:
        child = db.get(BackgroundJob, child_id)
        _make_running(db, child, worker_id="cancel-child")
        db.commit()
        result = cancel_pipeline(db, pipeline_id)
        db.commit()
        assert result.status == PipelineStatus.CANCEL_REQUESTED
        assert db.get(BackgroundJob, child_id).requested_cancel is True
    engine.dispose()


def test_06_app_restart_recovers_wait_contract_enqueue_gap(
    authority_database_url: str,
) -> None:
    engine = create_engine(authority_database_url)
    pipeline_id, _root_id = _seed_pipeline(engine)
    dependency_id, _child_id = _seed_dependency(engine, pipeline_id, enqueue=False)
    with Session(engine) as restarted:
        created = reconcile_pending_dependency_enqueues(restarted)
        restarted.commit()
        dependency = restarted.get(PipelineDependency, dependency_id)
        assert len(created) == 1
        assert dependency.state == "QUEUED"
        assert dependency.child_job_id == created[0]
    engine.dispose()


def test_07_worker_restart_preserves_waiting_child(authority_database_url: str) -> None:
    engine = create_engine(authority_database_url)
    pipeline_id, _root_id = _seed_pipeline(engine)
    dependency_id, child_id = _seed_dependency(engine, pipeline_id)
    with Session(engine) as restarted:
        pipeline = restarted.get(PipelineRun, pipeline_id)
        dependency = restarted.get(PipelineDependency, dependency_id)
        child = restarted.get(BackgroundJob, child_id)
        assert pipeline.status == PipelineStatus.WAITING_DEPENDENCY
        assert dependency.state == "QUEUED"
        assert child.status == JobStatus.QUEUED
    engine.dispose()


def test_08_child_completion_replayed_twice_is_idempotent(
    authority_database_url: str,
) -> None:
    engine = create_engine(authority_database_url)
    pipeline_id, _root_id = _seed_pipeline(engine)
    dependency_id, child_id = _seed_dependency(engine, pipeline_id)
    continuation_id = _complete_and_reconcile(engine, child_id)
    with Session(engine) as db:
        reconcile_pipeline_job(db, child_id)
        db.commit()
        dependency = db.get(PipelineDependency, dependency_id)
        assert dependency.continuation_job_id == continuation_id
        assert _continuations(db, dependency_id) == [continuation_id]
    engine.dispose()


def test_09_simultaneous_continuation_submission_is_exactly_once(
    authority_database_url: str,
) -> None:
    engine = create_engine(authority_database_url)
    pipeline_id, _root_id = _seed_pipeline(engine)
    dependency_id, child_id = _seed_dependency(engine, pipeline_id)
    with Session(engine) as db:
        child = db.get(BackgroundJob, child_id)
        child.status = JobStatus.COMPLETED
        child.completed_at = datetime.now(UTC)
        child.result_json = {"status": "COMPLETED"}
        db.commit()
    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def submit() -> None:
        try:
            with Session(engine) as db:
                barrier.wait(timeout=5)
                reconcile_pipeline_job(db, child_id)
                db.commit()
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=submit), threading.Thread(target=submit)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    with Session(engine) as db:
        assert errors == []
        assert len(_continuations(db, dependency_id)) == 1
    engine.dispose()


def test_10_two_pipelines_can_require_sec_simultaneously(
    authority_database_url: str,
) -> None:
    engine = create_engine(authority_database_url)
    pipeline_ids = [_seed_pipeline(engine)[0] for _ in range(2)]
    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def create_dependency(pipeline_id: int) -> None:
        try:
            barrier.wait(timeout=5)
            _seed_dependency(engine, pipeline_id)
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [
        threading.Thread(target=create_dependency, args=(pipeline_id,))
        for pipeline_id in pipeline_ids
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    with Session(engine) as db:
        assert errors == []
        rows = list(
            db.scalars(
                select(PipelineDependency).where(
                    PipelineDependency.pipeline_run_id.in_(pipeline_ids)
                )
            )
        )
        assert len(rows) == 2
        assert {row.state for row in rows} == {"QUEUED"}
    engine.dispose()


def test_11_root_heartbeat_during_child_creation_does_not_block(
    authority_database_url: str,
) -> None:
    engine = create_engine(authority_database_url)
    pipeline_id, root_id = _seed_pipeline(engine, root_status=JobStatus.RUNNING)
    dependency_id, _child_id = _seed_dependency(engine, pipeline_id, enqueue=False)
    with Session(engine) as child_tx:
        enqueue_sec_readiness_dependency(child_tx, dependency_id=dependency_id)
        with Session(engine) as control:
            root = control.get(BackgroundJob, root_id)
            started = time.monotonic()
            heartbeat_job(control, root, execution_token=root.execution_token)
            control.commit()
            elapsed = time.monotonic() - started
        child_tx.rollback()
    assert elapsed < 2
    engine.dispose()


def test_12_supervisor_inspection_during_active_transaction_is_bounded(
    authority_database_url: str,
) -> None:
    engine = create_engine(authority_database_url)
    pipeline_id, _root_id = _seed_pipeline(engine)
    with Session(engine) as active:
        pipeline = active.get(PipelineRun, pipeline_id)
        pipeline.message = "uncommitted diagnostic mutation"
        active.flush()
        with Session(engine) as observer:
            observer.execute(text("SET LOCAL statement_timeout = '1500ms'"))
            started = time.monotonic()
            findings = inspect_pipeline_invariants(observer)
            elapsed = time.monotonic() - started
        active.rollback()
    assert isinstance(findings, tuple)
    assert elapsed < 1.5
    engine.dispose()


def test_13_lock_timeout_cancellation_is_bounded_and_diagnostic(
    authority_database_url: str,
) -> None:
    engine = create_engine(authority_database_url)
    pipeline_id, root_id = _seed_pipeline(engine, root_status=JobStatus.RUNNING)
    locked = threading.Event()
    release = threading.Event()

    def hold_root_lock() -> None:
        with Session(engine) as db:
            db.execute(
                select(BackgroundJob.id)
                .where(BackgroundJob.id == root_id)
                .with_for_update()
            )
            locked.set()
            release.wait(timeout=10)
            db.rollback()

    locker = threading.Thread(target=hold_root_lock)
    locker.start()
    assert locked.wait(timeout=5)
    started = time.monotonic()
    try:
        with Session(engine) as db, pytest.raises(PipelineCancellationContended) as caught:
            cancel_pipeline(db, pipeline_id)
    finally:
        release.set()
        locker.join(timeout=5)
    elapsed = time.monotonic() - started
    assert elapsed < 3
    assert caught.value.diagnostics["code"] == "PIPELINE_CANCELLATION_LOCK_CONTENDED"
    assert caught.value.diagnostics["sessions"]
    with Session(engine) as db:
        assert db.get(PipelineRun, pipeline_id).status == PipelineStatus.CANCEL_REQUESTED
    engine.dispose()


def test_14_child_failure_fails_dependency_and_pipeline(authority_database_url: str) -> None:
    engine = create_engine(authority_database_url)
    pipeline_id, _root_id = _seed_pipeline(engine)
    dependency_id, child_id = _seed_dependency(engine, pipeline_id)
    with Session(engine) as db:
        child = db.get(BackgroundJob, child_id)
        child.status = JobStatus.FAILED
        child.error_message = "bounded failure"
        child.completed_at = datetime.now(UTC)
        db.commit()
        reconcile_pipeline_job(db, child_id)
        db.commit()
        assert db.get(PipelineDependency, dependency_id).state == "FAILED"
        assert db.get(PipelineRun, pipeline_id).status == PipelineStatus.FAILED
    engine.dispose()


def test_15_child_retry_keeps_dependency_waiting(authority_database_url: str) -> None:
    engine = create_engine(authority_database_url)
    pipeline_id, _root_id = _seed_pipeline(engine)
    dependency_id, child_id = _seed_dependency(engine, pipeline_id)
    with Session(engine) as db:
        child = db.get(BackgroundJob, child_id)
        child.status = JobStatus.QUEUED
        child.retry_count = 1
        db.commit()
        reconcile_pipeline_job(db, child_id)
        db.commit()
        assert db.get(PipelineDependency, dependency_id).state == "QUEUED"
        assert db.get(PipelineRun, pipeline_id).status == PipelineStatus.WAITING_DEPENDENCY
    engine.dispose()


def test_16_parent_cancel_before_child_completion_wins(authority_database_url: str) -> None:
    engine = create_engine(authority_database_url)
    pipeline_id, _root_id = _seed_pipeline(engine)
    dependency_id, child_id = _seed_dependency(engine, pipeline_id)
    with Session(engine) as db:
        child = db.get(BackgroundJob, child_id)
        _make_running(db, child, worker_id="cancel-race")
        db.commit()
        cancel_pipeline(db, pipeline_id)
        child = db.get(BackgroundJob, child_id)
        child.status = JobStatus.COMPLETED
        child.completed_at = datetime.now(UTC)
        child.execution_token = None
        child.worker_id = None
        child.worker_instance_id = None
        db.commit()
        result = reconcile_safe_pipeline_invariants(db)
        db.commit()
        assert pipeline_id in result["cancellations"]
        assert db.get(PipelineRun, pipeline_id).status == PipelineStatus.CANCELLED
        assert db.get(PipelineDependency, dependency_id).continuation_job_id is None
    engine.dispose()


def test_17_stale_worker_generation_during_continuation_is_reported(
    authority_database_url: str,
) -> None:
    engine = create_engine(authority_database_url)
    pipeline_id, _root_id = _seed_pipeline(engine)
    _dependency_id, child_id = _seed_dependency(engine, pipeline_id)
    continuation_id = _complete_and_reconcile(engine, child_id)
    with Session(engine) as db:
        db.add(
            BackgroundWorker(
                worker_id="generation-worker",
                instance_id="new-generation",
                generation=2,
                queues_json=["interactive"],
            )
        )
        continuation = db.get(BackgroundJob, continuation_id)
        _make_running(
            db,
            continuation,
            worker_id="generation-worker",
            instance_id="old-generation",
        )
        db.commit()
        codes = {finding.code for finding in inspect_pipeline_invariants(db)}
        assert "STALE_GENERATION_WITH_APPARENT_AUTHORITY" in codes
    engine.dispose()


def test_18_unresolved_dependency_prevents_pipeline_completion(
    authority_database_url: str,
) -> None:
    engine = create_engine(authority_database_url)
    pipeline_id, _root_id = _seed_pipeline(engine)
    _seed_dependency(engine, pipeline_id)
    with Session(engine) as db:
        pipeline = db.get(PipelineRun, pipeline_id)
        with pytest.raises(PipelineTransitionError, match="BLOCKED_BY_DEPENDENCY"):
            transition_pipeline(
                db,
                pipeline,
                PipelineStatus.COMPLETED,
                actor="pipeline_orchestrator",
            )
        db.rollback()
    engine.dispose()


def test_19_terminal_pipeline_is_not_resumed_by_delayed_child_completion(
    authority_database_url: str,
) -> None:
    engine = create_engine(authority_database_url)
    pipeline_id, _root_id = _seed_pipeline(engine)
    dependency_id, child_id = _seed_dependency(engine, pipeline_id)
    with Session(engine) as db:
        pipeline = db.get(PipelineRun, pipeline_id)
        transition_pipeline(
            db, pipeline, PipelineStatus.CANCELLED, actor="pipeline_orchestrator"
        )
        child = db.get(BackgroundJob, child_id)
        child.status = JobStatus.COMPLETED
        child.completed_at = datetime.now(UTC)
        db.commit()
        reconcile_pipeline_job(db, child_id)
        db.commit()
        assert db.get(PipelineRun, pipeline_id).status == PipelineStatus.CANCELLED
        assert db.get(PipelineDependency, dependency_id).continuation_job_id is None
    engine.dispose()


def test_20_terminal_root_cannot_leave_pipeline_running(
    authority_database_url: str,
) -> None:
    engine = create_engine(authority_database_url)
    pipeline_id, root_id = _seed_pipeline(engine, root_status=JobStatus.RUNNING)
    with Session(engine) as db:
        root = db.get(BackgroundJob, root_id)
        root.status = JobStatus.COMPLETED
        root.completed_at = datetime.now(UTC)
        db.commit()
        reconcile_pipeline_job(db, root_id)
        db.commit()
        pipeline = db.get(PipelineRun, pipeline_id)
        assert pipeline.status == PipelineStatus.FAILED
        assert pipeline.error_message == "PIPELINE_ROOT_TERMINAL_WITH_ACTIVE_PIPELINE"
    engine.dispose()


def test_21_pipeline_item_session_has_explicit_attempt_authority(
    authority_database_url: str,
) -> None:
    engine = create_engine(authority_database_url)
    pipeline_id, root_id = _seed_pipeline(engine, root_status=JobStatus.RUNNING)
    with Session(engine) as db:
        root = db.get(BackgroundJob, root_id)
        pipeline = db.get(PipelineRun, pipeline_id)
        assert root.execution_token
        token = root.execution_token
        run_id = pipeline.upload_run_id

    with Session(engine) as pipeline_db:
        with fence_domain_commits(
            pipeline_db,
            job_id=root_id,
            execution_token=token,
        ):
            with _bounded_item_session(
                pipeline_db,
                execution_job_id=root_id,
                execution_token=token,
            ) as item_db:
                item_db.add(
                    RawCompanyRow(
                        run_id=run_id,
                        row_number=2,
                        ticker="BOUNDARY",
                        raw_json={"ticker": "BOUNDARY"},
                    )
                )
                item_db.commit()

    with Session(engine) as db:
        persisted = db.scalar(
            select(RawCompanyRow).where(
                RawCompanyRow.run_id == run_id,
                RawCompanyRow.ticker == "BOUNDARY",
            )
        )
        assert persisted is not None
    engine.dispose()


def test_22_pipeline_domain_build_does_not_hold_control_row_lock(
    authority_database_url: str,
) -> None:
    engine = create_engine(authority_database_url)
    pipeline_id, root_id = _seed_pipeline(engine, root_status=JobStatus.RUNNING)
    with Session(engine) as db:
        root = db.get(BackgroundJob, root_id)
        pipeline = db.get(PipelineRun, pipeline_id)
        assert root.execution_token
        token = root.execution_token
        run_id = pipeline.upload_run_id

    with Session(engine) as pipeline_db:
        with (
            fence_domain_commits(
                pipeline_db,
                job_id=root_id,
                execution_token=token,
            ),
            deferred_execution_ownership_lock(),
        ):
            assert_current_execution_ownership(
                pipeline_db,
                job_id=root_id,
                execution_token=token,
            )
            with Session(engine) as control_db:
                with control_plane_transaction(control_db):
                    control_db.execute(text("SET LOCAL lock_timeout = '500ms'"))
                    control_job = control_db.get(BackgroundJob, root_id)
                    heartbeat_job(
                        control_db,
                        control_job,
                        execution_token=token,
                    )
            pipeline_db.add(
                RawCompanyRow(
                    run_id=run_id,
                    row_number=2,
                    ticker="DEFERRED",
                    raw_json={"ticker": "DEFERRED"},
                )
            )
            pipeline_db.commit()

    with Session(engine) as db:
        assert db.scalar(
            select(RawCompanyRow).where(
                RawCompanyRow.run_id == run_id,
                RawCompanyRow.ticker == "DEFERRED",
            )
        )
    engine.dispose()


def _seed_pipeline(engine, *, root_status: str = JobStatus.COMPLETED) -> tuple[int, int]:
    from app.services.market_calculation_context_service import create_pipeline_market_context
    from app.services.scope_refresh_adoption import admit_frozen_operation, bind_semantic_authority
    from app.services.work_scope_identity import AcquisitionRequirement, ScopeMember

    with Session(engine) as db:
        run = UploadRun(
            filename=f"authority-{uuid.uuid4().hex}.csv",
            row_count=1,
            status="COMPLETED",
        )
        db.add(run)
        db.flush()
        ticker = f"T{run.id}"
        db.add(
            RawCompanyRow(
                run_id=run.id,
                row_number=1,
                ticker=ticker,
                raw_json={"ticker": ticker},
            )
        )
        pipeline = PipelineRun(
            upload_run_id=run.id,
            status=PipelineStatus.RUNNING,
            current_step="VALIDATING_RUN",
            result_json={},
        )
        db.add(pipeline)
        db.flush()
        db.add(
            PipelineStep(
                pipeline_run_id=pipeline.id,
                step_name="VALIDATING_RUN",
                step_order=1,
                status=PipelineStepStatus.PENDING,
                retry_count=0,
            )
        )
        cutoff = create_pipeline_market_context(db, pipeline, cutoff_at=datetime.now(UTC))
        authority = admit_frozen_operation(
            db,
            operation_kind="full-pipeline-run",
            subject_kind="ticker",
            members=(ScopeMember("TICKER", ticker),),
            cycle_key=f"pipeline-authority-certification:{pipeline.id}",
            business_cutoff=cutoff.cutoff_at,
            provider_source_class="PIPELINE_INPUTS",
            request_type="FULL_PIPELINE",
            requirements=(AcquisitionRequirement("VALIDATING_RUN"),),
            policy_identity="pipeline-authority-certification",
            scope_definition={"upload_run_id": run.id},
            refresh_reason="FULL_PIPELINE_ADMISSION",
        )
        bind_semantic_authority(pipeline, authority)
        root = enqueue_job(
            db,
            "FULL_PIPELINE",
            {
                "pipeline_run_id": pipeline.id,
                "market_calculation_context_id": cutoff.context_id,
                "market_cutoff_at": cutoff.cutoff_at.isoformat(),
                "input_as_of_session": cutoff.latest_completed_session.isoformat(),
                "market_calendar_version": cutoff.calendar_version,
                "bar_readiness_version": cutoff.bar_readiness_version,
                **authority.as_dict(),
            },
            related_run_id=run.id,
        )
        bind_semantic_authority(root, authority)
        root.status = root_status
        if root_status == JobStatus.RUNNING:
            _make_running(db, root, worker_id=f"root-{pipeline.id}")
        elif root_status == JobStatus.COMPLETED:
            root.completed_at = datetime.now(UTC)
        pipeline.result_json = {
            "pipeline_root_job_id": root.id,
            "background_job_id": root.id,
            "market_calculation_context_id": cutoff.context_id,
            "market_cutoff_at": cutoff.cutoff_at.isoformat(),
            "input_as_of_session": cutoff.latest_completed_session.isoformat(),
            "market_calendar_version": cutoff.calendar_version,
            "bar_readiness_version": cutoff.bar_readiness_version,
        }
        db.commit()
        return pipeline.id, root.id


def _seed_dependency(
    engine,
    pipeline_id: int,
    *,
    enqueue: bool = True,
) -> tuple[int, int | None]:
    with Session(engine) as db:
        pipeline = db.get(PipelineRun, pipeline_id)
        dependency = prepare_sec_readiness_dependency(
            db,
            pipeline=pipeline,
            diagnostics={
                "processor": {"active_signature": "certification-signature"},
                "readiness": {
                    "processor_signature": "certification-signature",
                    "requested_tickers": 1,
                    "ready_tickers": 0,
                    "blocking_tickers": [f"T{pipeline.upload_run_id}"],
                },
            },
            resume_from_step="VALIDATING_RUN",
        )
        db.commit()
        if not enqueue:
            return dependency.id, None
        child = enqueue_sec_readiness_dependency(db, dependency_id=dependency.id)
        db.commit()
        return dependency.id, child.id


def _complete_and_reconcile(engine, child_id: int) -> int:
    with Session(engine) as db:
        child = db.get(BackgroundJob, child_id)
        child.status = JobStatus.COMPLETED
        child.completed_at = datetime.now(UTC)
        child.result_json = {"status": "COMPLETED"}
        db.commit()
        reconcile_pipeline_job(db, child_id)
        db.commit()
        dependency = db.get(PipelineDependency, child.pipeline_dependency_id)
        assert dependency.continuation_job_id is not None
        return dependency.continuation_job_id


def _continuations(db: Session, dependency_id: int) -> list[int]:
    return list(
        db.scalars(
            select(BackgroundJob.id)
            .where(
                BackgroundJob.pipeline_dependency_id == dependency_id,
                BackgroundJob.job_type == "FULL_PIPELINE",
            )
            .order_by(BackgroundJob.id)
        )
    )


def _make_running(
    db: Session,
    job: BackgroundJob,
    *,
    worker_id: str,
    instance_id: str = "generation-1",
) -> None:
    now = datetime.now(UTC)
    job.status = JobStatus.RUNNING
    job.worker_id = worker_id
    job.worker_instance_id = instance_id
    job.execution_token = f"token-{job.id}-{uuid.uuid4().hex}"
    job.locked_at = now
    job.heartbeat_at = now
    job.lease_expires_at = now + timedelta(seconds=60)
    db.flush()
