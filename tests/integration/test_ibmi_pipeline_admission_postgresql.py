"""Persist the actual CERI continuation target even when Winner is disabled."""

from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database_safety import run_guarded_alembic_upgrade
from app.models.tables import PipelineStep, RawCompanyRow, UploadRun
from app.services.pipeline_service import DECISION_HANDOFF_PIPELINE_STEP, start_pipeline
from app.settings import Settings, get_settings


def test_native_admission_retains_real_handoff_with_winner_disabled(
    disposable_postgres_database,
    monkeypatch,
):
    run_guarded_alembic_upgrade(Config("alembic.ini"), disposable_postgres_database)
    for key, value in {
        "CERI_ENABLED": "true",
        "CERI_RUN_CAPTURE_ENABLED": "true",
        "CERI_PROVIDER_INGEST_ENABLED": "true",
        "CERI_BATCHED_WORKFLOW_ENABLED": "true",
        "CERI_LEGACY_PIPELINE_SCHEDULING_ENABLED": "false",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    settings = Settings(
        _env_file=None,
        database_url=disposable_postgres_database,
        ceri_enabled=True,
        ceri_provider_ingest_enabled=True,
        ceri_run_capture_enabled=True,
        ceri_batched_workflow_enabled=True,
        ceri_legacy_pipeline_scheduling_enabled=False,
        setup_lifecycle_pipeline_step_enabled=True,
        winner_probability_capture_in_pipeline=False,
    )
    monkeypatch.setattr("app.services.pipeline_service.get_settings", lambda: settings)
    engine = create_engine(disposable_postgres_database)
    try:
        with Session(engine) as db:
            upload = UploadRun(filename="continuation.csv", status="COMPLETED")
            db.add(upload)
            db.flush()
            db.add(
                RawCompanyRow(
                    run_id=upload.id,
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
                upload.id,
                ceri_provider_ingest_enabled=True,
                setup_lifecycle_pipeline_step_enabled=True,
            )
            pipeline_id = pipeline.id
            db.commit()
        with Session(engine) as db:
            steps = list(
                db.scalars(
                    select(PipelineStep)
                    .where(PipelineStep.pipeline_run_id == pipeline_id)
                    .order_by(PipelineStep.step_order)
                )
            )
            names = [row.step_name for row in steps]
            assert names[names.index("CERI_PROVIDER_INGEST") + 1] == DECISION_HANDOFF_PIPELINE_STEP
            assert names.index(DECISION_HANDOFF_PIPELINE_STEP) < names.index(
                "CAPTURING_SETUP_SIGNALS"
            )
            assert names.index("EVALUATING_SETUP_LIFECYCLES") < names.index(
                "CAPTURING_WINNER_PREDICTIONS"
            )
            assert names.count(DECISION_HANDOFF_PIPELINE_STEP) == 1
            assert all(row.status == "PENDING" for row in steps)
    finally:
        engine.dispose()
        get_settings.cache_clear()
