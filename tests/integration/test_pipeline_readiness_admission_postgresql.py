from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from alembic import command
from app.database_safety import configure_guarded_alembic
from app.models.tables import (
    AcquisitionPlanRecord,
    MarketCalculationContext,
    MarketDataSessionDisposition,
    PipelineRun,
    TransitionDecisionHandoffManifest,
    UploadRun,
)
from app.services.market_data_session_readiness import load_current_session_readiness
from app.services.transition_preflight_plan_service import (
    TransitionPreflightError,
    _pipeline_downstream_tickers,
)

SESSION = date(2026, 10, 6)


@pytest.fixture
def admission_engine(disposable_postgres_database):
    config = Config("alembic.ini")
    configure_guarded_alembic(config, disposable_postgres_database)
    command.upgrade(config, "head")
    from sqlalchemy import create_engine

    engine = create_engine(disposable_postgres_database)
    try:
        yield engine
    finally:
        engine.dispose()


def test_unresolved_readiness_blocks_handoff_and_preserves_durable_evidence(
    admission_engine,
) -> None:
    with Session(admission_engine) as db:
        run, pipeline, context = _seed_pipeline(db)
        blocker = _disposition(
            pipeline,
            run,
            context,
            ticker="WAIT",
            disposition="TRANSIENT_FAILURE",
            lifecycle_state="UNKNOWN",
            eligible=False,
        )
        db.add(blocker)
        db.commit()

        with pytest.raises(TransitionPreflightError) as error:
            _pipeline_downstream_tickers(db, pipeline_run_id=pipeline.id)

        assert error.value.code == "MARKET_DATA_SESSION_READINESS_BLOCKED"
        persisted = db.scalar(
            select(MarketDataSessionDisposition).where(
                MarketDataSessionDisposition.pipeline_run_id == pipeline.id,
                MarketDataSessionDisposition.ticker == "WAIT",
            )
        )
        assert persisted is not None
        assert persisted.disposition == "TRANSIENT_FAILURE"
        assert db.scalar(select(func.count(TransitionDecisionHandoffManifest.id))) == 0


def test_all_terminal_inactive_readiness_has_empty_handoff_population(
    admission_engine,
) -> None:
    with Session(admission_engine) as db:
        run, pipeline, context = _seed_pipeline(db)
        db.add_all(
            [
                _disposition(
                    pipeline,
                    run,
                    context,
                    ticker="OLD",
                    disposition="TERMINAL_INACTIVE",
                    lifecycle_state="DELISTED",
                    eligible=False,
                ),
                _disposition(
                    pipeline,
                    run,
                    context,
                    ticker="MERGED",
                    disposition="TERMINAL_INACTIVE",
                    lifecycle_state="MERGED",
                    eligible=False,
                ),
            ]
        )
        db.commit()

        assert _pipeline_downstream_tickers(db, pipeline_run_id=pipeline.id) == set()


def test_mixed_readiness_filters_handoff_to_exact_downstream_eligible_set(
    admission_engine,
) -> None:
    with Session(admission_engine) as db:
        run, pipeline, context = _seed_pipeline(db)
        db.add_all(
            [
                _disposition(
                    pipeline,
                    run,
                    context,
                    ticker="READY_A",
                    disposition="READY",
                    lifecycle_state="ACTIVE",
                    eligible=True,
                ),
                _disposition(
                    pipeline,
                    run,
                    context,
                    ticker="READY_B",
                    disposition="READY",
                    lifecycle_state="UNKNOWN",
                    eligible=True,
                ),
                _disposition(
                    pipeline,
                    run,
                    context,
                    ticker="OLD",
                    disposition="TERMINAL_INACTIVE",
                    lifecycle_state="DELISTED",
                    eligible=False,
                ),
            ]
        )
        db.commit()

        assert _pipeline_downstream_tickers(db, pipeline_run_id=pipeline.id) == {
            "READY_A",
            "READY_B",
        }


