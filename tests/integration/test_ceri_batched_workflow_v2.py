from __future__ import annotations

import json
import os
import socket
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import Barrier
from time import perf_counter

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.database_safety import run_guarded_alembic_upgrade
from app.models.ceri_tables import (
    CeriAlertEvent,
    CeriChangeEvent,
    CeriCompany,
    CeriDerivedFeature,
    CeriEstimateSnapshot,
    CeriFeatureBuildState,
    CeriFeatureSourceManifest,
    CeriIngestionRun,
    CeriIngestionRunSourceRecord,
    CeriRevisionFeature,
    CeriScoreSnapshot,
    CeriSourceRecord,
)
from app.models.tables import (
    BackgroundJob,
    MarketCalculationContext,
    PipelineRun,
    PipelineStep,
    RawCompanyRow,
    UploadRun,
)
from app.services.background_job_service import (
    JobStatus,
    claim_next_job,
    enqueue_job,
    mark_job_completed,
    record_job_progress,
)
from app.services.background_worker import persist_detached_job_control
from app.services.ceri.batched_job_handlers import (
    execute_feature_batch_job,
    execute_normalize_batch_job,
    execute_provider_ingest_batch_job,
    execute_run_finalize_job,
)
from app.services.ceri.batched_workflow import (
    CERI_FEATURE_BATCH,
    CERI_NORMALIZE_BATCH,
    CERI_PROVIDER_INGEST_BATCH,
    CERI_RUN_FINALIZE,
)
from app.services.ceri.constants import CERI_PIPELINE_PROVIDER_INGEST_STEP
from app.services.ceri.feature_rebuild_service import (
    CeriFeatureRebuildRequest,
    CeriFeatureRebuildService,
)
from app.services.ceri.job_handlers import (
    CERI_ALERT_REBUILD,
    CERI_CAPTURE_RUN,
    CERI_CHANGE_DETECTION,
    CERI_REBUILD_FEATURES,
    execute_alert_rebuild_job,
    execute_capture_run_job,
    execute_change_detection_job,
    execute_normalize_job,
    execute_rebuild_features_job,
)
from app.services.domain_write_fence import ControlPlaneLockTimeout, control_plane_transaction
from app.services.market_calculation_context_service import (
    CeriContextIntegrityError,
    create_pipeline_market_context,
    freeze_or_validate_pipeline_ceri_authority,
    resolve_pipeline_ceri_context,
)
from app.services.pipeline_service import (
    FULL_PIPELINE_JOB_TYPE,
    PipelineStepStatus,
    _finalize_ceri_async_timing,
    _roll_up_ceri_pipeline_failure,
)
from app.services.scope_refresh_adoption import (
    LegacySemanticAuthorityError,
    admit_frozen_operation,
    bind_semantic_authority,
    require_semantic_authority,
)
from app.services.transition_preflight_plan_service import (
    _resolve_decision_ceri_context,
    _validate_handoff_temporal_lineage,
)
from app.services.work_scope_identity import AcquisitionRequirement, ScopeMember
from app.services.worker_registry import register_worker
from app.settings import Settings


class _FixedDate(date):
    @classmethod
    def today(cls) -> date:
        return cls(2026, 8, 12)


