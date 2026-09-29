from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from alembic import command
from app.models.ceri_tables import CeriCompany, CeriScoreSnapshot
from app.models.tables import (
    BackgroundJob,
    CoreCalculationEvidence,
    PipelineDependency,
    PipelineStep,
    RawCompanyRow,
    SetupSignalSnapshot,
    UploadRun,
    WinnerPredictionSnapshot,
)
from app.services.background_job_service import JobStatus, enqueue_job
from app.services.ceri.parent_pipeline_fence import require_parent_pipeline_active
from app.services.market_calculation_context_service import market_context_for_pipeline
from app.services.pipeline_dependency_service import prepare_ceri_workflow_dependency
from app.services.pipeline_prerequisites import CeriParentPipelineTerminalError
from app.services.pipeline_service import (
    DECISION_HANDOFF_PIPELINE_STEP,
    FULL_PIPELINE_JOB_TYPE,
    PipelineStatus,
    PipelineStepStatus,
    enqueue_pipeline_after_ceri_completion,
    roll_up_ceri_pipeline_job_failure,
    start_pipeline,
)
from app.services.scope_refresh_adoption import bind_semantic_authority, require_semantic_authority
from app.settings import Settings


def test_ceri_completion_barrier_is_restart_safe_and_exactly_once(
    disposable_postgres_database: str,
    monkeypatch,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    settings = Settings(_env_file=None, winner_probability_capture_in_pipeline=True)
    monkeypatch.setattr("app.services.pipeline_service.get_settings", lambda: settings)

    with Session(engine) as db:
        pipeline, workflow_key, alert_id = _seed_waiting_pipeline(db, certified=True)
        pipeline_id = pipeline.id
        db.commit()

    # A fresh Session represents a worker restart after the provider DAG was
    # committed but before its terminal continuation was evaluated.
    with Session(engine) as db:
        alert = db.get(BackgroundJob, alert_id)
        first = enqueue_pipeline_after_ceri_completion(db, alert)
        db.commit()
        first_id = first.id

    with Session(engine) as db:
        alert = db.get(BackgroundJob, alert_id)
        duplicate = enqueue_pipeline_after_ceri_completion(db, alert)
        db.commit()
        pipeline = db.get(type(pipeline), pipeline_id)
        continuations = list(
            db.scalars(
                select(BackgroundJob).where(
                    BackgroundJob.job_type == FULL_PIPELINE_JOB_TYPE,
                    BackgroundJob.request_key
                    == (
                        f"resume-pipeline:{pipeline_id}:after-ceri:{workflow_key}:"
                        f"from:{DECISION_HANDOFF_PIPELINE_STEP}"
                    ),
                )
            )
        )
        assert duplicate.id == first_id
        assert len(continuations) == 1
        assert pipeline.status == PipelineStatus.PENDING
        assert pipeline.current_step == DECISION_HANDOFF_PIPELINE_STEP
        assert pipeline.result_json["ceri_completion_state"] == "CERTIFIED"
        assert pipeline.result_json["ceri_certified_capture_count"] == 1
        assert continuations[0].payload_json["pipeline_run_id"] == pipeline_id
        assert continuations[0].payload_json["ceri_provider_workflow_key"] == workflow_key
        dependency = db.scalar(
            select(PipelineDependency).where(
                PipelineDependency.pipeline_run_id == pipeline_id,
                PipelineDependency.dependency_type == "CERI_WORKFLOW",
            )
        )
        assert dependency.state == "COMPLETED"
        assert dependency.child_job_id == alert.id
        assert dependency.continuation_job_id == first_id
        assert continuations[0].pipeline_dependency_id == dependency.id
        assert require_semantic_authority(continuations[0]) == require_semantic_authority(pipeline)

    engine.dispose()


def test_ceri_completion_barrier_rolls_partial_child_into_parent_without_downstream(
    disposable_postgres_database: str,
    monkeypatch,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    settings = Settings(_env_file=None, winner_probability_capture_in_pipeline=True)
    monkeypatch.setattr("app.services.pipeline_service.get_settings", lambda: settings)

    with Session(engine) as db:
        pipeline, _workflow_key, alert_id = _seed_waiting_pipeline(
            db,
            certified=True,
            provider_status=JobStatus.PARTIAL,
        )
        pipeline_id = pipeline.id
        alert = db.get(BackgroundJob, alert_id)
        assert enqueue_pipeline_after_ceri_completion(db, alert) is None
        db.commit()

    with Session(engine) as db:
        pipeline = db.get(type(pipeline), pipeline_id)
        step = db.scalar(
            select(PipelineStep).where(
                PipelineStep.pipeline_run_id == pipeline_id,
                PipelineStep.step_name == "CERI_PROVIDER_INGEST",
            )
        )
        downstream = list(
            db.scalars(
                select(BackgroundJob).where(
                    BackgroundJob.job_type == FULL_PIPELINE_JOB_TYPE,
                    BackgroundJob.payload_json["resume_from_step"].astext
                    == DECISION_HANDOFF_PIPELINE_STEP,
                )
            )
        )
        assert pipeline.status == PipelineStatus.PARTIAL
        assert pipeline.completed_at is not None
        assert pipeline.result_json["ceri_completion_state"] == "FAILED"
        assert step.status == PipelineStepStatus.FAILED
        assert downstream == []

    engine.dispose()


def test_ceri_completion_barrier_rejects_missing_certified_capture(
    disposable_postgres_database: str,
    monkeypatch,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    settings = Settings(_env_file=None, winner_probability_capture_in_pipeline=True)
    monkeypatch.setattr("app.services.pipeline_service.get_settings", lambda: settings)

    with Session(engine) as db:
        pipeline, _workflow_key, alert_id = _seed_waiting_pipeline(db, certified=False)
        pipeline_id = pipeline.id
        alert = db.get(BackgroundJob, alert_id)
        assert enqueue_pipeline_after_ceri_completion(db, alert) is None
        db.commit()

    with Session(engine) as db:
        pipeline = db.get(type(pipeline), pipeline_id)
        assert pipeline.status == PipelineStatus.FAILED
        assert pipeline.result_json["ceri_failure_reason"].startswith(
            "CERI_CERTIFIED_CAPTURE_INCOMPLETE"
        )
        assert pipeline.result_json.get("ceri_continuation_job_id") is None

    engine.dispose()


def test_parent_failure_blocks_queued_siblings_and_fences_running_child(
    disposable_postgres_database: str,
) -> None:
    """Incident regression D/E: terminal parent stops the complete CERI tail."""

    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        pipeline, workflow_key, running_alert_id = _seed_waiting_pipeline(
            db,
            certified=False,
            provider_status=JobStatus.FAILED,
        )
        provider = db.scalar(
            select(BackgroundJob).where(
                BackgroundJob.workflow_key == workflow_key,
                BackgroundJob.job_type == "CERI_PROVIDER_INGEST_BATCH",
            )
        )
        payload = {
            "pipeline_run_id": pipeline.id,
            "run_id": pipeline.upload_run_id,
            "workflow_key": workflow_key,
            "calculation_context_id": market_context_for_pipeline(db, pipeline).context_id,
        }
        queued_feature = BackgroundJob(
            job_type="CERI_FEATURE_BATCH",
            status=JobStatus.QUEUED,
            request_key=f"{workflow_key}:feature:fenced",
            workflow_key=workflow_key,
            related_run_id=pipeline.upload_run_id,
            payload_json=payload,
        )
        queued_finalizer = BackgroundJob(
            job_type="CERI_RUN_FINALIZE",
            status=JobStatus.QUEUED,
            request_key=f"{workflow_key}:finalize:fenced",
            workflow_key=workflow_key,
            related_run_id=pipeline.upload_run_id,
            payload_json=payload,
        )
        db.add_all([queued_feature, queued_finalizer])
        db.flush()
        feature_id = queued_feature.id
        finalizer_id = queued_finalizer.id
        pipeline_id = pipeline.id

        roll_up_ceri_pipeline_job_failure(db, provider)
        db.commit()

    with Session(engine) as db:
        pipeline = db.get(type(pipeline), pipeline_id)
        running_alert = db.get(BackgroundJob, running_alert_id)
        queued_feature = db.get(BackgroundJob, feature_id)
        queued_finalizer = db.get(BackgroundJob, finalizer_id)

        assert pipeline.status == PipelineStatus.FAILED
        assert queued_feature.status == JobStatus.BLOCKED
        assert queued_finalizer.status == JobStatus.BLOCKED
        assert queued_feature.result_json["reason_code"] == "CERI_PARENT_PIPELINE_TERMINAL"
        assert queued_finalizer.result_json["reason_code"] == "CERI_PARENT_PIPELINE_TERMINAL"
        with pytest.raises(CeriParentPipelineTerminalError):
            require_parent_pipeline_active(db, running_alert, lock_for_checkpoint=True)
        assert db.scalar(select(func.count()).select_from(CeriScoreSnapshot)) == 0
        assert db.scalar(select(func.count()).select_from(SetupSignalSnapshot)) == 0
        assert db.scalar(select(func.count()).select_from(WinnerPredictionSnapshot)) == 0
        assert not list(
            db.scalars(
                select(BackgroundJob).where(
                    BackgroundJob.job_type == FULL_PIPELINE_JOB_TYPE,
                    BackgroundJob.payload_json["resume_from_step"].astext
                    == DECISION_HANDOFF_PIPELINE_STEP,
                )
            )
        )

    engine.dispose()


def _seed_waiting_pipeline(
    db: Session,
    *,
    certified: bool,
    provider_status: str = JobStatus.COMPLETED,
):
    run = UploadRun(filename="ceri-barrier.csv", row_count=1, status="COMPLETED")
    db.add(run)
    db.flush()
    db.add(
        RawCompanyRow(
            run_id=run.id,
            row_number=1,
            ticker="MSFT",
            company_name="Microsoft",
            sector="Technology",
            raw_json={"Symbol": "MSFT"},
        )
    )
    db.flush()
    pipeline = start_pipeline(
        db,
        upload_run_id=run.id,
        ceri_provider_ingest_enabled=True,
        setup_lifecycle_pipeline_step_enabled=True,
    )
    context = market_context_for_pipeline(db, pipeline)
    workflow_key = f"ceri:pipeline:{run.id}:frozen-test-config"
    pipeline.status = PipelineStatus.WAITING_FOR_CERI_COMPLETION
    pipeline.current_step = "CERI_PROVIDER_INGEST"
    pipeline.result_json = {
        **(pipeline.result_json or {}),
        "ceri_completion_state": "WAITING",
        "ceri_provider_workflow_key": workflow_key,
    }
    prepare_ceri_workflow_dependency(
        db,
        pipeline=pipeline,
        workflow_key=workflow_key,
        resume_from_step=DECISION_HANDOFF_PIPELINE_STEP,
    )
    for step in pipeline.steps:
        if step.step_order <= next(
            row.step_order for row in pipeline.steps if row.step_name == "CERI_PROVIDER_INGEST"
        ):
            step.status = PipelineStepStatus.COMPLETED

    authority = require_semantic_authority(pipeline)
    provider = enqueue_job(
        db,
        "CERI_PROVIDER_INGEST_BATCH",
        {
            "pipeline_run_id": pipeline.id,
            "run_id": run.id,
            "workflow_key": workflow_key,
            "calculation_context_id": context.context_id,
        },
        related_run_id=run.id,
        request_key=f"{workflow_key}:provider",
        workflow_key=workflow_key,
    )
    bind_semantic_authority(provider, authority, required_for_parent_completion=True)
    provider.status = provider_status
    alert = enqueue_job(
        db,
        "CERI_ALERT_REBUILD",
        {
            "pipeline_run_id": pipeline.id,
            "run_id": run.id,
            "workflow_key": workflow_key,
            "calculation_context_id": context.context_id,
        },
        related_run_id=run.id,
        request_key=f"{workflow_key}:alert",
        workflow_key=workflow_key,
        parent_job_id=provider.id,
    )
    bind_semantic_authority(alert, authority, required_for_parent_completion=True)
    alert.status = JobStatus.RUNNING

    if certified:
        company = CeriCompany(
            ticker="MSFT",
            exchange="US",
            current_provider_ids_json={"manual": "MSFT"},
        )
        db.add(company)
        db.flush()
        evidence = CoreCalculationEvidence(
            artifact_kind="CERI",
            run_id=run.id,
            ticker="MSFT",
            calculation_identity_fingerprint="ceri-barrier-identity",
            calculation_identity_json={"run_id": run.id, "ticker": "MSFT"},
            payload_fingerprint="ceri-barrier-payload",
            payload_json={"run_id": run.id, "ticker": "MSFT"},
            source_evidence_ids_json={},
            evidence_key=f"ceri-barrier:{run.id}:MSFT",
            calculated_at=datetime(2026, 9, 24, 20, tzinfo=UTC),
        )
        db.add(evidence)
        db.flush()
        db.add(
            CeriScoreSnapshot(
                evidence_id=evidence.id,
                run_id=run.id,
                source_run_id_text=str(run.id),
                company_id=company.id,
                ticker="MSFT",
                as_of_session=date(2026, 9, 24),
                cutoff_at=context.cutoff_at,
                calculation_context_id=context.context_id,
                calendar_version=context.calendar_version,
                opportunity_score=7.0,
                opportunity_coverage_pct=100.0,
                event_risk_score=2.0,
                data_confidence="High",
                coverage_pct=100.0,
                posture="Positive",
                component_json={},
                opportunity_ledger_json={},
                confidence_ledger_json={},
                event_risk_ledger_json={},
                config_version="test-v1",
                config_hash="frozen-test-config",
                calculation_version="test-v1",
                evidence_contract_version="ceri-evidence-contract-v2",
                comparison_state="NO_PRIOR_COMPARABLE_SNAPSHOT",
                evidence_hash=f"ceri-score:{run.id}:MSFT",
                hash_schema_version="ceri-canonical-json-v2",
            )
        )
    db.flush()
    return pipeline, workflow_key, alert.id


def _upgrade(database_url: str) -> None:
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.attributes["database_url"] = database_url
    command.upgrade(config, "head")
