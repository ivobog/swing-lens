from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database_safety import run_guarded_alembic_upgrade
from app.models.ceri_tables import CeriScoreSnapshot
from app.models.tables import BackgroundJob, PipelineRun
from app.observability.correlation import worker_job_scope
from app.services.background_job_service import JobStatus, mark_job_completed
from app.services.background_worker import JobDeferred
from app.services.ceri.batched_workflow import (
    CERI_FEATURE_BATCH,
    CERI_NORMALIZE_BATCH,
    CERI_PROVIDER_INGEST_BATCH,
    CERI_RUN_FINALIZE,
)
from app.services.ceri.feature_certification_workflow import (
    CeriFeatureCertificationRequest,
    admit_ceri_feature_certification,
    execute_ceri_feature_certification_job,
)
from app.services.ceri.job_handlers import (
    CERI_ALERT_REBUILD,
    CERI_CAPTURE_RUN,
    CERI_CHANGE_DETECTION,
)
from app.services.ceri.parent_pipeline_fence import require_parent_pipeline_active
from app.services.pipeline_prerequisites import CeriParentPipelineTerminalError
from app.services.pipeline_service import PipelineStatus, roll_up_ceri_pipeline_job_failure
from app.settings import get_settings

ROOT = Path(__file__).resolve().parents[2]


def _upgrade(database_url: str) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    run_guarded_alembic_upgrade(config, database_url, "head")


def test_feature_certification_admission_graph_and_terminal_boundary_are_durable(
    disposable_postgres_database: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _upgrade(disposable_postgres_database)
    monkeypatch.setenv("RUNTIME_MODE", "CERTIFICATION")
    monkeypatch.setenv("RUNTIME_INSTANCE_ID", "feature-certification-test-session")
    monkeypatch.setenv("MARKET_DATA_PREWARM_ENABLED", "false")
    monkeypatch.setenv("WINNER_PROBABILITY_AUTO_COHORT_REFRESH_ENABLED", "false")
    monkeypatch.setenv("WINNER_PROBABILITY_AUTO_MATURATION_ENABLED", "false")
    get_settings.cache_clear()
    engine = create_engine(disposable_postgres_database)
    try:
        with Session(engine, expire_on_commit=False) as db:
            admitted = admit_ceri_feature_certification(
                db,
                CeriFeatureCertificationRequest(
                    tickers=("SYN2", "SYN1"),
                    provider_datasets=("eodhd:earnings", "eodhd:estimates"),
                    checkpoint_interval=1,
                    cutoff_at=datetime(2026, 9, 25, 22, tzinfo=UTC),
                    stop_boundary="FEATURE_ONLY",
                    request_key="synthetic-two-ticker-feature-certification",
                    requested_by="pytest",
                ),
            )
            root = db.get(BackgroundJob, admitted.root_job_id)
            assert root is not None
            assert root.payload_json["tickers"] == ["SYN1", "SYN2"]
            assert root.payload_json["checkpoint_interval"] == 1
            assert root.payload_json["allow_downstream_continuation"] is False
            root.status = JobStatus.RUNNING
            db.flush()
            with worker_job_scope(root), pytest.raises(JobDeferred):
                execute_ceri_feature_certification_job(db, root)

            children = list(
                db.scalars(
                    select(BackgroundJob)
                    .where(BackgroundJob.workflow_key == admitted.workflow_key)
                    .where(BackgroundJob.id != root.id)
                    .order_by(BackgroundJob.priority, BackgroundJob.id)
                )
            )
            assert [child.job_type for child in children] == [
                CERI_PROVIDER_INGEST_BATCH,
                CERI_NORMALIZE_BATCH,
                CERI_PROVIDER_INGEST_BATCH,
                CERI_NORMALIZE_BATCH,
                CERI_FEATURE_BATCH,
            ]
            assert all(child.payload_json["tickers"] == ["SYN1", "SYN2"] for child in children)
            assert all(child.payload_json["checkpoint_interval"] == 1 for child in children)
            assert all(child.parent_job_id == root.id for child in children)

            for child in children:
                child.status = JobStatus.COMPLETED
            db.flush()
            with worker_job_scope(root):
                result = execute_ceri_feature_certification_job(db, root)
            mark_job_completed(db, root, result)
            db.commit()

            pipeline = db.get(PipelineRun, admitted.pipeline_run_id)
            assert pipeline is not None
            assert pipeline.status == PipelineStatus.FEATURE_CERTIFIED
            assert root.status == JobStatus.COMPLETED
            all_jobs = list(db.scalars(select(BackgroundJob)))
            prohibited = {
                CERI_RUN_FINALIZE,
                CERI_CAPTURE_RUN,
                CERI_CHANGE_DETECTION,
                CERI_ALERT_REBUILD,
                "FULL_PIPELINE",
                "SETUP_SIGNAL_CAPTURE",
                "WINNER_PREDICTION_CAPTURE",
                "SETUP_LIFECYCLE_EVALUATION",
            }
            assert prohibited.isdisjoint({job.job_type for job in all_jobs})
            assert db.scalar(select(CeriScoreSnapshot.id).limit(1)) is None

            failed_admission = admit_ceri_feature_certification(
                db,
                CeriFeatureCertificationRequest(
                    tickers=("FAIL1", "FAIL2"),
                    cutoff_at=datetime(2026, 9, 25, 22, tzinfo=UTC),
                    request_key="synthetic-feature-certification-failure",
                    requested_by="pytest",
                ),
            )
            failed_root = db.get(BackgroundJob, failed_admission.root_job_id)
            assert failed_root is not None
            failed_root.status = JobStatus.RUNNING
            db.flush()
            with worker_job_scope(failed_root), pytest.raises(JobDeferred):
                execute_ceri_feature_certification_job(db, failed_root)
            failed_root.status = JobStatus.QUEUED
            failed_children = list(
                db.scalars(
                    select(BackgroundJob)
                    .where(BackgroundJob.workflow_key == failed_admission.workflow_key)
                    .where(BackgroundJob.id != failed_root.id)
                    .order_by(BackgroundJob.id)
                )
            )
            failed_child = failed_children[0]
            failed_child.status = JobStatus.FAILED
            failed_child.completed_at = datetime.now(UTC)
            roll_up_ceri_pipeline_job_failure(db, failed_child)
            db.flush()
            failed_pipeline = db.get(PipelineRun, failed_admission.pipeline_run_id)
            assert failed_pipeline is not None
            assert failed_pipeline.status == PipelineStatus.FAILED
            assert failed_root.status == JobStatus.FAILED
            assert all(
                child.status in {JobStatus.FAILED, JobStatus.BLOCKED}
                for child in failed_children
            )
            fenced_child = next(child for child in failed_children if child is not failed_child)
            with pytest.raises(CeriParentPipelineTerminalError):
                require_parent_pipeline_active(db, fenced_child)
            assert prohibited.isdisjoint(
                set(
                    db.scalars(
                        select(BackgroundJob.job_type).where(
                            BackgroundJob.workflow_key == failed_admission.workflow_key
                        )
                    )
                )
            )
            db.rollback()
    finally:
        engine.dispose()
        get_settings.cache_clear()
