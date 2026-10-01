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
from app.services.background_worker import execute_job
from app.services.domain_write_fence import (
    assert_current_execution_ownership,
    bounded_domain_session,
    control_plane_transaction,
    deferred_execution_ownership_lock,
    fence_domain_commits,
)
from app.services.pipeline_dependency_service import (
    enqueue_sec_readiness_dependency,
    prepare_ceri_workflow_dependency,
    prepare_sec_readiness_dependency,
    reconcile_pending_dependency_enqueues,
    reconcile_pipeline_job,
    reconcile_safe_pipeline_invariants,
)
from app.services.pipeline_executor import (
    PipelineContinuationSuppressed,
    _claim_pipeline_continuation_resume,
    _mark_pipeline_running,
)
from app.services.pipeline_invariant_service import inspect_pipeline_invariants
from app.services.pipeline_service import (
    PipelineCancellationContended,
    PipelineStatus,
    PipelineStepStatus,
    cancel_pipeline,
)
from app.services.pipeline_state_machine import PipelineTransitionError, transition_pipeline
from app.services.runtime_certification_observer import observe_certification_runtime
from app.settings import ProcessRole, RuntimeMode, Settings


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


def test_07b_restart_reconciles_completed_child_handoff_gap(
    authority_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(authority_database_url)
    pipeline_id, root_id = _seed_pipeline(engine)
    dependency_id, child_id = _seed_dependency(engine, pipeline_id)
    with Session(engine) as db:
        root = db.get(BackgroundJob, root_id)
        child = db.get(BackgroundJob, child_id)
        root.payload_json = {
            **(root.payload_json or {}),
            "certification_authorized": True,
            "transition_preflight_plan_id": 1,
            "certification_session_id": "cert-session-current",
        }
        child.payload_json = {
            **(child.payload_json or {}),
            "certification_authorized": True,
            "certification_session_id": "cert-session-current",
        }
        child.root_correlation_id = root.root_correlation_id
        child.status = JobStatus.COMPLETED
        child.completed_at = datetime.now(UTC)
        child.execution_token = None
        child.worker_id = None
        child.worker_instance_id = None
        db.commit()

        observation = observe_certification_runtime(
            db,
            pipeline_id=pipeline_id,
            control_heartbeat_seconds=30,
        )
        assert "PARENT_WAITING_WITHOUT_RECOVERABLE_CHILD" not in {
            failure.code for failure in observation.failures
        }

        monkeypatch.setattr(
            "app.services.background_job_service.get_settings",
            lambda: Settings(
                _env_file=None,
                runtime_mode=RuntimeMode.CERTIFICATION,
                process_role=ProcessRole.DURABLE_WORKER,
                use_durable_pipeline=True,
                durable_worker_process_enabled=True,
                runtime_instance_id="cert-session-current",
            ),
        )
        result = reconcile_safe_pipeline_invariants(db)
        db.commit()
        dependency = db.get(PipelineDependency, dependency_id)
        assert dependency.state == "COMPLETED"
        assert dependency.continuation_job_id in result["continuations_enqueued"]
        assert _continuations(db, dependency_id) == [dependency.continuation_job_id]
        continuation = db.get(BackgroundJob, dependency.continuation_job_id)
        assert continuation.root_correlation_id == root.root_correlation_id
        assert continuation.parent_job_id == child.id
        assert continuation.payload_json["certification_authorized"] is True
        assert continuation.payload_json["certification_session_id"] == "cert-session-current"
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
                select(BackgroundJob.id).where(BackgroundJob.id == root_id).with_for_update()
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
        step = db.scalar(select(PipelineStep).where(PipelineStep.pipeline_run_id == pipeline_id))
        assert step.status == "FAILED"
        assert step.error_message == "bounded failure"
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
        transition_pipeline(db, pipeline, PipelineStatus.CANCELLED, actor="pipeline_orchestrator")
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
            with bounded_domain_session(
                pipeline_db,
                job_id=root_id,
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


def test_23_completed_root_can_handoff_to_durable_ceri_dependency(
    authority_database_url: str,
) -> None:
    engine = create_engine(authority_database_url)
    pipeline_id, root_id = _seed_pipeline(engine, root_status=JobStatus.RUNNING)
    with Session(engine) as db:
        pipeline = db.get(PipelineRun, pipeline_id)
        dependency = prepare_ceri_workflow_dependency(
            db,
            pipeline=pipeline,
            workflow_key=f"ceri:pipeline:{pipeline.upload_run_id}:authority-test",
            resume_from_step="FREEZING_DECISION_HANDOFF_MANIFEST",
        )
        pipeline.status = PipelineStatus.WAITING_FOR_CERI_COMPLETION
        pipeline.current_step = "CERI_PROVIDER_INGEST"
        root = db.get(BackgroundJob, root_id)
        root.status = JobStatus.COMPLETED
        root.completed_at = datetime.now(UTC)
        db.commit()
        reconcile_pipeline_job(db, root_id)
        db.commit()

        assert db.get(PipelineRun, pipeline_id).status == PipelineStatus.WAITING_FOR_CERI_COMPLETION
        assert db.get(PipelineDependency, dependency.id).state == "RUNNING"
        findings = inspect_pipeline_invariants(db)
        assert not any(
            finding.code == "ACTIVE_PIPELINE_WITH_TERMINAL_ROOT_JOB"
            and finding.pipeline_id == pipeline_id
            for finding in findings
        )
    engine.dispose()


def test_24_generic_durable_handler_does_not_self_block_detached_heartbeat(
    authority_database_url: str,
) -> None:
    """D6 regression: any durable handler defers its ownership lock to commit."""

    engine = create_engine(authority_database_url)
    pipeline_id, root_id = _seed_pipeline(engine, root_status=JobStatus.RUNNING)
    with Session(engine) as db:
        root = db.get(BackgroundJob, root_id)
        pipeline = db.get(PipelineRun, pipeline_id)
        root.job_type = "AUTHORITY_DEFERRED_PROBE"
        db.commit()
        token = root.execution_token
        run_id = pipeline.upload_run_id

    def handler(handler_db: Session, handled_job: BackgroundJob) -> dict:
        assert_current_execution_ownership(
            handler_db,
            job_id=handled_job.id,
            execution_token=token,
        )
        with Session(engine) as control_db:
            with control_plane_transaction(control_db):
                control_db.execute(text("SET LOCAL lock_timeout = '500ms'"))
                control_job = control_db.get(BackgroundJob, handled_job.id)
                heartbeat_job(control_db, control_job, execution_token=token)
        handler_db.add(
            RawCompanyRow(
                run_id=run_id,
                row_number=2,
                ticker="GENERIC-DEFERRED",
                raw_json={"ticker": "GENERIC-DEFERRED"},
            )
        )
        handler_db.commit()
        return {"status": "ok"}

    with Session(engine) as db:
        job = db.get(BackgroundJob, root_id)
        result = execute_job(
            db,
            job,
            handlers={"AUTHORITY_DEFERRED_PROBE": handler},
            execution_token=token,
        )
        assert result == {"status": "ok"}

    with Session(engine) as db:
        assert db.scalar(
            select(RawCompanyRow).where(
                RawCompanyRow.run_id == run_id,
                RawCompanyRow.ticker == "GENERIC-DEFERRED",
            )
        )
    engine.dispose()


def test_25_cancel_during_pending_enqueue_converges_without_child(
    authority_database_url: str,
) -> None:
    engine = create_engine(authority_database_url)
    pipeline_id, _root_id = _seed_pipeline(engine)
    dependency_id, child_id = _seed_dependency(engine, pipeline_id, enqueue=False)
    assert child_id is None

    with Session(engine) as db:
        cancelled = cancel_pipeline(db, pipeline_id)
        db.commit()
        repeated = cancel_pipeline(db, pipeline_id)
        db.commit()
        created = reconcile_pending_dependency_enqueues(db)
        db.commit()

        dependency = db.get(PipelineDependency, dependency_id)
        assert cancelled.status == PipelineStatus.CANCELLED
        assert repeated.status == PipelineStatus.CANCELLED
        assert dependency.state == "CANCELLED"
        assert dependency.child_job_id is None
        assert created == ()
        assert not any(
            finding.pipeline_id == pipeline_id for finding in inspect_pipeline_invariants(db)
        )
    engine.dispose()


def test_26_cancel_observes_terminal_winner_idempotently(
    authority_database_url: str,
) -> None:
    engine = create_engine(authority_database_url)
    pipeline_id, _root_id = _seed_pipeline(engine)
    terminal_locked = threading.Event()
    release_terminal = threading.Event()
    outcomes: list[str] = []
    errors: list[Exception] = []

    def terminal_winner() -> None:
        try:
            with Session(engine) as db:
                pipeline = db.scalar(
                    select(PipelineRun).where(PipelineRun.id == pipeline_id).with_for_update()
                )
                terminal_locked.set()
                release_terminal.wait(timeout=10)
                pipeline.status = PipelineStatus.FAILED
                pipeline.completed_at = datetime.now(UTC)
                pipeline.error_message = "authoritative terminal winner"
                db.commit()
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    def cancellation_observer() -> None:
        try:
            assert terminal_locked.wait(timeout=5)
            with Session(engine) as db:
                observed = cancel_pipeline(db, pipeline_id)
                db.commit()
                outcomes.append(observed.status)
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    terminal_thread = threading.Thread(target=terminal_winner)
    cancel_thread = threading.Thread(target=cancellation_observer)
    terminal_thread.start()
    cancel_thread.start()
    assert terminal_locked.wait(timeout=5)
    time.sleep(0.1)
    release_terminal.set()
    terminal_thread.join(timeout=10)
    cancel_thread.join(timeout=10)

    with Session(engine) as db:
        pipeline = db.get(PipelineRun, pipeline_id)
        assert errors == []
        assert outcomes == [PipelineStatus.FAILED]
        assert pipeline.status == PipelineStatus.FAILED
        assert pipeline.error_message == "authoritative terminal winner"
    engine.dispose()


def test_27_completed_root_does_not_override_cancel_requested(
    authority_database_url: str,
) -> None:
    engine = create_engine(authority_database_url)
    pipeline_id, root_id = _seed_pipeline(engine)
    with Session(engine) as db:
        pipeline = db.get(PipelineRun, pipeline_id)
        pipeline.status = PipelineStatus.CANCEL_REQUESTED
        db.commit()

        reconcile_pipeline_job(db, root_id)
        db.commit()
        assert db.get(PipelineRun, pipeline_id).status == PipelineStatus.CANCEL_REQUESTED

        result = reconcile_safe_pipeline_invariants(db)
        db.commit()
        assert pipeline_id in result["cancellations"]
        assert db.get(PipelineRun, pipeline_id).status == PipelineStatus.CANCELLED
    engine.dispose()


@pytest.mark.parametrize(
    "winner",
    (
        PipelineStatus.CANCEL_REQUESTED,
        PipelineStatus.CANCELLED,
        PipelineStatus.FAILED,
        PipelineStatus.COMPLETED,
    ),
)
def test_28_continuation_claim_suppresses_cancel_or_terminal_winner(
    authority_database_url: str,
    winner: str,
) -> None:
    engine = create_engine(authority_database_url)
    pipeline_id, _root_id = _seed_pipeline(engine)
    _dependency_id, child_id = _seed_dependency(engine, pipeline_id)
    assert child_id is not None
    _complete_and_reconcile(engine, child_id)

    with Session(engine) as db:
        pipeline = db.get(PipelineRun, pipeline_id)
        pipeline.status = winner
        if winner != PipelineStatus.CANCEL_REQUESTED:
            pipeline.completed_at = datetime.now(UTC)
        db.commit()

    with Session(engine) as restarted, pytest.raises(PipelineContinuationSuppressed):
        pipeline = restarted.get(PipelineRun, pipeline_id)
        _claim_pipeline_continuation_resume(restarted, pipeline)

    with Session(engine) as db:
        expected = PipelineStatus.CANCELLED if winner == PipelineStatus.CANCEL_REQUESTED else winner
        assert db.get(PipelineRun, pipeline_id).status == expected
    engine.dispose()


def test_29_continuation_claim_and_running_transition_hold_one_authority_lock(
    authority_database_url: str,
) -> None:
    engine = create_engine(authority_database_url)
    pipeline_id, _root_id = _seed_pipeline(engine)
    _dependency_id, child_id = _seed_dependency(engine, pipeline_id)
    assert child_id is not None
    _complete_and_reconcile(engine, child_id)
    claim_held = threading.Event()
    release_claim = threading.Event()
    cancelled: list[str] = []
    errors: list[Exception] = []

    def resume() -> None:
        try:
            with Session(engine) as db:
                pipeline = db.get(PipelineRun, pipeline_id)
                pipeline = _claim_pipeline_continuation_resume(db, pipeline)
                claim_held.set()
                release_claim.wait(timeout=10)
                _mark_pipeline_running(db, pipeline)
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    def cancel() -> None:
        try:
            assert claim_held.wait(timeout=5)
            with Session(engine) as db:
                result = cancel_pipeline(db, pipeline_id)
                db.commit()
                cancelled.append(result.status)
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    resume_thread = threading.Thread(target=resume)
    cancel_thread = threading.Thread(target=cancel)
    resume_thread.start()
    cancel_thread.start()
    assert claim_held.wait(timeout=5)
    time.sleep(0.1)
    release_claim.set()
    resume_thread.join(timeout=10)
    cancel_thread.join(timeout=10)

    with Session(engine) as db:
        assert errors == []
        assert cancelled == [PipelineStatus.CANCELLED]
        assert db.get(PipelineRun, pipeline_id).status == PipelineStatus.CANCELLED
        assert not any(
            finding.code == "TERMINAL_PIPELINE_WITH_LIVE_CONTINUATION"
            and finding.pipeline_id == pipeline_id
            for finding in inspect_pipeline_invariants(db)
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
