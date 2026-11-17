"""Initial acquisition checkpoints agree with committed native admission facts."""

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from alembic import command
from app.database_safety import configure_guarded_alembic
from app.models.tables import (
    FundamentalScore,
    IBFetchRun,
    PipelineRun,
    PipelineStep,
    UploadRun,
    WorkScopeRecord,
)
from app.services import combined_ranking_identity, pipeline_executor
from app.services.ib_fetch_plan_service import FetchAction, FetchPlan, FetchPlanItem
from app.services.market_calculation_context_service import calculation_identity_from_market_context
from app.services.market_clock_service import MarketCalculationCutoff
from app.services.scope_refresh_adoption import admit_frozen_operation, bind_semantic_authority
from app.services.work_scope_identity import AcquisitionRequirement, ScopeMember


@pytest.mark.parametrize("failure_boundary", [
    "prewarm_read", "before_acquisition_commit", "admission_commit",
])
def test_auxiliary_failure_cannot_poison_initial_admission_retry(
    disposable_postgres_database, monkeypatch, failure_boundary,
):
    config = Config("alembic.ini")
    configure_guarded_alembic(config, disposable_postgres_database)
    command.upgrade(config, "head")
    engine = create_engine(disposable_postgres_database)
    expected_session = date(2026, 11, 9)
    cutoff = MarketCalculationCutoff(
        cutoff_at=datetime(2026, 11, 9, 22, tzinfo=UTC),
        exchange_timezone="America/New_York", latest_completed_session=expected_session,
        daily_bar_ready_at=None, calendar_version="durability-test",
        bar_readiness_version="durability-test", cutoff_reason="durability-test", context_id=100,
    )
    monkeypatch.setattr(
        pipeline_executor, "latest_completed_us_trading_day", lambda: expected_session,
    )
    # This regression isolates the admission transaction. Fundamental evidence
    # is already certified; its presence and real resume control flow remain in use.
    monkeypatch.setattr(pipeline_executor, "_require_unchanged_core_evidence", lambda *a, **k: None)
    def fundamental_identity(row):
        return calculation_identity_from_market_context(
            cutoff, run_id=row.run_id, pipeline_id=pipeline_id, ticker=row.ticker,
        )
    monkeypatch.setattr(
        combined_ranking_identity, "fundamental_score_identity", fundamental_identity,
    )

    def prewarm_context(*args):
        if failure_boundary == "prewarm_read":
            raise RuntimeError("Auxiliary admission boundary failure")
        return SimpleNamespace(
            covered_tickers=(), fetched_tickers=(), age_seconds=None, job_id=None,
        )
    monkeypatch.setattr(pipeline_executor, "resolve_pipeline_prewarm_context", prewarm_context)

    def progress(_db, **values):
        if (
            failure_boundary == "before_acquisition_commit"
            and values.get("stage") == "WAITING_FOR_IB_ACQUISITION"
        ):
            raise RuntimeError("Auxiliary admission boundary failure")

    def no_broker_calls(*args, **kwargs):
        pytest.fail("The injected failure must occur before any broker work")

    try:
        with Session(engine) as db:
            run = UploadRun(filename="admission-durability.csv", row_count=1, status="COMPLETED")
            db.add(run)
            db.flush()
            authority = admit_frozen_operation(
                db, operation_kind="full-pipeline-run", subject_kind="ticker",
                members=(ScopeMember("TICKER", "MSFT"),), cycle_key="admission-durability-test",
                business_cutoff=cutoff.cutoff_at, provider_source_class="PIPELINE_INPUTS",
                request_type="FULL_PIPELINE",
                requirements=(AcquisitionRequirement("FETCHING_MARKET_DATA"),),
                policy_identity="pytest-admission-durability",
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
            pipeline_id = pipeline.id
            db.add(FundamentalScore(run_id=run.id, ticker="MSFT"))
            for order, name in enumerate((
                "VALIDATING_RUN", "SCORING_FUNDAMENTALS", "FETCHING_MARKET_DATA",
            )):
                db.add(PipelineStep(
                    pipeline_run_id=pipeline_id, step_order=order, step_name=name,
                    status="COMPLETED" if order < 2 else "FAILED",
                ))
            db.commit()
            plan = FetchPlan(
                run_id=run.id, requested_tickers=["MSFT"], symbols_including_benchmarks=["MSFT"],
                items=[FetchPlanItem(
                    ticker="MSFT", contract_status="RESOLVED", what_to_show="TRADES",
                    action=FetchAction.TOP_UP_RECENT, duration="2 D", bar_size="1 day",
                    current_bar_count=300, first_bar_date=date(2025, 1, 2),
                    latest_bar_date=date(2026, 11, 6), required_bars=252,
                    reason="Frozen-session request", estimated_request_count=1,
                    freshness_threshold_date=expected_session,
                    request_end_date=expected_session, request_end_mode="CURRENT",
                    reviewed_session_expiry=expected_session,
                )],
                estimated_request_count=1, estimated_full_backfills=0,
                estimated_top_ups=1, estimated_refreshes=0, estimated_skips=0, warnings=[],
            )
            dependencies = pipeline_executor.PipelineExecutionDependencies(
                market_cutoff=cutoff, build_fetch_plan=lambda **kwargs: plan,
                check_ib_gateway=lambda: SimpleNamespace(
                    status="IB_API_READY", api_connected=True, checked_at=datetime.now(UTC),
                    error_code=None, message="ready",
                ),
                check_ib_historical_capability=no_broker_calls, execute_fetch_plan=no_broker_calls,
            )
            if failure_boundary == "admission_commit":
                commit = db.commit
                interrupted = False
                def fail_admission_commit_once():
                    nonlocal interrupted
                    if not interrupted and (pipeline.result_json or {}).get("ib_fetch_plan"):
                        interrupted = True
                        raise RuntimeError("Auxiliary admission boundary failure")
                    commit()
                monkeypatch.setattr(db, "commit", fail_admission_commit_once)
            # Configuration delivery is separate from this transaction contract.
            # The function body and native rollback/transition paths are unchanged.
            with pytest.raises(RuntimeError, match="Auxiliary admission boundary failure"):
                pipeline_executor.execute_full_pipeline.__wrapped__(
                    db, pipeline_run_id=pipeline_id, resume_from_step="FETCHING_MARKET_DATA",
                    dependencies=dependencies, progress_callback=progress,
                )
            db.expire_all()
            persisted = db.get(PipelineRun, pipeline_id)
            saved = persisted.result_json
            child_scope = db.scalar(select(WorkScopeRecord.scope_id).where(
                WorkScopeRecord.parent_scope_id == authority.scope_id,
                WorkScopeRecord.scope_kind == "ib-price-acquisition",
            ))
            assert persisted.status == "FAILED"
            assert db.scalar(select(IBFetchRun.id).where(IBFetchRun.run_id == run.id)) is None
            assert bool(saved.get("ib_fetch_plan")) == bool(child_scope)
            assert bool(saved.get("ib_fetch_authority")) == bool(child_scope)
            if failure_boundary == "admission_commit":
                assert child_scope is None
                assert saved["market_data_resume_checkpoint"].get("first_plan_admitted") is not True
                assert saved["ib_planned_requests"] == 0
            else:
                assert child_scope is not None
                assert saved["market_data_resume_checkpoint"]["first_plan_admitted"] is True
            print({
                "failure_boundary": failure_boundary,
                "durable_plan": saved.get("ib_fetch_plan") is not None,
                "durable_authority": saved.get("ib_fetch_authority") is not None,
                "durable_child_scope": child_scope is not None,
                "first_plan_admitted": saved["market_data_resume_checkpoint"].get(
                    "first_plan_admitted", False,
                ),
                "planned_requests": saved["ib_planned_requests"],
                "executed_requests": saved["ib_executed_requests"],
            })
            # Either admission committed atomically and the exact retained plan
            # is reusable, or it rolled back and a first admission remains valid.
            checkpoint = pipeline_executor._validate_market_data_resume(
                db, persisted, ["MSFT"], cutoff,
            )
            assert checkpoint["fetch_plan_state"] == ("RETAINED" if child_scope else "NOT_ADMITTED")
    finally:
        engine.dispose()
