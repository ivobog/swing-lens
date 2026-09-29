from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import psycopg
import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.models.ceri_tables import CeriCompany, CeriSecSyncState
from app.models.tables import (
    BackgroundJob,
    PipelineDependency,
    PipelineRun,
    PipelineStep,
    RawCompanyRow,
    UploadRun,
)
from app.services.background_job_service import (
    JobStatus,
    claim_next_job,
    enqueue_job,
    mark_job_blocked,
    mark_job_completed,
)
from app.services.ceri.sec.processor_lifecycle import certify_processor, promote_processor
from app.services.ceri.sec.processor_signature import sec_guidance_processor_signature
from app.services.ceri.sec.provider import SecCeriProvider
from app.services.ceri.sec.readiness_repair import (
    SEC_READINESS_REPAIR_JOB_TYPE,
    SecReadinessRepairUnresolved,
    execute_sec_readiness_repair,
    schedule_sec_readiness_repair,
)
from app.services.pipeline_service import PipelineStatus, PipelineStepStatus
from app.settings import SecDocumentIncrementalMode, Settings


class _RepairSecClient:
    def __init__(self) -> None:
        self.config = SimpleNamespace()
        self.requests = 0
        self.failures = 0
        self.last_success_at = None
        self.download_calls = 0

    def company_tickers(self):
        self.requests += 1
        return {"0": {"ticker": "TEST", "cik_str": 123456}}

    def submissions(self, _cik):
        self.requests += 1
        return {
            "filings": {
                "recent": {
                    "form": ["8-K"],
                    "accessionNumber": ["0000123456-26-000001"],
                    "primaryDocument": ["test-8k.htm"],
                    "filingDate": ["2026-08-01"],
                }
            }
        }

    def archive_document(self, *_args):
        self.requests += 1
        self.download_calls += 1
        return "The company expects full year revenue guidance of $100 to $110 million."

    def stats(self):
        return SimpleNamespace(
            requests=self.requests,
            filing_document_requests=self.download_calls,
            bytes_downloaded=100 * self.download_calls,
        )


