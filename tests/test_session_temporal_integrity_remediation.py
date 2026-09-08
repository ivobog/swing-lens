from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.models.tables import MarketCalculationContext, PipelineRun
from app.services.ceri.price_response_service import CeriPriceResponseService
from app.services.ib_market_intelligence.enums import IntelligenceModule
from app.services.ib_market_intelligence.orchestration import _historical_date_ranges
from app.services.market_calculation_context_service import (
    PipelineCalculationContextError,
    resolve_pipeline_market_context,
)
from app.services.market_clock_service import (
    BAR_READINESS_VERSION,
    CALENDAR_VERSION,
    MarketClockService,
    SessionTimestampPolicy,
)
from app.settings import Settings

NY = ZoneInfo("America/New_York")
ZURICH = ZoneInfo("Europe/Zurich")


def _ny(day: date, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=NY)


@pytest.mark.parametrize(
    ("instant", "expected"),
    [
        (_ny(date(2026, 9, 8), 8), date(2026, 9, 4)),
        (_ny(date(2026, 9, 8), 9, 29, 59), date(2026, 9, 4)),
        (_ny(date(2026, 9, 8), 9, 30), date(2026, 9, 4)),
        (_ny(date(2026, 9, 8), 12), date(2026, 9, 4)),
        (_ny(date(2026, 9, 8), 15, 59, 59), date(2026, 9, 4)),
        (_ny(date(2026, 9, 8), 16), date(2026, 9, 4)),
        (_ny(date(2026, 9, 8), 16, 14, 59), date(2026, 9, 4)),
        (_ny(date(2026, 9, 8), 16, 15), date(2026, 9, 8)),
        (_ny(date(2026, 9, 8), 16, 15, 1), date(2026, 9, 8)),
    ],
)
def test_regular_session_cutoff_matrix(instant: datetime, expected: date) -> None:
    cutoff = MarketClockService().cutoff_for(instant, reason="TEST_REGULAR_BOUNDARY")

    assert cutoff.latest_completed_session == expected
    assert cutoff.calendar_version == CALENDAR_VERSION
    assert cutoff.bar_readiness_version == BAR_READINESS_VERSION


@pytest.mark.parametrize(
    ("instant", "expected"),
    [
        (_ny(date(2026, 11, 27), 12, 59, 59), date(2026, 11, 25)),
        (_ny(date(2026, 11, 27), 13), date(2026, 11, 25)),
        (_ny(date(2026, 11, 27), 13, 14, 59), date(2026, 11, 25)),
        (_ny(date(2026, 11, 27), 13, 15), date(2026, 11, 27)),
    ],
)
def test_early_close_cutoff_matrix(instant: datetime, expected: date) -> None:
    cutoff = MarketClockService().cutoff_for(instant, reason="TEST_EARLY_CLOSE")
    assert cutoff.latest_completed_session == expected


@pytest.mark.parametrize(
    ("instant", "expected"),
    [
        (_ny(date(2026, 9, 5), 12), date(2026, 9, 4)),
        (_ny(date(2026, 9, 7), 12), date(2026, 9, 4)),
        (_ny(date(2021, 12, 31), 18), date(2021, 12, 30)),
    ],
)
def test_weekend_and_holiday_cutoffs(instant: datetime, expected: date) -> None:
    assert (
        MarketClockService().cutoff_for(instant, reason="TEST_HOLIDAY").latest_completed_session
        == expected
    )


def test_cutoff_is_immutable_and_requires_aware_time() -> None:
    service = MarketClockService()
    cutoff = service.cutoff_for(_ny(date(2026, 9, 8), 16, 15), reason="TEST_IMMUTABLE")

    with pytest.raises(FrozenInstanceError):
        cutoff.latest_completed_session = date(2026, 9, 9)  # type: ignore[misc]
    with pytest.raises(ValueError, match="timezone-aware"):
        service.cutoff_for(datetime(2026, 9, 8, 16, 15), reason="TEST_NAIVE")


@pytest.mark.parametrize(
    ("event_at", "expected"),
    [
        (_ny(date(2026, 9, 8), 8), date(2026, 9, 8)),
        (_ny(date(2026, 9, 8), 9, 29, 59), date(2026, 9, 8)),
        (_ny(date(2026, 9, 8), 9, 30), date(2026, 9, 9)),
        (_ny(date(2026, 9, 8), 15, 59), date(2026, 9, 9)),
        (_ny(date(2026, 11, 27), 13), date(2026, 11, 30)),
        (_ny(date(2026, 9, 12), 12), date(2026, 9, 14)),
    ],
)
def test_versioned_daily_reaction_policy(event_at: datetime, expected: date) -> None:
    reaction = MarketClockService().canonical_session_for_timestamp(
        event_at, policy=SessionTimestampPolicy.NEXT_MARKET_OPEN_AFTER_EVENT
    )
    assert reaction == expected


def test_date_only_ceri_event_fails_closed() -> None:
    assert CeriPriceResponseService().reaction_session(None, date(2026, 9, 8)) is None


