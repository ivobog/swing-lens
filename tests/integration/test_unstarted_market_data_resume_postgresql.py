from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from alembic import command
from app.database_safety import configure_guarded_alembic
from app.models.tables import BackgroundJob, IBFetchRun, PipelineRun, UploadRun
from app.services import pipeline_executor
from app.services.ib_fetch_job_service import IB_FETCH_JOB_TYPE
from app.services.scope_refresh_adoption import admit_frozen_operation, bind_semantic_authority
from app.services.work_scope_identity import AcquisitionRequirement, ScopeMember


def test_unstarted_resume_rejects_real_durable_acquisition_facts(
    disposable_postgres_database, monkeypatch,
):
    """Missing JSON must not permit replacement of an existing native acquisition."""
    config = Config("alembic.ini")
    configure_guarded_alembic(config, disposable_postgres_database)
    command.upgrade(config, "head")
    engine = create_engine(disposable_postgres_database)
    expected_session = date(2026, 11, 9)
    monkeypatch.setattr(
        pipeline_executor, "latest_completed_us_trading_day", lambda: expected_session,
    )
    cutoff = SimpleNamespace(
        latest_completed_session=expected_session, context_id=100,
        cutoff_at=datetime(2026, 11, 9, 22, tzinfo=UTC),
    )
    with Session(engine) as db:
        run = UploadRun(filename="unstarted.csv", row_count=1, status="COMPLETED")
        db.add(run)
        db.flush()
        authority = admit_frozen_operation(
            db, operation_kind="full-pipeline-run", subject_kind="ticker",
            members=(ScopeMember("TICKER", "MSFT"),), cycle_key="unstarted-pipeline-test",
            business_cutoff=datetime(2026, 11, 9, 22, tzinfo=UTC),
            provider_source_class="PIPELINE_INPUTS", request_type="FULL_PIPELINE",
            requirements=(AcquisitionRequirement("FETCHING_MARKET_DATA"),),
            policy_identity="pytest-unstarted-resume",
            scope_definition={"upload_run_id": run.id},
        )
        pipeline = PipelineRun(
            upload_run_id=run.id, status="QUEUED", current_step="FETCHING_MARKET_DATA",
            result_json={
                "ib_api_available_at_execution": False,
                "ib_execution_error_code": "IB_GATEWAY_API_UNREACHABLE",
                "ib_planned_requests": 0, "ib_executed_requests": 0,
            },
        )
        bind_semantic_authority(pipeline, authority)
        db.add(pipeline)
        db.flush()
        def validate():
            return pipeline_executor._validate_unstarted_market_data_resume(
                db, pipeline, ["MSFT"], cutoff,
            )

        validate()
        # The parent pipeline itself already has a plan/scope. Only an actual
        # child acquisition scope proves that market acquisition was admitted.
        with db.begin_nested() as nested:
            admit_frozen_operation(
                db, operation_kind="ib-price-acquisition", subject_kind="ticker",
                members=(ScopeMember("TICKER", "MSFT"),), cycle_key="existing-fetch-scope",
                business_cutoff=expected_session, provider_source_class="INTERACTIVE_BROKERS",
                request_type="HISTORICAL_BARS",
                requirements=(AcquisitionRequirement("MSFT:TRADES"),),
                policy_identity="pytest-existing-fetch", scope_definition={"run_id": run.id},
                parent_scope_id=authority.scope_id,
            )
            with pytest.raises(ValueError, match="MARKET_DATA_RESUME_ACQUISITION_ALREADY_ADMITTED"):
                validate()
            nested.rollback()
        validate()
        with db.begin_nested() as nested:
            db.add(BackgroundJob(
                job_type=IB_FETCH_JOB_TYPE, related_run_id=run.id, status="FAILED", payload_json={},
            ))
            db.flush()
            with pytest.raises(ValueError, match="MARKET_DATA_RESUME_ACQUISITION_ALREADY_ADMITTED"):
                validate()
            nested.rollback()
        validate()
        with db.begin_nested() as nested:
            db.add(IBFetchRun(run_id=run.id, requested_tickers=["MSFT"], status="FAILED"))
            db.flush()
            with pytest.raises(ValueError, match="MARKET_DATA_RESUME_ACQUISITION_ALREADY_ADMITTED"):
                validate()
            nested.rollback()
        validate()
        assert pipeline.scope_id == authority.scope_id
        assert pipeline.refresh_cycle_id == authority.refresh_cycle_id
        assert pipeline.acquisition_plan_id == authority.acquisition_plan_id
        assert pipeline.result_json["ib_planned_requests"] == 0
        db.rollback()
    engine.dispose()
