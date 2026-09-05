from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.models.tables import WinnerPredictionSnapshot
from app.services.us_market_calendar import (
    first_us_market_open_after,
    is_us_trading_day,
    us_market_session,
)
from app.services.winner_probability.temporal_integrity import (
    ENTRY_TIMING_VALIDATION_VERSION,
    TemporalValidityReason,
    TemporalValidityStatus,
    validate_next_open_timing,
)
from app.services.winner_probability.temporal_validation_service import (
    TemporalQuarantineItem,
    TemporalValidationService,
)

NY = ZoneInfo("America/New_York")


@pytest.mark.parametrize(
    ("local_time", "expected_session"),
    [
        ((8, 0), date(2026, 8, 17)),
        ((9, 29), date(2026, 8, 17)),
        ((9, 30), date(2026, 8, 18)),
        ((9, 31), date(2026, 8, 18)),
        ((13, 0), date(2026, 8, 18)),
        ((16, 0), date(2026, 8, 18)),
        ((16, 14), date(2026, 8, 18)),
        ((16, 30), date(2026, 8, 18)),
    ],
)
def test_next_open_is_strictly_after_decision(local_time, expected_session) -> None:
    hour, minute = local_time
    decision_at = datetime(2026, 8, 17, hour, minute, tzinfo=NY)

    selected = first_us_market_open_after(decision_at)

    assert selected.session == expected_session
    assert decision_at < selected.open_at


def test_intraday_calculation_1_1_regression_run_120_128() -> None:
    decision_at = datetime(2026, 8, 20, 11, 28, tzinfo=NY)

    selected = first_us_market_open_after(decision_at)

    assert selected.session == date(2026, 8, 21)
    assert selected.open_at == datetime(2026, 8, 21, 9, 30, tzinfo=NY)


def test_legacy_calculation_1_0_stale_anchor_cannot_retroactively_select_open() -> None:
    decision_at = datetime(2026, 8, 3, 20, 0, tzinfo=NY)

    selected = first_us_market_open_after(decision_at)

    assert selected.session == date(2026, 8, 4)
    assert decision_at < selected.open_at


def test_weekend_and_observed_cross_year_holiday() -> None:
    saturday = datetime(2026, 8, 15, 12, 0, tzinfo=NY)
    assert first_us_market_open_after(saturday).session == date(2026, 8, 17)
    assert is_us_trading_day(date(2021, 12, 31)) is False
    assert first_us_market_open_after(datetime(2021, 12, 30, 16, 30, tzinfo=NY)).session == date(
        2022, 1, 3
    )


def test_dst_session_uses_exchange_local_open() -> None:
    before_open = datetime(2026, 3, 9, 13, 29, tzinfo=UTC)
    at_open = datetime(2026, 3, 9, 13, 30, tzinfo=UTC)

    assert first_us_market_open_after(before_open).session == date(2026, 3, 9)
    assert first_us_market_open_after(at_open).session == date(2026, 3, 10)


def test_early_close_schedule_is_explicit() -> None:
    session = us_market_session(date(2026, 11, 27))

    assert session is not None
    assert session.close_at == datetime(2026, 11, 27, 13, 0, tzinfo=NY)


def test_temporal_validation_rejects_equality_and_preserves_reason() -> None:
    entry_open = datetime(2026, 8, 17, 9, 30, tzinfo=NY)

    invalid = validate_next_open_timing(entry_open, entry_open)
    valid = validate_next_open_timing(entry_open.astimezone(UTC).replace(minute=29), entry_open)

    assert invalid.status == TemporalValidityStatus.EXECUTION_INVALID
    assert invalid.evidence_eligible is False
    assert invalid.reason_codes == (TemporalValidityReason.ENTRY_NOT_AFTER_DECISION,)
    assert invalid.validation_version == ENTRY_TIMING_VALIDATION_VERSION
    assert valid.status == TemporalValidityStatus.VALID
    assert valid.evidence_eligible is True


def test_unresolved_semantic_lineage_fails_closed_even_with_future_entry() -> None:
    decision = datetime(2026, 8, 17, 8, 0, tzinfo=NY)
    entry_open = datetime(2026, 8, 17, 9, 30, tzinfo=NY)

    result = validate_next_open_timing(
        decision,
        entry_open,
        source_data_cutoff_at=decision,
        semantic_input_time_valid=None,
    )

    assert result.status == TemporalValidityStatus.TEMPORAL_LINEAGE_UNRESOLVED
    assert result.entry_timing_valid is True
    assert result.evidence_eligible is False


def test_source_after_decision_is_lookahead_invalid() -> None:
    decision = datetime(2026, 8, 17, 8, 0, tzinfo=NY)
    entry_open = datetime(2026, 8, 17, 9, 30, tzinfo=NY)

    result = validate_next_open_timing(
        decision,
        entry_open,
        source_data_cutoff_at=decision.replace(hour=8, minute=1),
    )

    assert result.status == TemporalValidityStatus.LOOKAHEAD_INVALID
    assert result.evidence_eligible is False


def test_quarantine_plan_is_deterministic_and_write_requires_reviewed_hash() -> None:
    predictions = [
        WinnerPredictionSnapshot(
            id=prediction_id,
            source_data_cutoff_at=datetime(2026, 8, 19, 20, 0, tzinfo=UTC),
        )
        for prediction_id in (1, 2)
    ]
    db = _PredictionReadDb(predictions)
    items = tuple(
        TemporalQuarantineItem(
            prediction_id=prediction_id,
            decision_at=datetime(2026, 8, 20, 15, 28, tzinfo=UTC),
            entry_session=date(2026, 8, 20),
            semantic_input_time_valid=None,
            incident_reason="PROVEN_RETROACTIVE_NEXT_OPEN",
        )
        for prediction_id in (2, 1)
    )
    service = TemporalValidationService()

    first = service.plan_quarantine(db, items=items)
    second = service.plan_quarantine(db, items=tuple(reversed(items)))

    assert first.manifest_hash == second.manifest_hash
    assert first.item_count == first.invalid_count == 2
    assert [item.prediction_id for item in first.items] == [1, 2]
    with pytest.raises(PermissionError, match="approve_write"):
        service.apply_quarantine(
            db,
            plan=first,
            expected_manifest_hash=first.manifest_hash,
            actor="test",
            request_key="incident-1292",
            approve_write=False,
        )
    assert db.write_count == 0


class _PredictionReadDb:
    def __init__(self, predictions) -> None:
        self.predictions = predictions
        self.write_count = 0

    def scalars(self, _statement):
        return iter(self.predictions)

    def add_all(self, _rows) -> None:
        self.write_count += 1