def test_session_distance_ignores_weekends_and_holidays() -> None:
    service = MarketClockService()
    assert service.trading_session_distance(date(2026, 9, 4), date(2026, 9, 8)) == 1
    assert service.trading_session_distance(date(2026, 9, 8), date(2026, 9, 4)) == -1


@pytest.mark.parametrize(
    "execution_at",
    [
        _ny(date(2026, 9, 8), 9, 30),  # US market open
        _ny(date(2026, 9, 8), 16),  # US market close
        _ny(date(2026, 9, 8), 16, 15),  # daily bar ready
        datetime(2026, 9, 9, 0, 0, tzinfo=UTC),  # UTC midnight
        datetime(2026, 9, 9, 0, 0, tzinfo=ZURICH),  # Zurich midnight
        _ny(date(2026, 9, 9), 0),  # New York midnight
        _ny(date(2026, 9, 9), 17),  # next completed US session
        _ny(date(2026, 9, 12), 12),  # weekend
        _ny(date(2026, 11, 26), 12),  # holiday
        _ny(date(2026, 10, 30), 12),  # US/Europe DST offset-gap week
    ],
    ids=[
        "market-open",
        "market-close",
        "daily-bar-ready",
        "utc-midnight",
        "zurich-midnight",
        "new-york-midnight",
        "next-session",
        "weekend",
        "holiday",
        "dst-offset-gap",
    ],
)
def test_serialized_pipeline_context_survives_all_execution_boundaries(
    execution_at: datetime,
) -> None:
    original = (
        MarketClockService()
        .cutoff_for(
            _ny(date(2026, 9, 8), 6, 6, 38),
            reason="FULL_PIPELINE_FROZEN_AT_ENQUEUE",
        )
        .with_context_id(42)
    )
    payload = json.loads(
        json.dumps(
            {
                "calculation_context_id": original.context_id,
                "cutoff_at": original.cutoff_at.isoformat(),
                "as_of_session": original.latest_completed_session.isoformat(),
                "calendar_version": original.calendar_version,
            }
        )
    )
    db = _context_db(original)

    resolved = resolve_pipeline_market_context(
        db,
        calculation_context_id=payload["calculation_context_id"],
        upload_run_id=7,
        expected_cutoff_at=datetime.fromisoformat(payload["cutoff_at"]),
        expected_latest_completed_session=date.fromisoformat(payload["as_of_session"]),
        expected_calendar_version=payload["calendar_version"],
    )
    wall_clock_context = MarketClockService().cutoff_for(
        execution_at,
        reason="DELAYED_EXECUTION_WOULD_BE_WRONG",
    )

    assert execution_at > original.cutoff_at
    assert wall_clock_context.cutoff_at != original.cutoff_at
    assert resolved == original


def test_pipeline_context_resolution_fails_closed_for_wrong_pipeline() -> None:
    original = (
        MarketClockService()
        .cutoff_for(_ny(date(2026, 9, 8), 6), reason="FULL_PIPELINE_FROZEN_AT_ENQUEUE")
        .with_context_id(42)
    )

    with pytest.raises(PipelineCalculationContextError, match="not pipeline 99"):
        resolve_pipeline_market_context(
            _context_db(original),
            calculation_context_id=42,
            upload_run_id=7,
            pipeline_run_id=99,
        )


def test_pipeline_context_resolution_fails_closed_for_payload_mismatch() -> None:
    original = (
        MarketClockService()
        .cutoff_for(_ny(date(2026, 9, 8), 6), reason="FULL_PIPELINE_FROZEN_AT_ENQUEUE")
        .with_context_id(42)
    )

    with pytest.raises(PipelineCalculationContextError, match="Payload cutoff_at"):
        resolve_pipeline_market_context(
            _context_db(original),
            calculation_context_id=42,
            upload_run_id=7,
            expected_cutoff_at=_ny(date(2026, 9, 9), 6),
        )


def test_ibmi_semantic_range_chunks_have_session_boundaries() -> None:
    settings = Settings(
        _env_file=None,
        job_worker_enabled=False,
        ib_intelligence_historical_chunk_days=3,
    )

    ranges = _historical_date_ranges(
        {"start_date": "2026-09-05", "end_date": "2026-09-13"},
        IntelligenceModule.LIQUIDITY,
        settings,
    )

    assert ranges == [
        (date(2026, 9, 8), date(2026, 9, 10)),
        (date(2026, 9, 11), date(2026, 9, 11)),
    ]


def _context_db(cutoff):
    row = MarketCalculationContext(
        id=42,
        pipeline_run_id=3,
        upload_run_id=7,
        cutoff_at=cutoff.cutoff_at,
        exchange_timezone=cutoff.exchange_timezone,
        latest_completed_session=cutoff.latest_completed_session,
        daily_bar_ready_at=cutoff.daily_bar_ready_at,
        calendar_version=cutoff.calendar_version,
        bar_readiness_version=cutoff.bar_readiness_version,
        cutoff_reason=cutoff.cutoff_reason,
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