def test_readiness_reload_and_handoff_filter_are_pipeline_isolated(admission_engine) -> None:
    with Session(admission_engine) as db:
        run_a, pipeline_a, context_a = _seed_pipeline(db)
        run_b, pipeline_b, context_b = _seed_pipeline(db)
        db.add_all(
            [
                _disposition(
                    pipeline_a,
                    run_a,
                    context_a,
                    ticker="A_ONLY",
                    disposition="READY",
                    lifecycle_state="ACTIVE",
                    eligible=True,
                ),
                _disposition(
                    pipeline_b,
                    run_b,
                    context_b,
                    ticker="B_ONLY",
                    disposition="READY",
                    lifecycle_state="ACTIVE",
                    eligible=True,
                ),
                _disposition(
                    pipeline_b,
                    run_b,
                    context_b,
                    ticker="B_BLOCKER",
                    disposition="REQUIRED_DATA_UNAVAILABLE",
                    lifecycle_state="ACTIVE",
                    eligible=False,
                ),
            ]
        )
        db.commit()
        pipeline_a_id = pipeline_a.id
        pipeline_b_id = pipeline_b.id

    with Session(admission_engine) as resumed:
        reloaded_a = load_current_session_readiness(resumed, pipeline_run_id=pipeline_a_id)
        assert reloaded_a is not None
        assert [row.ticker for row in reloaded_a.rows] == ["A_ONLY"]
        assert reloaded_a.downstream_tickers == ("A_ONLY",)
        assert _pipeline_downstream_tickers(resumed, pipeline_run_id=pipeline_a_id) == {
            "A_ONLY"
        }
        with pytest.raises(TransitionPreflightError) as error:
            _pipeline_downstream_tickers(resumed, pipeline_run_id=pipeline_b_id)
        assert error.value.code == "MARKET_DATA_SESSION_READINESS_BLOCKED"


def _seed_pipeline(db: Session):
    plan = AcquisitionPlanRecord(
        plan_id=("g5-admission-" + uuid.uuid4().hex).ljust(64, "0")[:64],
        plan_kind="G5_ADMISSION_CERTIFICATION",
        plan_version="v1",
        payload_json={"source": "g5-direct-regression"},
    )
    run = UploadRun(filename="g5-admission.csv", row_count=3, status="COMPLETED")
    db.add_all([plan, run])
    db.flush()
    pipeline = PipelineRun(
        upload_run_id=run.id,
        acquisition_plan_id=plan.plan_id,
        status="PREPARING",
    )
    db.add(pipeline)
    db.flush()
    context = MarketCalculationContext(
        pipeline_run_id=pipeline.id,
        upload_run_id=run.id,
        cutoff_at=datetime(2026, 10, 6, 22, 0, tzinfo=UTC),
        exchange_timezone="America/New_York",
        latest_completed_session=SESSION,
        calendar_version="g5-certification-v1",
        bar_readiness_version="g5-certification-v1",
        cutoff_reason="G5_ADMISSION_CERTIFICATION",
    )
    db.add(context)
    db.flush()
    return run, pipeline, context


def _disposition(
    pipeline: PipelineRun,
    run: UploadRun,
    context: MarketCalculationContext,
    *,
    ticker: str,
    disposition: str,
    lifecycle_state: str,
    eligible: bool,
) -> MarketDataSessionDisposition:
    return MarketDataSessionDisposition(
        pipeline_run_id=pipeline.id,
        upload_run_id=run.id,
        market_calculation_context_id=context.id,
        ticker=ticker,
        expected_session=SESSION,
        latest_bar_session=SESSION if eligible else None,
        disposition=disposition,
        lifecycle_state=lifecycle_state,
        technical_eligible=eligible,
        downstream_eligible=eligible,
        reason_code="G5_DIRECT_REGRESSION",
        reason_message="Direct pipeline admission certification fixture",
        evidence_json={"source": "g5-direct-regression"},
        revision=1,
        is_current_revision=True,
    )