def test_concurrent_finalizers_create_one_capture_in_postgresql(
    disposable_postgres_database: str,
    monkeypatch,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    workflow_key = "ceri:pipeline:95:config-a"
    with Session(engine) as db:
        db.add_all(
            [
                _new_job(
                    db,
                    job_type=CERI_FEATURE_BATCH,
                    workflow_key=workflow_key,
                    request_key=f"{workflow_key}:feature:{index}",
                    related_run_id=95,
                    status=JobStatus.COMPLETED,
                    priority=130,
                    payload_json={},
                    max_retries=3,
                )
                for index in (1, 2)
            ]
        )
        finalizer = _new_job(
            db,
            job_type=CERI_RUN_FINALIZE,
            workflow_key=workflow_key,
            request_key=f"{workflow_key}:finalize",
            related_run_id=95,
            status=JobStatus.RUNNING,
            priority=140,
            payload_json={
                "workflow_key": workflow_key,
                "run_id": 95,
                "expected_feature_batches": 2,
            },
            max_retries=3,
        )
        db.add(finalizer)
        db.commit()
        finalizer_id = finalizer.id

    monkeypatch.setattr(
        "app.services.ceri.batched_job_handlers.ceri_flags",
        lambda: type("Flags", (), {"enabled": True})(),
    )
    barrier = Barrier(2)

    def finalize() -> int | None:
        with Session(engine) as db:
            job = db.get(BackgroundJob, finalizer_id)
            barrier.wait(timeout=10)
            result = execute_run_finalize_job(db, job)
            db.commit()
            return result["capture_job_id"]

    with ThreadPoolExecutor(max_workers=2) as executor:
        capture_ids = list(executor.map(lambda _index: finalize(), range(2)))

    with Session(engine) as db:
        capture_count = db.scalar(
            select(func.count(BackgroundJob.id)).where(
                BackgroundJob.workflow_key == workflow_key,
                BackgroundJob.job_type == "CERI_CAPTURE_RUN",
            )
        )
        capture = db.scalar(
            select(BackgroundJob).where(
                BackgroundJob.workflow_key == workflow_key,
                BackgroundJob.job_type == "CERI_CAPTURE_RUN",
            )
        )
    engine.dispose()

    assert capture_count == 1
    assert len(set(capture_ids)) == 1
    assert capture.request_key == f"{workflow_key}:capture"


def test_run9_two_ticker_detached_checkpoint_does_not_self_lock_postgresql(
    disposable_postgres_database: str,
    monkeypatch,
) -> None:
    """Reproduce Run 9's exact second-ticker transaction relationship."""

    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(
        "app.services.ceri.batched_job_handlers.ceri_flags",
        lambda: type("Flags", (), {"provider_ingest": True})(),
    )

    with Session(engine) as db:
        run = UploadRun(filename="run9-two-ticker-regression.csv", row_count=2, status="COMPLETED")
        db.add(run)
        db.flush()
        authority = admit_frozen_operation(
            db,
            operation_kind="full-pipeline-run",
            subject_kind="ticker",
            members=(ScopeMember("TICKER", "AAPL"), ScopeMember("TICKER", "ACLS")),
            cycle_key=f"run9-regression:{run.id}",
            business_cutoff=date(2026, 9, 30),
            provider_source_class="CERI",
            request_type="PROVIDER_INGEST",
            requirements=(AcquisitionRequirement("CERI_SOURCE_RECORDS"),),
            policy_identity="run9-control-plane-regression-v1",
            scope_definition={"run_id": run.id, "tickers": ["AAPL", "ACLS"]},
        )
        pipeline = PipelineRun(upload_run_id=run.id, status="RUNNING", result_json={})
        bind_semantic_authority(pipeline, authority)
        db.add(pipeline)
        db.flush()
        workflow_key = f"ceri:pipeline:{pipeline.id}:run9-regression"
        job = enqueue_job(
            db,
            CERI_PROVIDER_INGEST_BATCH,
            {
                "workflow_key": workflow_key,
                "pipeline_run_id": pipeline.id,
                "run_id": run.id,
                "provider": "eodhd",
                "dataset": "estimates",
                "tickers": ["AAPL", "ACLS"],
                "checkpoint_interval": 1,
            },
            request_key=f"{workflow_key}:provider:eodhd:estimates:0001",
            workflow_key=workflow_key,
            related_run_id=run.id,
            priority=80,
            max_retries=3,
        )
        bind_semantic_authority(job, authority)
        worker = register_worker(
            db,
            worker_id="run9-regression-worker",
            queues=("background",),
            heartbeat_timeout_seconds=30,
            hostname=socket.gethostname(),
            process_id=os.getpid(),
        )
        db.commit()
        run_id = int(run.id)
        claimed = claim_next_job(
            db,
            worker_id=worker.worker_id,
            worker_instance_id=worker.instance_id,
            queues=("background",),
            lease_seconds=30,
        )
        assert claimed is not None and claimed.id == job.id
        db.commit()
        job_id = int(claimed.id)
        execution_token = str(claimed.execution_token)
        control_before = worker.control_loop_heartbeat_at

    class PersistingProviderService:
        def ingest(self, db, request, *, should_cancel):
            row_number = 1 if request.ticker == "AAPL" else 2
            db.add(
                RawCompanyRow(
                    run_id=run_id,
                    row_number=row_number,
                    ticker=request.ticker,
                    company_name=request.ticker,
                    sector="Technology",
                    raw_json={"ticker": request.ticker, "certification": "run9-regression"},
                )
            )
            # On ticker two this flush reproduced Run 9 by autoflushing the
            # dirty BackgroundJob checkpoint before the detached callback.
            db.flush()
            assert should_cancel() is False
            return type(
                "Result",
                (),
                {"as_dict": lambda self: {"status": "COMPLETED", "failed": 0}},
            )()

    with Session(engine) as db:
        claimed = db.get(BackgroundJob, job_id)

        def detached(**progress):
            return persist_detached_job_control(
                session_factory=factory,
                job_id=job_id,
                execution_token=execution_token,
                worker_id="run9-regression-worker",
                worker_instance_id=str(claimed.worker_instance_id),
                lease_seconds=30,
                progress=progress,
            )

        claimed._control_plane_progress = detached
        result = execute_provider_ingest_batch_job(
            db,
            claimed,
            ingestion_service=PersistingProviderService(),
        )
        mark_job_completed(db, claimed, result, execution_token=execution_token)
        db.commit()

    with Session(engine) as db:
        completed = db.get(BackgroundJob, job_id)
        worker = db.get(type(worker), "run9-regression-worker")
        blocked = db.scalar(
            text(
                """
                select count(*) from pg_stat_activity
                where datname = current_database()
                  and pid <> pg_backend_pid()
                  and cardinality(pg_blocking_pids(pid)) > 0
                  and query ilike '%background_jobs%'
                """
            )
        )
        persisted_tickers = set(
            db.scalars(select(RawCompanyRow.ticker).where(RawCompanyRow.run_id == run_id))
        )
        assert completed.status == JobStatus.COMPLETED
        assert completed.progress_processed == 2
        assert completed.progress_total == 2
        assert completed.progress_sequence >= 2
        assert completed.heartbeat_at is None
        assert completed.lease_expires_at is None
        assert completed.operational_metadata_json["ceri_batch"]["completed_tickers"] == [
            "AAPL",
            "ACLS",
        ]
        assert worker.control_loop_heartbeat_at >= control_before
        assert blocked == 0
        assert persisted_tickers == {"AAPL", "ACLS"}
    engine.dispose()


def test_detached_control_write_has_bounded_postgresql_lock_timeout(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as setup:
        job = enqueue_job(
            setup,
            "CERI_PROVIDER_INGEST_BATCH",
            {"tickers": ["AAPL", "ACLS"]},
            request_key="control-lock-timeout-regression",
        )
        setup.flush()
        job.status = JobStatus.RUNNING
        job.execution_token = "control-lock-timeout-token"
        setup.commit()
        job_id = int(job.id)

    with Session(engine) as locker:
        locker.execute(select(BackgroundJob.id).where(BackgroundJob.id == job_id).with_for_update())
        started = perf_counter()
        with Session(engine) as control:
            with pytest.raises(ControlPlaneLockTimeout, match="exceeded"):
                with control_plane_transaction(control, lock_timeout_seconds=0.2):
                    record_job_progress(
                        control,
                        job_id=job_id,
                        execution_token="control-lock-timeout-token",
                        stage="CERI_PROVIDER_INGEST",
                        processed=1,
                        total=2,
                        checkpoint_version="locked-checkpoint",
                        operational_metadata_patch={"ceri_batch": {"processed": 1}},
                    )
        elapsed = perf_counter() - started
        locker.rollback()

    with Session(engine) as verify:
        unchanged = verify.get(BackgroundJob, job_id)
        assert unchanged.progress_sequence == 0
        assert unchanged.operational_metadata_json == {}
    engine.dispose()
    assert elapsed < 2.0


def test_legacy_enqueue_remains_available_while_live_migration_waits(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database, revision="0034_slse_dashboard_indexes")
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        job = enqueue_job(
            db,
            "FULL_PIPELINE",
            {"pipeline_run_id": 1},
            request_key="full-pipeline:pre-migration-probe",
        )
        db.commit()
        job_id = job.id
    with engine.connect() as connection:
        persisted = connection.exec_driver_sql(
            "select job_type, request_key from background_jobs where id = %s",
            (job_id,),
        ).one()
    engine.dispose()

    assert persisted == (
        "FULL_PIPELINE",
        "full-pipeline:pre-migration-probe",
    )


def test_batched_workflow_outputs_match_legacy_workflow_in_postgresql(
    disposable_postgres_database_factory: Callable[[], AbstractContextManager[str]],
    monkeypatch,
) -> None:
    enabled_settings = Settings(
        _env_file=None,
        ceri_enabled=True,
        ceri_provider_ingest_enabled=True,
        ceri_run_capture_enabled=True,
        ceri_alerts_enabled=True,
        ceri_legacy_pipeline_scheduling_enabled=False,
        ceri_batched_workflow_enabled=True,
    )
    monkeypatch.setattr(
        "app.services.ceri.feature_flags.get_settings",
        lambda: enabled_settings,
    )
    monkeypatch.setattr("app.settings.get_settings", lambda: enabled_settings)
    fixed_now = datetime(2026, 8, 12, 16, 30, tzinfo=UTC)
    monkeypatch.setattr("app.services.ceri.capture_service._utcnow", lambda: fixed_now)
    monkeypatch.setattr("app.services.ceri.feature_rebuild_service.date", _FixedDate)

    with disposable_postgres_database_factory() as legacy_url:
        with disposable_postgres_database_factory() as batched_url:
            _upgrade(legacy_url)
            _upgrade(batched_url)
            legacy = _execute_legacy_fixture(legacy_url)
            batched = _execute_batched_fixture(batched_url)

    assert batched == legacy
    assert len(batched["source_records"]) == 4
    assert len(batched["normalized"]) == 4
    assert len(batched["features"]) >= 3
    assert len(batched["snapshots"]) == 1
    # A first snapshot establishes a baseline; it is never an upgrade or alert.
    assert len(batched["changes"]) == 0
    assert len(batched["alerts"]) == 0


def test_pipeline_ceri_zero_history_freezes_post_acquisition_context_and_scores(
    disposable_postgres_database: str,
    monkeypatch,
) -> None:
    """Fresh-run provider evidence is frozen after normalization, before scoring."""

    _upgrade(disposable_postgres_database)
    enabled_settings = Settings(
        _env_file=None,
        ceri_enabled=True,
        ceri_provider_ingest_enabled=True,
        ceri_run_capture_enabled=True,
        ceri_alerts_enabled=True,
        ceri_legacy_pipeline_scheduling_enabled=False,
        ceri_batched_workflow_enabled=True,
    )
    monkeypatch.setattr(
        "app.services.ceri.feature_flags.get_settings",
        lambda: enabled_settings,
    )
    monkeypatch.setattr("app.settings.get_settings", lambda: enabled_settings)
    engine = create_engine(disposable_postgres_database)

    with Session(engine) as db:
        run_id = _seed_zero_history_run(db)
        authority = _fixture_authority(db, run_id=run_id, cycle_key="zero-history")
        pipeline = PipelineRun(upload_run_id=run_id, status="RUNNING", result_json={})
        bind_semantic_authority(pipeline, authority)
        db.add(pipeline)
        db.flush()
        main_context = create_pipeline_market_context(
            db,
            pipeline,
            cutoff_at=datetime(2026, 8, 12, 11, tzinfo=UTC),
        )
        main_context_id = main_context.context_id
        main_cutoff_at = main_context.cutoff_at
        pipeline.result_json = {
            "market_calculation_context_id": main_context.context_id,
            "market_cutoff_at": main_context.cutoff_at.isoformat(),
            "input_as_of_session": main_context.latest_completed_session.isoformat(),
            "market_calendar_version": main_context.calendar_version,
            "bar_readiness_version": main_context.bar_readiness_version,
        }
        workflow_key = f"ceri:pipeline:{pipeline.id}:zero-history"
        pipeline.status = "WAITING_FOR_CERI_COMPLETION"
        pipeline.current_step = CERI_PIPELINE_PROVIDER_INGEST_STEP
        pipeline.result_json = {
            **pipeline.result_json,
            "ceri_provider_workflow_key": workflow_key,
        }
        db.add(
            PipelineStep(
                pipeline_run_id=pipeline.id,
                step_name=CERI_PIPELINE_PROVIDER_INGEST_STEP,
                step_order=9,
                status=PipelineStepStatus.WAITING_DEPENDENCY,
                started_at=datetime.now(UTC),
                result_json={
                    "async_timing": {
                        "dispatch_duration_ms": 1.0,
                        "dependency_wait_started_at": datetime.now(UTC).isoformat(),
                    }
                },
            )
        )
        assert db.scalar(select(func.count()).select_from(CeriEstimateSnapshot)) == 0

        provider = _new_job(
            db,
            job_type=CERI_PROVIDER_INGEST_BATCH,
            workflow_key=workflow_key,
            request_key=f"{workflow_key}:provider:eodhd:estimates:0001",
            related_run_id=run_id,
            status=JobStatus.RUNNING,
            priority=80,
            payload_json={
                "workflow_key": workflow_key,
                "provider": "eodhd",
                "dataset": "estimates",
                "tickers": ["MSFT"],
                "run_id": run_id,
                "pipeline_run_id": pipeline.id,
                "checkpoint_interval": 1,
            },
            max_retries=3,
        )
        normalize = _new_job(
            db,
            job_type=CERI_NORMALIZE_BATCH,
            workflow_key=workflow_key,
            request_key=f"{workflow_key}:normalize:eodhd:estimates:0001",
            related_run_id=run_id,
            status=JobStatus.RUNNING,
            priority=81,
            payload_json={
                "workflow_key": workflow_key,
                "provider": "eodhd",
                "dataset": "estimates",
                "tickers": ["MSFT"],
                "run_id": run_id,
                "pipeline_run_id": pipeline.id,
                "checkpoint_interval": 1,
            },
            max_retries=3,
        )
        bind_semantic_authority(provider, authority)
        bind_semantic_authority(normalize, authority)
        db.add_all([provider, normalize])
        db.commit()

        class SeededProviderService:
            def ingest(self, current_db, request, *, should_cancel):
                _seed_current_run_estimates(
                    current_db,
                    run_id=run_id,
                    request_key=request.request_key,
                    commit=False,
                )
                return type(
                    "Result",
                    (),
                    {
                        "as_dict": lambda self: {
                            "status": "COMPLETED",
                            "inserted": 4,
                            "failed": 0,
                        }
                    },
                )()

        ingested = _execute_handler(
            db,
            provider,
            lambda current_db, current_job: execute_provider_ingest_batch_job(
                current_db,
                current_job,
                ingestion_service=SeededProviderService(),
            ),
        )
        assert ingested["processed_tickers"] == 1
        assert ingested["failed"] == 0
        normalized = _execute_handler(db, normalize, execute_normalize_batch_job)
        assert normalized["normalized"] == 4
        known_at = max(db.scalars(select(CeriEstimateSnapshot.known_at)))
        assert known_at > main_cutoff_at

        features = []
        for batch_index in (1, 2):
            feature = _new_job(
                db,
                job_type=CERI_FEATURE_BATCH,
                workflow_key=workflow_key,
                request_key=f"{workflow_key}:feature:{batch_index:04d}",
                related_run_id=run_id,
                status=JobStatus.RUNNING,
                priority=130,
                payload_json={
                    "workflow_key": workflow_key,
                    "pipeline_run_id": pipeline.id,
                    "tickers": ["MSFT"],
                    "run_id": run_id,
                    "batch_index": batch_index,
                    "expected_normalization_batches": 1,
                    "checkpoint_interval": 1,
                },
                max_retries=3,
            )
            bind_semantic_authority(feature, authority)
            db.add(feature)
            features.append(feature)
        db.commit()
        rebuilt = [_execute_handler(db, feature, execute_feature_batch_job) for feature in features]
        assert rebuilt[0]["features"] > 0
        for feature in features:
            db.refresh(feature)
        ceri_context_id = int(features[0].payload_json["calculation_context_id"])
        assert all(
            int(feature.payload_json["calculation_context_id"]) == ceri_context_id
            for feature in features
        )
        ceri_cutoff_at = datetime.fromisoformat(features[0].payload_json["cutoff_at"])
        assert ceri_context_id != main_context_id
        assert ceri_cutoff_at >= known_at

        finalizer = _new_job(
            db,
            job_type=CERI_RUN_FINALIZE,
            workflow_key=workflow_key,
            request_key=f"{workflow_key}:finalize",
            related_run_id=run_id,
            status=JobStatus.RUNNING,
            priority=140,
            payload_json={
                "workflow_key": workflow_key,
                "pipeline_run_id": pipeline.id,
                "run_id": run_id,
                "expected_feature_batches": 2,
            },
            max_retries=3,
        )
        bind_semantic_authority(finalizer, authority)
        db.add(finalizer)
        db.commit()
        _execute_handler(db, finalizer, execute_run_finalize_job)
        capture = db.scalar(
            select(BackgroundJob).where(
                BackgroundJob.job_type == CERI_CAPTURE_RUN,
                BackgroundJob.workflow_key == workflow_key,
            )
        )
        captured = _execute_handler(db, capture, execute_capture_run_job)
        assert captured["score_snapshots"] == 1
        change = db.get(BackgroundJob, int(captured["change_job_id"]))
        changed = _execute_handler(db, change, execute_change_detection_job)
        alert = db.get(BackgroundJob, int(changed["alert_job_id"]))
        _execute_handler(db, alert, execute_alert_rebuild_job)
        continuation = db.scalar(
            select(BackgroundJob).where(
                BackgroundJob.job_type == FULL_PIPELINE_JOB_TYPE,
                BackgroundJob.parent_job_id == alert.id,
            )
        )
        assert continuation is not None
        assert continuation.payload_json["resume_from_step"] == "FREEZING_DECISION_HANDOFF_MANIFEST"

        snapshot = db.scalar(select(CeriScoreSnapshot).where(CeriScoreSnapshot.run_id == run_id))
        db.refresh(pipeline)
        assert snapshot is not None
        assert snapshot.calculation_context_id == ceri_context_id
        assert pipeline.result_json["market_calculation_context_id"] == main_context_id
        assert pipeline.result_json["market_cutoff_at"] == main_cutoff_at.isoformat()
        assert pipeline.result_json["ceri_calculation_context_id"] == ceri_context_id
        assert pipeline.result_json["ceri_completion_state"] == "CERTIFIED"
        assert pipeline.result_json["ceri_continuation_job_id"] == continuation.id
        retained_ceri_context = resolve_pipeline_ceri_context(
            db,
            calculation_context_id=ceri_context_id,
            upload_run_id=run_id,
            pipeline_run_id=pipeline.id,
        )
        decision_ceri_context = _resolve_decision_ceri_context(
            db,
            pipeline_run_id=pipeline.id,
            upload_run_id=run_id,
            market_cutoff=main_context,
        )
        assert decision_ceri_context.context_id == retained_ceri_context.context_id
        _validate_handoff_temporal_lineage(
            db,
            upload_run_id=run_id,
            market_cutoff=main_context,
            ceri_market_cutoff=retained_ceri_context,
            built_rows=(),
        )

    engine.dispose()


def test_ceri_authority_crash_rolls_back_context_and_all_payload_references(
    disposable_postgres_database: str,
    monkeypatch,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        pipeline, features = _seed_ceri_authority_fixture(db, suffix="crash")
        pipeline_id = int(pipeline.id)
        workflow_key = str(features[0].workflow_key)
        import app.services.market_calculation_context_service as context_service

        original = context_service.create_or_get_pipeline_ceri_context

        def crash_after_context(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("simulated process termination before authority commit")

        monkeypatch.setattr(
            context_service,
            "create_or_get_pipeline_ceri_context",
            crash_after_context,
        )
        with pytest.raises(RuntimeError, match="simulated process termination"):
            freeze_or_validate_pipeline_ceri_authority(
                db,
                job=features[0],
                payload=dict(features[0].payload_json or {}),
            )
        db.rollback()

    with Session(engine) as verification:
        retained_pipeline = verification.get(PipelineRun, pipeline_id)
        retained_features = list(
            verification.scalars(
                select(BackgroundJob)
                .where(BackgroundJob.workflow_key == workflow_key)
                .order_by(BackgroundJob.id)
            )
        )
        assert "ceri_calculation_context_id" not in retained_pipeline.result_json
        assert all("calculation_context_id" not in row.payload_json for row in retained_features)
        assert (
            verification.scalar(
                select(func.count()).select_from(context_service.MarketCalculationContext)
            )
            == 0
        )
    engine.dispose()


def test_ceri_authority_commit_is_durable_and_shared_by_all_feature_batches(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        pipeline, features = _seed_ceri_authority_fixture(db, suffix="shared")
        pipeline_id = int(pipeline.id)
        workflow_key = str(features[0].workflow_key)
        frozen = freeze_or_validate_pipeline_ceri_authority(
            db,
            job=features[0],
            payload=dict(features[0].payload_json or {}),
        )
        db.commit()
        context_id = int(frozen["calculation_context_id"])

    with Session(engine) as verification:
        retained_pipeline = verification.get(PipelineRun, pipeline_id)
        retained_features = list(
            verification.scalars(
                select(BackgroundJob)
                .where(BackgroundJob.workflow_key == workflow_key)
                .order_by(BackgroundJob.id)
            )
        )
        assert retained_pipeline.result_json["ceri_calculation_context_id"] == context_id
        assert {int(row.payload_json["calculation_context_id"]) for row in retained_features} == {
            context_id
        }
        context_service_row = verification.get(MarketCalculationContext, context_id)
        assert context_service_row is not None
        assert context_service_row.upload_run_id == retained_pipeline.upload_run_id
    engine.dispose()


def test_missing_ceri_context_is_repaired_only_without_dependent_artifacts(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        pipeline, features = _seed_ceri_authority_fixture(db, suffix="repair")
        dangling = {
            **features[0].payload_json,
            "calculation_context_id": 999_999,
            "cutoff_at": datetime(2026, 8, 12, 12, tzinfo=UTC).isoformat(),
            "as_of_session": "2026-08-11",
            "calendar_version": "swinglens-us-equities-v1",
        }
        features[0].payload_json = dangling
        db.commit()
        repaired = freeze_or_validate_pipeline_ceri_authority(
            db,
            job=features[0],
            payload=dangling,
        )
        db.commit()
        assert int(repaired["calculation_context_id"]) != 999_999
        assert (
            pipeline.result_json["ceri_calculation_context_id"]
            == repaired["calculation_context_id"]
        )
        assert {row.payload_json["calculation_context_id"] for row in features} == {
            repaired["calculation_context_id"]
        }
    engine.dispose()


def test_missing_ceri_context_with_existing_manifest_fails_closed(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        pipeline, features = _seed_ceri_authority_fixture(db, suffix="fail-closed")
        frozen = freeze_or_validate_pipeline_ceri_authority(
            db,
            job=features[0],
            payload=dict(features[0].payload_json or {}),
        )
        db.commit()
        anchor = dict(features[0].payload_json["effective_configuration_anchor"])
        db.add(
            CeriFeatureSourceManifest(
                background_job_id=features[0].id,
                run_id=pipeline.upload_run_id,
                pipeline_run_id=pipeline.id,
                calculation_context_id=int(frozen["calculation_context_id"]),
                batch_index=1,
                cutoff_at=datetime.fromisoformat(str(frozen["cutoff_at"])),
                as_of_session=date.fromisoformat(str(frozen["as_of_session"])),
                calendar_version=str(frozen["calendar_version"]),
                configuration_anchor_id=str(anchor["anchor_id"]),
                configuration_fingerprint=str(anchor["fingerprint"]),
                scope_id=features[0].scope_id,
                refresh_cycle_id=features[0].refresh_cycle_id,
                acquisition_plan_id=features[0].acquisition_plan_id,
                bundle_fingerprint="retained-bundle",
                source_count=0,
                manifest_version="ceri-feature-source-manifest-v1",
                manifest_json={"bundle_fingerprint": "retained-bundle", "source_count": 0},
            )
        )
        pipeline.result_json = {
            key: value
            for key, value in pipeline.result_json.items()
            if not key.startswith("ceri_calculation_")
        }
        features[0].payload_json = {
            **features[0].payload_json,
            "calculation_context_id": 999_999,
        }
        db.commit()

        with pytest.raises(CeriContextIntegrityError) as caught:
            freeze_or_validate_pipeline_ceri_authority(
                db,
                job=features[0],
                payload=dict(features[0].payload_json),
            )
        assert caught.value.code == "CERI_CONTEXT_DANGLING"
        assert "ceri_feature_source_manifests" in caught.value.diagnostics["artifacts"]
        db.rollback()
    engine.dispose()


def test_ceri_async_timing_replaces_dispatch_only_duration_with_logical_duration(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    now = datetime.now(UTC)
    with Session(engine) as db:
        pipeline, features = _seed_ceri_authority_fixture(db, suffix="timing")
        pipeline.result_json = {
            **pipeline.result_json,
            "performance": {"step_durations_ms": {CERI_PIPELINE_PROVIDER_INGEST_STEP: 1_408.0}},
        }
        provider_step = PipelineStep(
            pipeline_run_id=pipeline.id,
            step_name=CERI_PIPELINE_PROVIDER_INGEST_STEP,
            step_order=9,
            status=PipelineStepStatus.COMPLETED,
            started_at=now - timedelta(minutes=10),
            completed_at=now,
            result_json={
                "async_timing": {
                    "dispatch_duration_ms": 1_408.0,
                    "dependency_wait_started_at": (
                        now - timedelta(minutes=9, seconds=58)
                    ).isoformat(),
                }
            },
        )
        for index, feature in enumerate(features):
            feature.started_at = now - timedelta(minutes=8 - index)
            feature.completed_at = now - timedelta(minutes=1 - index)
        db.add(provider_step)
        db.flush()

        _finalize_ceri_async_timing(
            db,
            pipeline=pipeline,
            workflow_key=features[0].workflow_key,
            provider_step=provider_step,
            status=PipelineStepStatus.COMPLETED,
        )
        db.commit()

        timing = pipeline.result_json["performance"]["async_step_timings"][
            CERI_PIPELINE_PROVIDER_INGEST_STEP
        ]
        assert timing["dispatch_duration_ms"] == 1_408.0
        assert timing["dependency_wait_duration_ms"] == pytest.approx(598_000, abs=5)
        assert timing["total_logical_duration_ms"] == pytest.approx(600_000, abs=5)
        assert pipeline.result_json["performance"]["step_durations_ms"][
            CERI_PIPELINE_PROVIDER_INGEST_STEP
        ] == pytest.approx(600_000, abs=5)
    engine.dispose()


def test_ceri_parent_rollup_retains_actionable_child_failure(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        pipeline, features = _seed_ceri_authority_fixture(db, suffix="parent-diagnostic")
        workflow_key = f"ceri:feature-certification:{pipeline.id}:diagnostic"
        failed = features[0]
        failed.workflow_key = workflow_key
        failed.status = JobStatus.FAILED
        failed.progress_stage = "CERI_FEATURE_PREPARE"
        failed.payload_json = {
            **failed.payload_json,
            "workflow_key": workflow_key,
            "calculation_context_id": 16,
        }
        failed.result_json = {
            "failure_classification": {
                "kind": "DETERMINISTIC",
                "retryable": False,
                "code": "CERI_CONTEXT_DANGLING",
                "diagnostics": {"calculation_context_id": 16},
            }
        }
        pipeline.result_json = {"feature_certification_workflow_key": workflow_key}
        db.flush()

        _roll_up_ceri_pipeline_failure(
            db,
            pipeline,
            workflow_key=workflow_key,
            failed_jobs=(failed,),
        )
        db.commit()

        assert pipeline.status == "FAILED"
        assert pipeline.error_message == "CERI_FEATURE_CERTIFICATION_FAILED"
        detail = pipeline.result_json["ceri_failure_detail"]
        assert detail["job_id"] == failed.id
        assert detail["ceri_stage"] == "CERI_FEATURE_PREPARE"
        assert detail["feature_batch"] == 1
        assert detail["calculation_context_id"] == 16
        assert detail["classification"]["code"] == "CERI_CONTEXT_DANGLING"
    engine.dispose()


def test_postgresql_bulk_rebuild_is_idempotent_incremental_and_query_bounded(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    statements: list[str] = []
    authority_reads: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def record_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        if _context.execution_options.get("t14b_source_authority"):
            authority_reads.append(statement)
            return
        statements.append(statement.lstrip().split(None, 1)[0].upper())

    with Session(engine, expire_on_commit=False) as db:
        run_id, ingestion_run_id = _seed_fixture(db, request_key="perf-fixture:ingest:MSFT")
        processing = _new_job(
            db,
            job_type="CERI_NORMALIZE",
            related_run_id=run_id,
            request_key="perf-fixture:normalize:MSFT",
            status=JobStatus.RUNNING,
            payload_json={
                "request_key": "perf-fixture:normalize:MSFT",
                "ingestion_run_id": ingestion_run_id,
                "provider": "eodhd",
                "dataset": "estimates",
                "ticker": "MSFT",
                "run_id": run_id,
                "scope": {"ticker": "MSFT", "run_id": run_id},
            },
        )
        db.add(processing)
        db.flush()
        _execute_handler(db, processing, execute_normalize_job)
        request = CeriFeatureRebuildRequest(
            ticker="MSFT",
            run_id=run_id,
            as_of_session=date(2026, 8, 12),
        )
        service = CeriFeatureRebuildService()

        statements.clear()
        authority_reads.clear()
        first = service.rebuild(db, request)
        db.commit()
        first_selects = statements.count("SELECT")
        first_authority_reads = len(authority_reads)
        first_counts = (
            db.scalar(select(func.count()).select_from(CeriRevisionFeature)),
            db.scalar(select(func.count()).select_from(CeriDerivedFeature)),
            db.scalar(select(func.count()).select_from(CeriFeatureBuildState)),
        )
        first_hashes = tuple(
            db.scalars(select(CeriRevisionFeature.evidence_hash).order_by(CeriRevisionFeature.id))
        )

        statements.clear()
        second = service.rebuild(db, request)
        db.commit()
        second_counts = (
            db.scalar(select(func.count()).select_from(CeriRevisionFeature)),
            db.scalar(select(func.count()).select_from(CeriDerivedFeature)),
            db.scalar(select(func.count()).select_from(CeriFeatureBuildState)),
        )
        second_hashes = tuple(
            db.scalars(select(CeriRevisionFeature.evidence_hash).order_by(CeriRevisionFeature.id))
        )

        assert first.companies_rebuilt == 1
        assert first_selects <= 12
        assert first_authority_reads <= 3
        assert first.sql_write_count <= 5
        assert second.companies_skipped_unchanged == 1
        assert second_counts == first_counts
        assert second_hashes == first_hashes

    engine.dispose()


def test_optimized_50_company_batch_emits_bounded_performance_telemetry(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def record_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.lstrip().split(None, 1)[0].upper())

    tickers = tuple(f"P{index:03d}" for index in range(50))
    with Session(engine, expire_on_commit=False) as db:
        companies = [CeriCompany(ticker=ticker, exchange="US") for ticker in tickers]
        db.add_all(companies)
        db.flush()
        sources = [
            CeriSourceRecord(
                provider="fixture",
                dataset="estimates",
                provider_record_id=f"{company.ticker}-current",
                content_hash=f"content-{company.ticker}",
                idempotency_key=f"perf-{company.ticker}",
                export_policy="exportable",
                redistribution_allowed=False,
                purge_eligible=False,
                retrieved_at=datetime(2026, 8, 10, 20, tzinfo=UTC),
                ingested_at=datetime(2026, 8, 10, 20, tzinfo=UTC),
            )
            for company in companies
        ]
        db.add_all(sources)
        db.flush()
        db.add_all(
            [
                CeriEstimateSnapshot(
                    source_record_id=source.id,
                    company_id=company.id,
                    metric="EPS_DILUTED",
                    fiscal_period_end=date(2026, 9, 30),
                    period_type="CURRENT_QUARTER",
                    canonical_period_slot="CURRENT_QUARTER",
                    consensus="2.0",
                    high="2.2",
                    low="1.8",
                    analyst_count=10,
                    upward_count=6,
                    downward_count=2,
                    canonical_currency="USD",
                    canonical_scale="1",
                    effective_at=datetime(2026, 8, 10, 20, tzinfo=UTC),
                    known_at=datetime(2026, 8, 10, 20, tzinfo=UTC),
                    effective_session=date(2026, 8, 10),
                    canonical_observation_key=f"{company.ticker}:EPS:CQ:2026Q3",
                )
                for company, source in zip(companies, sources, strict=True)
            ]
        )
        db.commit()
        request = CeriFeatureRebuildRequest(tickers=tickers, as_of_session=date(2026, 8, 12))
        service = CeriFeatureRebuildService()

        statements.clear()
        started = perf_counter()
        first = service.rebuild(db, request)
        db.commit()
        first_wall_ms = int((perf_counter() - started) * 1000)
        first_selects = statements.count("SELECT")
        first_writes = sum(statements.count(verb) for verb in ("INSERT", "UPDATE", "DELETE"))

        statements.clear()
        started = perf_counter()
        second = service.rebuild(db, request)
        db.commit()
        second_wall_ms = int((perf_counter() - started) * 1000)

        telemetry = {
            "ticker_count": 50,
            "first_wall_ms": first_wall_ms,
            "first_seconds_per_ticker": first_wall_ms / 50_000,
            "first_select_count": first_selects,
            "first_write_count": first_writes,
            "first_companies_rebuilt": first.companies_rebuilt,
            "first_load_context_ms": first.load_context_ms,
            "first_batch_total_ms": first.batch_total_ms,
            "second_wall_ms": second_wall_ms,
            "second_companies_skipped": second.companies_skipped_unchanged,
            "rows_loaded": first.rows_loaded,
            "family_runtime_ms": first.family_runtime_ms,
            "persistence_ms": first.persistence_ms,
        }
        print("CERI_PERF_TELEMETRY=" + json.dumps(telemetry, sort_keys=True))

        assert first.companies_rebuilt == 50
        assert first_selects <= 12
        assert first_writes <= 200
        assert second.companies_skipped_unchanged == 50
        assert first_wall_ms < 60_000

    engine.dispose()


def _upgrade(database_url: str, *, revision: str = "head") -> None:
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    run_guarded_alembic_upgrade(config, database_url, revision)


def _execute_legacy_fixture(database_url: str) -> dict:
    engine = create_engine(database_url)
    with Session(engine) as db:
        run_id, ingestion_run_id = _seed_fixture(db, request_key="legacy:ingest:MSFT")
        authority = _fixture_authority(db, run_id=run_id, cycle_key="legacy-parity")
        pipeline = PipelineRun(upload_run_id=run_id, status="RUNNING")
        bind_semantic_authority(pipeline, authority)
        db.add(pipeline)
        db.flush()
        cutoff = create_pipeline_market_context(
            db, pipeline, cutoff_at=datetime(2026, 9, 8, 17, 30, tzinfo=UTC)
        )
        temporal_payload = {
            "pipeline_run_id": pipeline.id,
            "calculation_context_id": cutoff.context_id,
            "cutoff_at": cutoff.cutoff_at.isoformat(),
            "as_of_session": cutoff.latest_completed_session.isoformat(),
            "calendar_version": cutoff.calendar_version,
        }
        normalize = _new_job(
            db,
            job_type="CERI_NORMALIZE",
            related_run_id=run_id,
            request_key="legacy:normalize:MSFT",
            status=JobStatus.RUNNING,
            priority=70,
            payload_json={
                "request_key": "legacy:normalize:MSFT",
                "ingestion_run_id": ingestion_run_id,
                "provider": "eodhd",
                "dataset": "estimates",
                "ticker": "MSFT",
                "run_id": run_id,
                "scope": {"ticker": "MSFT", "run_id": run_id},
                **temporal_payload,
            },
            max_retries=3,
        )
        bind_semantic_authority(normalize, authority)
        db.add(normalize)
        db.flush()
        _execute_handler(db, normalize, execute_normalize_job)
        feature = db.scalar(
            select(BackgroundJob).where(BackgroundJob.job_type == CERI_REBUILD_FEATURES)
        )
        _execute_handler(db, feature, execute_rebuild_features_job)
        capture = db.scalar(select(BackgroundJob).where(BackgroundJob.job_type == CERI_CAPTURE_RUN))
        _execute_handler(db, capture, execute_capture_run_job)
        db.refresh(capture)
        capture_metadata = dict((capture.operational_metadata_json or {}).get("ceri_capture") or {})
        assert capture_metadata.get("processed") == 1
        assert capture.progress_sequence >= 15
        change = db.scalar(
            select(BackgroundJob).where(BackgroundJob.job_type == CERI_CHANGE_DETECTION)
        )
        _execute_handler(db, change, execute_change_detection_job)
        alert = db.scalar(select(BackgroundJob).where(BackgroundJob.job_type == CERI_ALERT_REBUILD))
        if alert is not None:
            _execute_handler(db, alert, execute_alert_rebuild_job)
        fingerprint = _parity_fingerprint(db)
    engine.dispose()
    return fingerprint


def _execute_batched_fixture(database_url: str) -> dict:
    engine = create_engine(database_url)
    with Session(engine) as db:
        run_id, ingestion_run_id = _seed_fixture(
            db,
            request_key="ceri:fixture:ingest:eodhd:estimates:MSFT",
        )
        authority = _fixture_authority(db, run_id=run_id, cycle_key="batched-parity")
        pipeline = PipelineRun(upload_run_id=run_id, status="RUNNING")
        bind_semantic_authority(pipeline, authority)
        db.add(pipeline)
        db.flush()
        cutoff = create_pipeline_market_context(
            db, pipeline, cutoff_at=datetime(2026, 9, 8, 17, 30, tzinfo=UTC)
        )
        workflow_key = f"ceri:pipeline:{pipeline.id}:fixture-config"
        ingestion = db.get(CeriIngestionRun, ingestion_run_id)
        ingestion.request_key = (
            f"{workflow_key}:ingest:eodhd:estimates:MSFT:refresh:{authority.refresh_cycle_id}"
        )
        temporal_payload = {
            "pipeline_run_id": pipeline.id,
            "calculation_context_id": cutoff.context_id,
            "cutoff_at": cutoff.cutoff_at.isoformat(),
            "as_of_session": cutoff.latest_completed_session.isoformat(),
            "calendar_version": cutoff.calendar_version,
        }
        provider = _new_job(
            db,
            job_type=CERI_PROVIDER_INGEST_BATCH,
            workflow_key=workflow_key,
            request_key=f"{workflow_key}:provider:eodhd:estimates:0001",
            related_run_id=run_id,
            status=JobStatus.COMPLETED,
            priority=80,
            payload_json={},
            max_retries=3,
        )
        normalize = _new_job(
            db,
            job_type=CERI_NORMALIZE_BATCH,
            workflow_key=workflow_key,
            request_key=f"{workflow_key}:normalize:eodhd:estimates:0001",
            related_run_id=run_id,
            status=JobStatus.RUNNING,
            priority=81,
            payload_json={
                "workflow_key": workflow_key,
                "provider": "eodhd",
                "dataset": "estimates",
                "tickers": ["MSFT"],
                "run_id": run_id,
                "checkpoint_interval": 1,
                **temporal_payload,
            },
            max_retries=3,
        )
        bind_semantic_authority(provider, authority)
        bind_semantic_authority(normalize, authority)
        db.add_all([provider, normalize])
        db.commit()
        _execute_handler(db, normalize, execute_normalize_batch_job)
        feature = _new_job(
            db,
            job_type=CERI_FEATURE_BATCH,
            workflow_key=workflow_key,
            request_key=f"{workflow_key}:feature:0001",
            related_run_id=run_id,
            status=JobStatus.RUNNING,
            priority=130,
            payload_json={
                "workflow_key": workflow_key,
                "tickers": ["MSFT"],
                "run_id": run_id,
                "expected_normalization_batches": 1,
                "checkpoint_interval": 1,
                **temporal_payload,
            },
            max_retries=3,
        )
        bind_semantic_authority(feature, authority)
        db.add(feature)
        db.commit()
        _execute_handler(db, feature, execute_feature_batch_job)
        finalizer = _new_job(
            db,
            job_type=CERI_RUN_FINALIZE,
            workflow_key=workflow_key,
            request_key=f"{workflow_key}:finalize",
            related_run_id=run_id,
            status=JobStatus.RUNNING,
            priority=140,
            payload_json={
                "workflow_key": workflow_key,
                "run_id": run_id,
                "expected_feature_batches": 1,
                **temporal_payload,
            },
            max_retries=3,
        )
        bind_semantic_authority(finalizer, authority)
        db.add(finalizer)
        db.commit()
        _execute_handler(db, finalizer, execute_run_finalize_job)
        capture = db.scalar(select(BackgroundJob).where(BackgroundJob.job_type == CERI_CAPTURE_RUN))
        _execute_handler(db, capture, execute_capture_run_job)
        change = db.scalar(
            select(BackgroundJob).where(BackgroundJob.job_type == CERI_CHANGE_DETECTION)
        )
        _execute_handler(db, change, execute_change_detection_job)
        alert = db.scalar(select(BackgroundJob).where(BackgroundJob.job_type == CERI_ALERT_REBUILD))
        _execute_handler(db, alert, execute_alert_rebuild_job)
        effects_before_retry = _effect_counts(db)
        _execute_handler(db, capture, execute_capture_run_job)
        _execute_handler(db, change, execute_change_detection_job)
        _execute_handler(db, alert, execute_alert_rebuild_job)
        assert _effect_counts(db) == effects_before_retry
        fingerprint = _parity_fingerprint(db)
    engine.dispose()
    return fingerprint


def _fixture_authority(db: Session, *, run_id: int, cycle_key: str):
    return admit_frozen_operation(
        db,
        operation_kind="full-pipeline-run",
        subject_kind="ticker",
        members=(ScopeMember("TICKER", "MSFT"),),
        cycle_key=f"ceri-parity:{cycle_key}:{run_id}",
        business_cutoff=date(2026, 8, 12),
        provider_source_class="CERI",
        request_type="FEATURE_REBUILD",
        requirements=(AcquisitionRequirement("CERI_SOURCE_RECORDS"),),
        policy_identity="ceri-parity-fixture-v1",
        scope_definition={"run_id": run_id, "tickers": ["MSFT"]},
    )


def _seed_zero_history_run(db: Session) -> int:
    run = UploadRun(filename="ceri-zero-history.csv", row_count=1, status="COMPLETED")
    company = CeriCompany(
        ticker="MSFT",
        exchange="US",
        current_provider_ids_json={"eodhd": "MSFT.US"},
    )
    db.add_all([run, company])
    db.flush()
    db.add(
        RawCompanyRow(
            run_id=run.id,
            row_number=1,
            ticker="MSFT",
            company_name="Microsoft",
            sector="Technology",
            raw_json={
                "ticker": "MSFT",
                "fundamental_score": 8,
                "technical_score": 7,
                "market_regime": "Bull trend",
            },
        )
    )
    db.commit()
    return run.id


def _seed_ceri_authority_fixture(
    db: Session,
    *,
    suffix: str,
) -> tuple[PipelineRun, list[BackgroundJob]]:
    run_id = _seed_zero_history_run(db)
    authority = _fixture_authority(db, run_id=run_id, cycle_key=f"authority-{suffix}")
    pipeline = PipelineRun(upload_run_id=run_id, status="RUNNING", result_json={})
    bind_semantic_authority(pipeline, authority)
    db.add(pipeline)
    db.flush()
    workflow_key = f"ceri:pipeline:{pipeline.id}:authority-{suffix}"
    features: list[BackgroundJob] = []
    for batch_index in (1, 2):
        feature = _new_job(
            db,
            job_type=CERI_FEATURE_BATCH,
            workflow_key=workflow_key,
            request_key=f"{workflow_key}:feature:{batch_index:04d}",
            related_run_id=run_id,
            status=JobStatus.QUEUED,
            priority=130,
            payload_json={
                "workflow_key": workflow_key,
                "pipeline_run_id": pipeline.id,
                "run_id": run_id,
                "tickers": ["MSFT"],
                "batch_index": batch_index,
                "expected_normalization_batches": 0,
                "checkpoint_interval": 1,
            },
            max_retries=3,
        )
        bind_semantic_authority(feature, authority)
        features.append(feature)
    db.commit()
    return pipeline, features


def _seed_current_run_estimates(
    db: Session,
    *,
    run_id: int,
    request_key: str,
    commit: bool = True,
) -> int:
    ingestion = CeriIngestionRun(
        provider="eodhd",
        provider_terms_version="fixture-1",
        dataset="estimates",
        status="COMPLETED",
        request_key=request_key,
        scope_json={"ticker": "MSFT", "run_id": run_id},
        requested_count=4,
        fetched_count=4,
        inserted_count=4,
        completed_at=datetime(2026, 8, 12, 12, tzinfo=UTC),
    )
    db.add(ingestion)
    db.flush()
    observations = (
        ("2026-04-30T20:00:00+00:00", "10.0"),
        ("2026-07-01T20:00:00+00:00", "11.0"),
        ("2026-08-01T20:00:00+00:00", "12.0"),
        ("2026-08-11T20:00:00+00:00", "13.0"),
    )
    for index, (effective_at, consensus) in enumerate(observations, start=1):
        source = CeriSourceRecord(
            ingestion_run_id=ingestion.id,
            provider="eodhd",
            provider_terms_version="fixture-1",
            dataset="estimates",
            provider_record_id=f"MSFT-current-run-estimate-{index}",
            company_hint_json={"ticker": "MSFT", "exchange": "US"},
            restricted_normalized_json={
                "ticker": "MSFT",
                "metric": "EPS_DILUTED",
                "period_type": "NEXT_FISCAL_YEAR",
                "fiscal_period_end": "2027-06-30",
                "consensus": consensus,
                "high": str(float(consensus) + 1),
                "low": str(float(consensus) - 1),
                "analyst_count": 12,
                "upward_count": 8,
                "downward_count": 2,
                "currency": "USD",
                "effective_at": effective_at,
            },
            observed_at=datetime.fromisoformat(effective_at),
            content_hash=f"zero-history-content-{index}",
            idempotency_key=f"zero-history-estimate-{index}",
            export_policy="exportable",
            redistribution_allowed=False,
            purge_eligible=False,
            retrieved_at=datetime(2026, 8, 12, 12, tzinfo=UTC),
            ingested_at=datetime(2026, 8, 12, 12, tzinfo=UTC),
        )
        db.add(source)
        db.flush()
        db.add(
            CeriIngestionRunSourceRecord(
                ingestion_run_id=ingestion.id,
                source_record_id=source.id,
                ticker="MSFT",
                provider=source.provider,
                dataset=source.dataset,
                ingestion_outcome="INSERTED",
                normalization_state="PENDING",
            )
        )
    if commit:
        db.commit()
    else:
        db.flush()
    return ingestion.id


def _seed_fixture(db: Session, *, request_key: str) -> tuple[int, int]:
    run = UploadRun(filename="ceri-parity.csv", row_count=1, status="COMPLETED")
    company = CeriCompany(
        ticker="MSFT",
        exchange="US",
        current_provider_ids_json={"eodhd": "MSFT.US"},
    )
    db.add_all([run, company])
    db.flush()
    db.add(
        RawCompanyRow(
            run_id=run.id,
            row_number=1,
            ticker="MSFT",
            company_name="Microsoft",
            sector="Technology",
            raw_json={
                "ticker": "MSFT",
                "fundamental_score": 8,
                "technical_score": 7,
                "market_regime": "Bull trend",
            },
        )
    )
    ingestion = CeriIngestionRun(
        provider="eodhd",
        provider_terms_version="fixture-1",
        dataset="estimates",
        status="COMPLETED",
        request_key=request_key,
        scope_json={"ticker": "MSFT", "run_id": run.id},
        requested_count=4,
        fetched_count=4,
        inserted_count=4,
        completed_at=datetime(2026, 8, 12, 12, tzinfo=UTC),
    )
    db.add(ingestion)
    db.flush()
    observations = (
        ("2026-04-30T20:00:00+00:00", "10.0"),
        ("2026-07-01T20:00:00+00:00", "11.0"),
        ("2026-08-01T20:00:00+00:00", "12.0"),
        ("2026-08-11T20:00:00+00:00", "13.0"),
    )
    for index, (effective_at, consensus) in enumerate(observations, start=1):
        db.add(
            CeriSourceRecord(
                ingestion_run_id=ingestion.id,
                provider="eodhd",
                provider_terms_version="fixture-1",
                dataset="estimates",
                provider_record_id=f"MSFT-estimate-{index}",
                company_hint_json={"ticker": "MSFT", "exchange": "US"},
                restricted_normalized_json={
                    "ticker": "MSFT",
                    "metric": "EPS_DILUTED",
                    "period_type": "NEXT_FISCAL_YEAR",
                    "fiscal_period_end": "2027-06-30",
                    "consensus": consensus,
                    "high": str(float(consensus) + 1),
                    "low": str(float(consensus) - 1),
                    "analyst_count": 12,
                    "upward_count": 8,
                    "downward_count": 2,
                    "currency": "USD",
                    "effective_at": effective_at,
                },
                observed_at=datetime.fromisoformat(effective_at),
                content_hash=f"content-{index}",
                idempotency_key=f"fixture-estimate-{index}",
                export_policy="exportable",
                redistribution_allowed=False,
                purge_eligible=False,
                retrieved_at=datetime(2026, 8, 12, 12, tzinfo=UTC),
                ingested_at=datetime(2026, 8, 12, 12, tzinfo=UTC),
            )
        )
    db.commit()
    return run.id, ingestion.id


def _claim_native_fixture_job(db: Session, job: BackgroundJob) -> None:
    if job.status == JobStatus.RUNNING:
        return
    worker_id = f"t14d-native-fixture-{job.id}"
    worker = register_worker(
        db,
        worker_id=worker_id,
        queues=("interactive", "broker", "background"),
        heartbeat_timeout_seconds=30,
        hostname="t14d-disposable",
        process_id=job.id,
    )
    db.commit()
    other_types = tuple(
        kind
        for kind in db.scalars(select(BackgroundJob.job_type).distinct())
        if kind != job.job_type
    )
    claimed = claim_next_job(
        db,
        worker_id=worker_id,
        worker_instance_id=worker.instance_id,
        excluded_job_types=other_types,
    )
    assert claimed is not None and claimed.id == job.id
    db.commit()


def _new_job(db: Session, *, job_type, payload_json, status, **kwargs) -> BackgroundJob:
    # Authority is frozen by the production enqueue before delivery, never
    # retrofitted into a pre-existing unanchored historical job.
    job = enqueue_job(db, job_type, payload_json, **kwargs)
    if status == JobStatus.RUNNING:
        _claim_native_fixture_job(db, job)
    elif status == JobStatus.COMPLETED:
        job.status = status  # Terminal upstream acquisition bookkeeping fixture.
        db.flush()
    return job


def _execute_handler(db: Session, job: BackgroundJob, handler) -> dict:
    if job.status == JobStatus.COMPLETED:
        # A new retained delivery of the same business request obtains a real
        # current attempt; a completed historical job is not made authoritative.
        completed_job = job
        job = enqueue_job(
            db,
            completed_job.job_type,
            dict(completed_job.payload_json),
            request_key=f"{completed_job.request_key}:redelivery:{completed_job.id}",
            related_run_id=completed_job.related_run_id,
            workflow_key=completed_job.workflow_key,
            priority=completed_job.priority,
            max_retries=completed_job.max_retries,
        )
        try:
            authority = require_semantic_authority(completed_job)
        except LegacySemanticAuthorityError:
            pass
        else:
            bind_semantic_authority(job, authority)
        db.commit()
    _claim_native_fixture_job(db, job)
    result = handler(db, job)
    assert not (result or {}).get("failed"), result
    job.status = (
        JobStatus.PARTIAL
        if result and result.get("status") == JobStatus.PARTIAL
        else JobStatus.COMPLETED
    )
    db.commit()
    return result or {}


def _parity_fingerprint(db: Session) -> dict:
    sources = list(db.scalars(select(CeriSourceRecord).order_by(CeriSourceRecord.id)))
    normalized = list(db.scalars(select(CeriEstimateSnapshot).order_by(CeriEstimateSnapshot.id)))
    features = list(db.scalars(select(CeriRevisionFeature).order_by(CeriRevisionFeature.id)))
    snapshots = list(db.scalars(select(CeriScoreSnapshot).order_by(CeriScoreSnapshot.id)))
    changes = list(db.scalars(select(CeriChangeEvent).order_by(CeriChangeEvent.id)))
    alerts = list(db.scalars(select(CeriAlertEvent).order_by(CeriAlertEvent.id)))
    return {
        "source_records": [
            (
                row.provider,
                row.dataset,
                row.provider_record_id,
                row.restricted_normalized_json,
                row.content_hash,
                row.idempotency_key,
                row.observed_at.isoformat() if row.observed_at else None,
                row.quarantine_reason,
            )
            for row in sources
        ],
        "normalized": [
            (
                row.metric,
                row.period_type,
                row.fiscal_period_end.isoformat(),
                str(row.consensus),
                str(row.high),
                str(row.low),
                row.analyst_count,
                row.upward_count,
                row.downward_count,
                row.effective_session.isoformat() if row.effective_session else None,
                row.canonical_observation_key,
                row.quality_flags_json,
            )
            for row in normalized
        ],
        "features": [
            (
                row.metric,
                row.period_key,
                row.as_of_session.isoformat(),
                row.window_days,
                str(row.absolute_change),
                str(row.pct_change),
                str(row.acceleration),
                row.revision_confidence_label,
                row.warnings_json,
                row.unavailable_reason,
                row.evidence_hash,
            )
            for row in features
        ],
        "snapshots": [
            (
                row.ticker,
                row.as_of_session.isoformat(),
                row.opportunity_score,
                row.event_risk_score,
                row.data_confidence,
                row.coverage_pct,
                row.posture,
                row.alignment_flags_json,
                row.component_json,
                row.reasons_json,
                row.warnings_json,
                row.evidence_hash,
            )
            for row in snapshots
        ],
        "changes": [
            (
                row.change_type,
                row.severity,
                row.delta_json,
                row.dedup_key,
            )
            for row in changes
        ],
        "alerts": [
            (
                row.event_key,
                row.ticker,
                row.severity,
                row.status,
                row.evidence_json,
            )
            for row in alerts
        ],
    }


def _effect_counts(db: Session) -> tuple[int, int, int]:
    return (
        int(db.scalar(select(func.count(CeriScoreSnapshot.id))) or 0),
        int(db.scalar(select(func.count(CeriChangeEvent.id))) or 0),
        int(db.scalar(select(func.count(CeriAlertEvent.id))) or 0),
    )
