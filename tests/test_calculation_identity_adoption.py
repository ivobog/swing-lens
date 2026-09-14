from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest

from app.models.tables import MarketCalculationContext, PipelineRun
from app.services.market_calculation_context_service import (
    PipelineCalculationContextError,
    calculation_identity_from_market_context,
    validate_pipeline_job_market_context,
)
from app.services.market_clock_service import MarketClockService


def _frozen_context():
    return (
        MarketClockService()
        .cutoff_for(
            datetime(2026, 9, 10, 20, tzinfo=UTC),
            reason="FULL_PIPELINE_FROZEN_AT_ENQUEUE",
        )
        .with_context_id(42)
    )


def _payload(context) -> dict[str, object]:
    return {
        "market_calculation_context_id": context.context_id,
        "market_cutoff_at": context.cutoff_at.isoformat(),
        "input_as_of_session": context.latest_completed_session.isoformat(),
        "market_calendar_version": context.calendar_version,
        "bar_readiness_version": context.bar_readiness_version,
    }


def _context_db(context):
    row = MarketCalculationContext(
        id=context.context_id,
        pipeline_run_id=3,
        upload_run_id=7,
        cutoff_at=context.cutoff_at,
        exchange_timezone=context.exchange_timezone,
        latest_completed_session=context.latest_completed_session,
        daily_bar_ready_at=context.daily_bar_ready_at,
        calendar_version=context.calendar_version,
        bar_readiness_version=context.bar_readiness_version,
        cutoff_reason=context.cutoff_reason,
    )
    pipeline = PipelineRun(id=3, upload_run_id=7, status="RUNNING")

    class ContextDb:
        def get(self, model, identity):
            if model is MarketCalculationContext and identity == 42:
                return row
            if model is PipelineRun and identity == 3:
                return pipeline
            return None

        def scalar(self, _statement):
            return 42

    return ContextDb()


def test_market_context_producer_creates_valid_calculation_identity() -> None:
    identity = calculation_identity_from_market_context(
        _frozen_context(),
        run_id=7,
        pipeline_id=3,
        ticker="aapl",
    )

    assert identity.validate().valid
    assert identity.subject.ticker.value == "AAPL"
    assert len(identity.fingerprint().value) == 64


def test_pipeline_job_consumer_accepts_compatible_identity(caplog) -> None:
    context = _frozen_context()
    caplog.set_level(logging.INFO)

    resolved = validate_pipeline_job_market_context(
        _context_db(context),
        pipeline_run_id=3,
        payload=_payload(context),
    )

    assert resolved == context
    record = next(
        record
        for record in caplog.records
        if record.message == "pipeline calculation identity compatibility"
    )
    assert record.calculation_identity_policy == "PIPELINE_CONTEXT_COMPATIBILITY"
    assert record.calculation_identity_result == "COMPATIBLE"
    assert len(record.calculation_identity_fingerprint) == 64


def test_pipeline_job_consumer_rejects_one_dimension_mismatch() -> None:
    context = _frozen_context()
    payload = _payload(context)
    payload["market_calendar_version"] = "swinglens-us-equities-v2"

    with pytest.raises(
        PipelineCalculationContextError,
        match=(
            "dimension=temporal.calendar.*"
            "policy=PIPELINE_CONTEXT_COMPATIBILITY"
        ),
    ):
        validate_pipeline_job_market_context(
            _context_db(context),
            pipeline_run_id=3,
            payload=payload,
        )