def test_repair_resolves_identity_bootstraps_and_resumes_same_pipeline(
    disposable_postgres_database: str,
    tmp_path,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    signature = sec_guidance_processor_signature()
    settings = Settings(
        _env_file=None,
        cache_dir=tmp_path / "cache",
        sec_document_incremental_mode=SecDocumentIncrementalMode.ACTIVE,
    )
    provider = SecCeriProvider(client=_RepairSecClient())

    with Session(engine) as db:
        certify_processor(
            db,
            processor_signature=signature,
            evidence={"test": True},
            actor="pytest",
        )
        promote_processor(db, processor_signature=signature, actor="pytest")
        run = UploadRun(filename="test.csv", row_count=1, status="COMPLETED")
        db.add(run)
        db.flush()
        db.add(
            RawCompanyRow(
                run_id=run.id,
                row_number=1,
                ticker="TEST",
                raw_json={"ticker": "TEST"},
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
        db.flush()
        root = _freeze_pipeline(db, pipeline)
        pipeline.result_json = {"pipeline_root_job_id": root.id, "background_job_id": root.id}
        repair = schedule_sec_readiness_repair(
            db,
            pipeline=pipeline,
            diagnostics={
                "processor": {
                    "active_signature": signature,
                    "deployed_signature": signature,
                },
                "readiness": {
                    "processor_signature": signature,
                    "requested_tickers": 1,
                    "ready_tickers": 0,
                    "counts": {"UNRESOLVED_MAPPING": 1},
                },
            },
        )
        db.commit()
        _claim_target(db, repair)

        result = execute_sec_readiness_repair(
            db,
            repair,
            settings=settings,
            provider=provider,
        )
        mark_job_completed(db, repair, result, execution_token=repair.execution_token)
        db.commit()

        from app.services.pipeline_dependency_service import reconcile_pipeline_job

        reconcile_pipeline_job(db, repair.id)
        db.commit()

        assert result["status"] == "COMPLETED"
        assert db.get(PipelineRun, pipeline.id).status == PipelineStatus.QUEUED
        assert db.scalar(select(CeriCompany).where(CeriCompany.ticker == "TEST")).cik == (
            "0000123456"
        )
        assert db.scalar(
            select(CeriSecSyncState).where(
                CeriSecSyncState.cik == "0000123456",
                CeriSecSyncState.processor_signature == signature,
            )
        )
        full_jobs = [
            job
            for job in list(
                db.scalars(
                    select(BackgroundJob).where(BackgroundJob.job_type == "FULL_PIPELINE")
                ).all()
            )
            if (job.payload_json or {}).get("resume_from_step")
        ]
        assert len(full_jobs) == 1
        resume_payload = full_jobs[0].payload_json
        assert resume_payload["pipeline_run_id"] == pipeline.id
        assert resume_payload["resume_from_step"] == "VALIDATING_RUN"
        assert resume_payload["market_calculation_context_id"] > 0
        assert resume_payload["market_cutoff_at"].endswith("Z")
        assert resume_payload["input_as_of_session"]
        assert resume_payload["market_calendar_version"]
        assert resume_payload["bar_readiness_version"]
        assert provider.client.download_calls == 1

        reconcile_pipeline_job(db, repair.id)
        db.commit()
        assert db.get(PipelineDependency, repair.pipeline_dependency_id).continuation_job_id == (
            full_jobs[0].id
        )
        assert (
            db.scalar(
                select(BackgroundJob).where(BackgroundJob.job_type == SEC_READINESS_REPAIR_JOB_TYPE)
            ).id
            == repair.id
        )
        assert (
            len(
                list(
                    db.scalars(
                        select(BackgroundJob)
                        .where(BackgroundJob.job_type == "FULL_PIPELINE")
                        .where(BackgroundJob.workflow_key.is_not(None))
                    ).all()
                )
            )
            == 1
        )
        assert provider.client.download_calls == 1
    engine.dispose()


class _PartiallyAmbiguousSecClient(_RepairSecClient):
    def company_tickers(self):
        self.requests += 1
        return {
            "0": {"ticker": "GOOD", "cik_str": 123456},
            "1": {"ticker": "BAD", "cik_str": 111111},
            "2": {"ticker": "BAD", "cik_str": 222222},
        }


def test_full_pipeline_sec_repair_handoff_does_not_self_block(
    disposable_postgres_database: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reproduce the run-188/189 transaction topology on real PostgreSQL.

    The pipeline validation transaction inserts the automatic SEC repair child,
    retaining a key-share lock on the running root through the lineage foreign
    key.  Its next lease heartbeat uses the worker's real control-session path.
    A leaked domain-fence context must not make that control commit request a
    conflicting ``FOR UPDATE`` lock on the root job.
    """

    import app.services.background_worker as background_worker_service
    import app.services.pipeline_executor as pipeline_executor_service
    import app.settings as app_settings
    from app.services.background_worker import _execute_full_pipeline_job, run_worker_once
    from app.services.ceri.sec.readiness_diagnostics import diagnose_sec_readiness
    from app.services.domain_write_fence import current_domain_write_ownership

    incident_settings = Settings(_env_file=None).model_copy(
        update={
            "ceri_enabled": True,
            "ceri_provider_ingest_enabled": True,
            "ceri_run_capture_enabled": True,
        }
    )
    monkeypatch.setattr(app_settings, "get_settings", lambda: incident_settings)
    monkeypatch.setattr(background_worker_service, "get_settings", lambda: incident_settings)
    monkeypatch.setattr(pipeline_executor_service, "get_settings", lambda: incident_settings)

    _upgrade(disposable_postgres_database)
    engine = create_engine(
        disposable_postgres_database,
        connect_args={
            "options": (
                "-c lock_timeout=1000ms -c statement_timeout=5000ms "
                "-c application_name=swinglens-pipeline-authority-reproducer"
            )
        },
    )
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    observed_sql: list[dict[str, object]] = []
    blocked_edges: list[dict[str, object]] = []
    stop_observer = threading.Event()
    main_backend_pid: dict[str, int] = {}
    child_insert_backend_pid: dict[str, int] = {}

    @event.listens_for(engine, "before_cursor_execute")
    def record_fence_sql(conn, _cursor, statement, _parameters, _context, _executemany):
        normalized = " ".join(str(statement).split())
        targets_jobs = "background_jobs" in normalized.lower()
        is_fence = targets_jobs and "FOR UPDATE" in normalized.upper()
        is_child_insert = targets_jobs and normalized.upper().startswith(
            "INSERT INTO BACKGROUND_JOBS"
        )
        if not is_fence and not is_child_insert:
            return
        ownership = current_domain_write_ownership()
        backend_pid = conn.connection.driver_connection.info.backend_pid
        if is_child_insert and ownership is not None:
            child_insert_backend_pid["value"] = backend_pid
        observed_sql.append(
            {
                "backend_pid": backend_pid,
                "kind": "child_insert" if is_child_insert else "ownership_fence",
                "statement": normalized,
                "ownership_job_id": ownership.job_id if ownership else None,
                "ownership_token_present": bool(ownership and ownership.execution_token),
            }
        )

    def observe_blocking() -> None:
        observer_url = disposable_postgres_database.replace(
            "postgresql+psycopg://", "postgresql://", 1
        )
        with psycopg.connect(observer_url, autocommit=True) as observer:
            while not stop_observer.wait(0.01):
                rows = observer.execute(
                    """
                    SELECT pid, state, wait_event_type, wait_event,
                           pg_blocking_pids(pid) AS blockers,
                           xact_start, query_start, query
                    FROM pg_stat_activity
                    WHERE datname = current_database()
                      AND wait_event_type = 'Lock'
                      AND cardinality(pg_blocking_pids(pid)) > 0
                    """
                ).fetchall()
                for row in rows:
                    edge = {
                        "waiter_pid": row[0],
                        "state": row[1],
                        "wait_event_type": row[2],
                        "wait_event": row[3],
                        "blocker_pids": list(row[4]),
                        "xact_start": row[5].isoformat() if row[5] else None,
                        "query_start": row[6].isoformat() if row[6] else None,
                        "query": " ".join(row[7].split()),
                    }
                    if edge not in blocked_edges:
                        blocked_edges.append(edge)

    observer = threading.Thread(target=observe_blocking, daemon=True)
    observer.start()
    try:
        with sessions() as db:
            signature = sec_guidance_processor_signature()
            certify_processor(
                db, processor_signature=signature, evidence={"test": True}, actor="pytest"
            )
            promote_processor(db, processor_signature=signature, actor="pytest")
            run = UploadRun(
                filename="pipeline-authority-reproducer.csv", row_count=1, status="COMPLETED"
            )
            db.add(run)
            db.flush()
            db.add(
                RawCompanyRow(
                    run_id=run.id,
                    row_number=1,
                    ticker="TEST",
                    raw_json={"ticker": "TEST"},
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
            db.flush()
            root = _freeze_pipeline(db, pipeline)
            root.status = JobStatus.QUEUED
            db.commit()
            root_id, pipeline_id = root.id, pipeline.id
            diagnostics = diagnose_sec_readiness(
                db, tickers=["TEST"], processor_signature=signature
            )
            assert not diagnostics.complete

        def validate(db, job):
            main_backend_pid["value"] = db.scalar(text("SELECT pg_backend_pid()"))
            return _execute_full_pipeline_job(db, job)

        started = time.monotonic()
        assert run_worker_once(
            worker_id="pytest-pipeline-authority-worker",
            worker_instance_id="pytest-pipeline-authority-instance",
            queues=("interactive", "broker", "background"),
            stale_after_seconds=900,
            heartbeat_timeout_seconds=30,
            session_factory=sessions,
            handlers={"FULL_PIPELINE": validate},
        )
        elapsed = time.monotonic() - started

        with sessions() as verify:
            retained_root = verify.get(BackgroundJob, root_id)
            retained_pipeline = verify.get(PipelineRun, pipeline_id)
            repair_jobs = list(
                verify.scalars(
                    select(BackgroundJob).where(
                        BackgroundJob.job_type == SEC_READINESS_REPAIR_JOB_TYPE,
                        BackgroundJob.parent_job_id == root_id,
                    )
                )
            )
            dependencies = list(
                verify.scalars(
                    select(PipelineDependency).where(
                        PipelineDependency.pipeline_run_id == pipeline_id
                    )
                )
            )
            report = {
                "elapsed_seconds": round(elapsed, 3),
                "handler_entry_backend_pid": main_backend_pid.get("value"),
                "child_insert_backend_pid": child_insert_backend_pid.get("value"),
                "root_job_id": root_id,
                "root_status": str(retained_root.status),
                "pipeline_id": pipeline_id,
                "pipeline_status": str(retained_pipeline.status),
                "repair_job_ids": [job.id for job in repair_jobs],
                "dependency_ids": [dependency.id for dependency in dependencies],
                "blocked_edges": blocked_edges,
                "fence_sql": observed_sql,
            }
            print("PIPELINE_AUTHORITY_REPRODUCER=" + repr(report))

            if retained_root.status != JobStatus.COMPLETED:
                assert blocked_edges, report
                blocked = next(
                    edge
                    for edge in blocked_edges
                    if "FOR UPDATE" in str(edge["query"]).upper()
                    and "background_jobs" in str(edge["query"]).lower()
                )
                assert child_insert_backend_pid["value"] in blocked["blocker_pids"], report
                pytest.fail(
                    "full-pipeline SEC repair handoff self-blocked its control commit: "
                    + repr(report)
                )

            assert elapsed < 5
            assert retained_pipeline.status == PipelineStatus.WAITING_DEPENDENCY
            assert len(repair_jobs) == 1
            assert len(dependencies) == 1
            assert dependencies[0].state == "QUEUED"
            assert dependencies[0].child_job_id == repair_jobs[0].id
            assert repair_jobs[0].pipeline_dependency_id == dependencies[0].id
            assert not blocked_edges
    finally:
        stop_observer.set()
        observer.join(timeout=2)
        event.remove(engine, "before_cursor_execute", record_fence_sql)
        engine.dispose()


def test_ambiguous_identity_does_not_stop_other_safe_repairs(
    disposable_postgres_database: str,
    tmp_path,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    signature = sec_guidance_processor_signature()
    settings = Settings(
        _env_file=None,
        cache_dir=tmp_path / "cache",
        sec_document_incremental_mode=SecDocumentIncrementalMode.ACTIVE,
    )
    provider = SecCeriProvider(client=_PartiallyAmbiguousSecClient())

    with Session(engine) as db:
        certify_processor(
            db,
            processor_signature=signature,
            evidence={"test": True},
            actor="pytest",
        )
        promote_processor(db, processor_signature=signature, actor="pytest")
        run = UploadRun(filename="ambiguous.csv", row_count=2, status="COMPLETED")
        db.add(run)
        db.flush()
        db.add_all(
            [
                RawCompanyRow(
                    run_id=run.id,
                    row_number=index,
                    ticker=ticker,
                    raw_json={"ticker": ticker},
                )
                for index, ticker in enumerate(("BAD", "GOOD"), start=1)
            ]
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
        root = _freeze_pipeline(db, pipeline)
        pipeline.result_json = {"pipeline_root_job_id": root.id, "background_job_id": root.id}
        repair = schedule_sec_readiness_repair(
            db,
            pipeline=pipeline,
            diagnostics={
                "processor": {"active_signature": signature},
                "readiness": {
                    "processor_signature": signature,
                    "requested_tickers": 2,
                    "ready_tickers": 0,
                    "counts": {"UNRESOLVED_MAPPING": 2},
                },
            },
        )
        db.commit()
        _claim_target(db, repair)

        with pytest.raises(SecReadinessRepairUnresolved, match="BAD") as blocked_error:
            execute_sec_readiness_repair(
                db,
                repair,
                settings=settings,
                provider=provider,
            )
        mark_job_blocked(
            db,
            repair,
            blocked_error.value,
            reason_code=blocked_error.value.reason_code,
            diagnostics=blocked_error.value.diagnostics,
            execution_token=repair.execution_token,
        )
        db.commit()
        from app.services.pipeline_dependency_service import reconcile_pipeline_job

        reconcile_pipeline_job(db, repair.id)
        db.commit()

        good = db.scalar(select(CeriCompany).where(CeriCompany.ticker == "GOOD"))
        assert good is not None and good.cik == "0000123456"
        assert db.scalar(select(CeriCompany).where(CeriCompany.ticker == "BAD")) is None
        assert db.scalar(
            select(CeriSecSyncState).where(
                CeriSecSyncState.cik == "0000123456",
                CeriSecSyncState.processor_signature == signature,
            )
        )
        blocked = db.get(PipelineRun, pipeline.id)
        assert blocked.status == PipelineStatus.BLOCKED
        dependency = db.get(PipelineDependency, repair.pipeline_dependency_id)
        assert dependency.state == "FAILED"
        unresolved = (repair.result_json or {})["diagnostics"]["unresolved_tickers"]
        assert set(unresolved) == {"BAD"}
        assert provider.client.download_calls == 1
        assert not list(
            db.scalars(
                select(BackgroundJob).where(
                    BackgroundJob.job_type == "FULL_PIPELINE",
                    BackgroundJob.workflow_key.is_not(None),
                )
            ).all()
        )
    engine.dispose()


def _freeze_pipeline(db, pipeline):
    from datetime import UTC, datetime

    from app.services.market_calculation_context_service import create_pipeline_market_context
    from app.services.scope_refresh_adoption import (
        admit_frozen_operation,
        bind_semantic_authority,
    )
    from app.services.work_scope_identity import AcquisitionRequirement, ScopeMember

    cutoff = create_pipeline_market_context(db, pipeline, cutoff_at=datetime.now(UTC))
    tickers = tuple(
        str(value).strip().upper()
        for value in db.scalars(
            select(RawCompanyRow.ticker)
            .where(RawCompanyRow.run_id == pipeline.upload_run_id)
            .order_by(RawCompanyRow.row_number)
        )
        if str(value).strip()
    )
    authority = admit_frozen_operation(
        db,
        operation_kind="full-pipeline-run",
        subject_kind="ticker",
        members=tuple(ScopeMember("TICKER", ticker) for ticker in tickers),
        cycle_key=f"pipeline-market-context:{cutoff.context_id}",
        business_cutoff=cutoff.cutoff_at,
        provider_source_class="PIPELINE_INPUTS",
        request_type="FULL_PIPELINE",
        requirements=(AcquisitionRequirement("VALIDATING_RUN"),),
        policy_identity="pytest-sec-readiness-repair",
        scope_definition={
            "upload_run_id": pipeline.upload_run_id,
            "market_calculation_context_id": cutoff.context_id,
        },
        refresh_reason="FULL_PIPELINE_ADMISSION",
    )
    bind_semantic_authority(pipeline, authority)
    payload = {
        "pipeline_run_id": pipeline.id,
        "market_calculation_context_id": cutoff.context_id,
        "market_cutoff_at": cutoff.cutoff_at.isoformat(),
        "input_as_of_session": cutoff.latest_completed_session.isoformat(),
        "market_calendar_version": cutoff.calendar_version,
        "bar_readiness_version": cutoff.bar_readiness_version,
        **authority.as_dict(),
    }
    root = enqueue_job(db, "FULL_PIPELINE", payload, related_run_id=pipeline.upload_run_id)
    bind_semantic_authority(root, authority)
    root.status = JobStatus.COMPLETED
    pipeline.result_json = {
        **(pipeline.result_json or {}),
        "pipeline_root_job_id": root.id,
        "background_job_id": root.id,
    }
    db.flush()
    return root


def _claim_target(db, job):
    """Acquire the real retained attempt used by native durable writers."""
    from app.models.tables import BackgroundWorker

    if db.get(BackgroundWorker, "pytest-repair-worker") is None:
        db.add(
            BackgroundWorker(
                worker_id="pytest-repair-worker",
                instance_id="pytest-repair-instance",
                generation=1,
                queues_json=["interactive", "broker", "background"],
            )
        )
    job.status = JobStatus.QUEUED
    job.priority = 0
    db.commit()
    claimed = claim_next_job(
        db, "pytest-repair-worker", queues=("interactive", "broker", "background")
    )
    assert claimed is not None and claimed.id == job.id
    db.commit()
    return claimed


def _upgrade(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def incident_db(disposable_postgres_database):
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        signature = sec_guidance_processor_signature()
        certify_processor(
            db, processor_signature=signature, evidence={"test": True}, actor="pytest"
        )
        promote_processor(db, processor_signature=signature, actor="pytest")
        run = UploadRun(filename="incident.csv", row_count=1, status="COMPLETED")
        db.add(run)
        db.flush()
        db.add(
            RawCompanyRow(run_id=run.id, row_number=1, ticker="TEST", raw_json={"ticker": "TEST"})
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
        db.commit()
        yield db, pipeline, signature
        db.rollback()
    engine.dispose()


def _schedule_incident(db, pipeline, signature):
    from app.services.background_worker import execute_job
    from app.services.ceri.sec.readiness_diagnostics import diagnose_sec_readiness
    from app.services.pipeline_executor import PipelineExecutionDependencies, execute_full_pipeline

    root = _freeze_pipeline(db, pipeline)
    db.commit()
    _claim_target(db, root)
    diagnostics = diagnose_sec_readiness(db, tickers=["TEST"], processor_signature=signature)
    assert not diagnostics.complete

    def validate(db, job):
        return execute_full_pipeline(
            db,
            job.payload_json["pipeline_run_id"],
            dependencies=PipelineExecutionDependencies(ceri_provider_ingest_enabled=True),
        )

    result = execute_job(db, root, {"FULL_PIPELINE": validate})
    assert result.status == PipelineStatus.WAITING_DEPENDENCY
    repair = db.get(BackgroundJob, pipeline.result_json["repair_job_id"])
    db.commit()
    return root, repair


def _configuration_probe(db, job):
    from app.services.ceri.config import load_ceri_config
    from app.services.configuration_delivery import current_delivery
    from app.services.core_effective_configuration import resolve_fundamental_configuration
    from app.settings import get_settings

    return (
        current_delivery().anchor,
        resolve_fundamental_configuration().snapshot.semantic_hash,
        load_ceri_config().config_hash,
        get_settings().technical_v5_enabled,
    )


def test_worker_repair_continuation_restart_and_c2_drift_keep_c1(
    incident_db, monkeypatch, tmp_path
):
    from app.services.background_worker import execute_job
    from app.services.configuration_delivery import ANCHOR_KEY, binding_reference
    from app.settings import get_settings

    db, pipeline, signature = incident_db
    root, repair = _schedule_incident(db, pipeline, signature)
    c1 = execute_job(db, root, {"FULL_PIPELINE": _configuration_probe})
    assert repair.payload_json[ANCHOR_KEY] == c1[0]
    assert repair.parent_job_id == root.id
    client = _RepairSecClient()
    original_download = client.archive_document
    c2 = get_settings().model_copy(update={"technical_v5_enabled": not c1[3]})

    def download_and_drift(*args):
        document = original_download(*args)
        monkeypatch.setattr("app.settings._get_current_settings", lambda: c2)
        monkeypatch.setattr(
            "app.services.configuration_delivery.resolve_pipeline_configurations",
            lambda *_a, **_k: pytest.fail("current C2 resolved during continuation"),
        )
        return document

    client.archive_document = download_and_drift
    provider = SecCeriProvider(client=client)
    settings = Settings(_env_file=None, cache_dir=tmp_path / "cache")

    def repair_handler(db, job):
        assert _configuration_probe(db, job) == c1
        return execute_sec_readiness_repair(db, job, settings=settings, provider=provider)

    _claim_target(db, repair)
    result = execute_job(db, repair, {SEC_READINESS_REPAIR_JOB_TYPE: repair_handler})
    mark_job_completed(db, repair, result, execution_token=repair.execution_token)
    db.commit()
    from app.services.pipeline_dependency_service import reconcile_pipeline_job

    reconcile_pipeline_job(db, repair.id)
    db.commit()
    dependency = db.get(PipelineDependency, repair.pipeline_dependency_id)
    continuation_id = dependency.continuation_job_id
    assert client.download_calls == 1
    assert get_settings().technical_v5_enabled != c1[3]
    # A fresh Python worker has neither the parent's delivery nor its settings cache.
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    probe = tmp_path / "restart_probe.py"
    probe.write_text(
        """import json, sys
from sqlalchemy.engine import make_url
from app.database_safety import assert_disposable_database
from app.db import SessionLocal
from app.models.tables import BackgroundJob
from app.services.background_worker import execute_job
import app.services.configuration_delivery as delivery
from app.services.ceri.config import load_ceri_config
from app.services.core_effective_configuration import resolve_fundamental_configuration
from app.settings import get_settings
assert_disposable_database(get_settings().database_url,
    active_database_url=make_url(get_settings().database_url).set(database="postgres"))
def forbidden(*args, **kwargs):
    raise AssertionError("Restart resolved current configuration")
delivery.resolve_pipeline_configurations = forbidden
def handler(db, job):
    return (delivery.current_delivery().anchor,
        resolve_fundamental_configuration().snapshot.semantic_hash,
        load_ceri_config().config_hash, get_settings().technical_v5_enabled)
with SessionLocal() as db:
    job = db.get(BackgroundJob, int(sys.argv[1]))
    print("RESTART_C1=" + json.dumps(execute_job(db, job, {"FULL_PIPELINE": handler})))
""",
        encoding="utf-8",
    )
    repository = Path(__file__).resolve().parents[2]
    environment = {
        **os.environ,
        "DATABASE_URL": db.get_bind().url.render_as_string(hide_password=False),
        "PYTHONPATH": str(repository),
        "DB_MONITOR_ENABLED": "false",
        "JOB_WORKER_ENABLED": "false",
        "PROCESS_ROLE": "CLI_OR_MAINTENANCE",
        "TECHNICAL_V5_ENABLED": str(not c1[3]).lower(),
    }
    restarted_worker = subprocess.run(
        [sys.executable, str(probe), str(continuation_id)],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert restarted_worker.returncode == 0, restarted_worker.stderr
    retained = next(
        line.removeprefix("RESTART_C1=")
        for line in restarted_worker.stdout.splitlines()
        if line.startswith("RESTART_C1=")
    )
    assert json.loads(retained) == list(c1)
    with Session(db.get_bind()) as restarted:
        continuation = restarted.get(BackgroundJob, continuation_id)
        assert continuation.parent_job_id == repair.id
        assert binding_reference(restarted, job_id=continuation.id) == c1[0]
        assert execute_job(restarted, continuation, {"FULL_PIPELINE": _configuration_probe}) == c1
        from app.models.tables import CoreCalculationEvidence
        from app.services.configuration_delivery import delivered_configuration
        from app.services.core_effective_configuration import CoreEffectiveConfiguration
        from app.services.effective_configuration import CONFIGURATION_PAYLOAD_KEY
        from app.services.fundamental_score_service import recalculate_run_fundamentals
        from app.services.market_calculation_context_service import market_context_for_pipeline

        def remaining_fundamentals(db, job):
            execution = db.get(PipelineRun, job.payload_json["pipeline_run_id"])
            return recalculate_run_fundamentals(
                db,
                execution.upload_run_id,
                market_cutoff=market_context_for_pipeline(db, execution),
                pipeline_run_id=execution.id,
                effective_configuration=CoreEffectiveConfiguration(
                    delivered_configuration("core.fundamental").snapshot
                ),
            )

        _claim_target(restarted, continuation)
        scores = execute_job(restarted, continuation, {"FULL_PIPELINE": remaining_fundamentals})
        assert len(scores) == 1 and scores[0].evidence_id is not None
        evidence = restarted.get(CoreCalculationEvidence, scores[0].evidence_id)
        assert evidence.payload_json[CONFIGURATION_PAYLOAD_KEY]["semantic_hash"] == c1[1]
        continuation.status = JobStatus.COMPLETED
        restarted.commit()
    db.expire_all()
    pipeline_status_before = db.get(PipelineRun, pipeline.id).status
    # Recovery of already-ready SEC evidence must not download or process it again.
    reconcile_pipeline_job(db, repair.id)
    db.commit()
    assert db.get(PipelineDependency, repair.pipeline_dependency_id).continuation_job_id == (
        continuation_id
    )
    assert client.download_calls == 1
    assert db.get(PipelineRun, pipeline.id).status == pipeline_status_before
    continuations = list(
        db.scalars(
            select(BackgroundJob).where(
                BackgroundJob.workflow_key == f"pipeline:{pipeline.id}:dependency-continuation"
            )
        )
    )
    assert len(continuations) == 1


def test_missing_authoritative_binding_is_terminal_and_visible(incident_db):
    from app.services.background_job_service import mark_job_failed_or_retry
    from app.services.background_worker import execute_job
    from app.services.pipeline_service import get_pipeline_status

    db, pipeline, signature = incident_db
    # A genuinely missing registry binding; no protected row is deleted to simulate it.
    repair = BackgroundJob(
        job_type=SEC_READINESS_REPAIR_JOB_TYPE,
        related_run_id=pipeline.upload_run_id,
        status=JobStatus.RUNNING,
        retry_count=0,
        max_retries=5,
        payload_json={"pipeline_run_id": pipeline.id, "resume_from_step": "VALIDATING_RUN"},
    )
    db.add(repair)
    db.flush()
    pipeline.status = PipelineStatus.PREPARING
    pipeline.result_json = {
        "repair_job_id": repair.id,
        "sec_repair": {"ready_tickers": 1, "total_tickers": 1},
    }
    db.commit()
    with pytest.raises(ValueError, match="MISSING_CONFIGURATION_ANCHOR_BINDING") as caught:
        execute_job(
            db, repair, {SEC_READINESS_REPAIR_JOB_TYPE: lambda *_a: pytest.fail("handler ran")}
        )
    db.rollback()
    mark_job_failed_or_retry(db, repair, caught.value)
    db.commit()
    assert repair.status == JobStatus.FAILED
    assert repair.operational_metadata_json["failure_classification"]["retryable"] is False
    status = get_pipeline_status(db, pipeline.id)
    assert status.status == PipelineStatus.BLOCKED
    assert status.steps[0].status == PipelineStepStatus.BLOCKED
    assert "MISSING_CONFIGURATION_ANCHOR_BINDING" in status.steps[0].error_message
    assert "continuation failed" in status.steps[0].message
    assert str(repair.id) in status.steps[0].error_message


@pytest.mark.parametrize("wrong", ["anchor", "pipeline", "run"])
def test_wrong_execution_identity_rejected_before_sec_work(incident_db, wrong):
    from app.services.background_worker import execute_job
    from app.services.configuration_delivery import ANCHOR_KEY

    db, pipeline, signature = incident_db
    root, repair = _schedule_incident(db, pipeline, signature)
    if wrong == "anchor":
        repair.payload_json = {
            **repair.payload_json,
            ANCHOR_KEY: {"anchor_id": "b" * 64, "fingerprint": "c" * 64},
        }
    elif wrong == "pipeline":
        other = PipelineRun(upload_run_id=pipeline.upload_run_id, status="PENDING")
        db.add(other)
        db.flush()
        _freeze_pipeline(db, other)  # Same C1 does not authorize another execution identity.
        repair.payload_json = {**repair.payload_json, "pipeline_run_id": other.id}
    else:
        other = UploadRun(filename="other.csv", status="COMPLETED")
        db.add(other)
        db.flush()
        repair.related_run_id = other.id
    db.commit()
    with pytest.raises(
        ValueError, match="CONFIGURATION_(ANCHOR_PARENT|EXECUTION_LINEAGE)_MISMATCH"
    ) as caught:
        execute_job(db, repair, {SEC_READINESS_REPAIR_JOB_TYPE: lambda *_a: pytest.fail("SEC ran")})
    from app.services.background_job_service import mark_job_failed_or_retry
    from app.services.pipeline_service import get_pipeline_status

    db.rollback()
    repair.status = JobStatus.RUNNING
    db.flush()
    mark_job_failed_or_retry(db, repair, caught.value)
    db.commit()
    from app.services.pipeline_dependency_service import reconcile_pipeline_job

    reconcile_pipeline_job(db, repair.id)
    db.commit()
    assert get_pipeline_status(db, pipeline.id).status == "FAILED"
    assert not list(
        db.scalars(
            select(BackgroundJob).where(
                BackgroundJob.workflow_key == f"pipeline:{pipeline.id}:sec-continuation"
            )
        )
    )


def test_legacy_unbound_helper_resolves_pipeline_without_backfill(incident_db):
    from app.models.tables import ExecutionConfigurationBinding
    from app.services.background_worker import execute_job
    from app.services.configuration_delivery import binding_reference

    db, pipeline, signature = incident_db
    root = _freeze_pipeline(db, pipeline)
    repair = BackgroundJob(
        job_type=SEC_READINESS_REPAIR_JOB_TYPE,
        status="QUEUED",
        related_run_id=pipeline.upload_run_id,
        parent_job_id=root.id,
        payload_json={"pipeline_run_id": pipeline.id},
    )
    db.add(repair)
    db.commit()
    observed = execute_job(db, repair, {SEC_READINESS_REPAIR_JOB_TYPE: _configuration_probe})
    assert observed[0] == binding_reference(db, pipeline_run_id=pipeline.id)
    assert db.get(ExecutionConfigurationBinding, f"job:{repair.id}") is None


def test_recovered_lease_owner_cannot_publish_continuation(incident_db, tmp_path):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from app.services.background_job_service import JobLeaseLost, fence_job_execution

    db, pipeline, signature = incident_db
    root, repair = _schedule_incident(db, pipeline, signature)
    repair.status = JobStatus.RUNNING
    repair.execution_token = "old-owner"
    repair.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
    db.commit()
    repair._execution_token = "old-owner"
    with Session(db.get_bind()) as recovered:
        recovered.execute(
            update(BackgroundJob)
            .where(BackgroundJob.id == repair.id)
            .values(execution_token="new-owner")
        )
        recovered.commit()
    with pytest.raises(JobLeaseLost):
        fence_job_execution(db, repair)
    db.rollback()
    provider = SecCeriProvider(client=_RepairSecClient())
    with pytest.raises(JobLeaseLost):
        execute_sec_readiness_repair(
            db,
            repair,
            provider=provider,
            settings=Settings(_env_file=None, cache_dir=tmp_path / "cache"),
        )
    db.rollback()
    assert provider.client.requests == 0
    assert not list(
        db.scalars(
            select(BackgroundJob).where(
                BackgroundJob.workflow_key == f"pipeline:{pipeline.id}:sec-continuation"
            )
        )
    )


def test_preupgrade_terminal_helper_visible_and_normal_resume_keeps_c1(incident_db):
    from app.services.configuration_delivery import binding_reference
    from app.services.pipeline_service import get_pipeline_status, resume_pipeline

    db, pipeline, signature = incident_db
    root, repair = _schedule_incident(db, pipeline, signature)
    c1 = binding_reference(db, pipeline_run_id=pipeline.id)
    repair.status = JobStatus.FAILED
    repair.error_message = "MISSING_CONFIGURATION_ANCHOR_BINDING"
    pipeline.result_json = {
        **pipeline.result_json,
        "sec_repair": {
            **pipeline.result_json["sec_repair"],
            "ready_tickers": 1,
            "total_tickers": 1,
        },
    }
    db.commit()
    from app.services.pipeline_dependency_service import reconcile_pipeline_job

    reconcile_pipeline_job(db, repair.id)
    db.commit()
    status = get_pipeline_status(db, pipeline.id)
    assert status.status == PipelineStatus.FAILED
    assert "MISSING_CONFIGURATION_ANCHOR_BINDING" in status.steps[0].error_message
    assert pipeline.status == PipelineStatus.FAILED
    resume_pipeline(db, pipeline.id)
    db.commit()
    assert pipeline.status == PipelineStatus.QUEUED
    resumed = db.get(BackgroundJob, pipeline.result_json["background_job_id"])
    assert resumed.payload_json["resume_from_step"] == "VALIDATING_RUN"
    assert binding_reference(db, job_id=resumed.id) == c1
    with pytest.raises(ValueError, match="Only BLOCKED"):
        resume_pipeline(db, pipeline.id)


def test_wrong_retained_helper_binding_is_rejected(incident_db, monkeypatch):
    from app.models.tables import ExecutionConfigurationBinding
    from app.services.background_worker import execute_job
    from app.services.configuration_delivery import binding_reference
    from app.settings import get_settings

    db, pipeline, signature = incident_db
    root, repair = _schedule_incident(db, pipeline, signature)
    c1 = binding_reference(db, pipeline_run_id=pipeline.id)
    changed = get_settings().model_copy(
        update={"technical_v5_enabled": not get_settings().technical_v5_enabled}
    )
    monkeypatch.setattr("app.settings._get_current_settings", lambda: changed)
    other = PipelineRun(upload_run_id=pipeline.upload_run_id, status="PENDING")
    db.add(other)
    db.flush()
    _freeze_pipeline(db, other)
    c2 = binding_reference(db, pipeline_run_id=other.id)
    assert c1 != c2
    db.add(
        ExecutionConfigurationBinding(
            binding_key=f"job:{repair.id}", job_id=repair.id, anchor_id=c2["anchor_id"]
        )
    )
    db.commit()
    with pytest.raises(ValueError, match="CONFIGURATION_ANCHOR_PARENT_MISMATCH"):
        execute_job(db, repair, {SEC_READINESS_REPAIR_JOB_TYPE: lambda *_a: pytest.fail("SEC ran")})


def test_concurrent_sec_completion_creates_one_continuation(incident_db):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from app.observability.correlation import worker_job_scope
    from app.services.pipeline_service import enqueue_pipeline_after_sec_repair

    db, pipeline, signature = incident_db
    root, repair = _schedule_incident(db, pipeline, signature)
    pipeline_id, repair_id = pipeline.id, repair.id
    barrier = Barrier(2)
    engine = db.get_bind()
    db.rollback()

    def complete():
        with Session(engine) as concurrent:
            execution = concurrent.get(PipelineRun, pipeline_id)
            parent = concurrent.get(BackgroundJob, repair_id)
            barrier.wait(timeout=10)
            with worker_job_scope(parent):
                continuation = enqueue_pipeline_after_sec_repair(
                    concurrent, execution, processor_signature=signature
                )
            concurrent.commit()
            return continuation.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(complete) for _ in range(2)]
        ids = [future.result(timeout=30) for future in futures]
    assert ids[0] == ids[1]
    assert (
        len(
            list(
                db.scalars(
                    select(BackgroundJob).where(
                        BackgroundJob.workflow_key == f"pipeline:{pipeline_id}:sec-continuation"
                    )
                )
            )
        )
        == 1
    )


def test_wrong_continuation_anchor_is_terminal_and_visible_before_handler(incident_db):
    from app.services.background_job_service import mark_job_failed_or_retry
    from app.services.background_worker import execute_job
    from app.services.configuration_delivery import ANCHOR_KEY
    from app.services.pipeline_service import enqueue_pipeline_after_sec_repair, get_pipeline_status

    db, pipeline, signature = incident_db
    root, repair = _schedule_incident(db, pipeline, signature)
    continuation = enqueue_pipeline_after_sec_repair(db, pipeline, processor_signature=signature)
    continuation.payload_json = {
        **continuation.payload_json,
        ANCHOR_KEY: {"anchor_id": "b" * 64, "fingerprint": "c" * 64},
    }
    continuation.status = JobStatus.RUNNING
    db.commit()
    with pytest.raises(ValueError, match="CONFIGURATION_ANCHOR_PARENT_MISMATCH") as caught:
        execute_job(db, continuation, {"FULL_PIPELINE": lambda *_a: pytest.fail("handler ran")})
    db.rollback()
    mark_job_failed_or_retry(db, continuation, caught.value)
    db.commit()
    assert continuation.status == JobStatus.FAILED
    assert continuation.operational_metadata_json["failure_classification"]["retryable"] is False
    status = get_pipeline_status(db, pipeline.id)
    assert status.status == PipelineStatus.BLOCKED
    assert status.steps[0].status == PipelineStepStatus.BLOCKED
    assert "CONFIGURATION_ANCHOR_PARENT_MISMATCH" in status.steps[0].error_message
    assert "continuation failed" in status.message
