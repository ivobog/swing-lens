from datetime import date

import pytest

from app.services.ib_historical_request_scope import (
    HistoricalEndMode,
    HistoricalRequestScope,
    build_historical_request_scope,
    validate_reviewed_session_current,
)


@pytest.mark.parametrize(
    ("duration", "end", "expected_start"),
    [
        ("5 D", date(2026, 8, 10), date(2026, 8, 4)),  # weekend
        ("2 D", date(2026, 7, 6), date(2026, 7, 2)),  # observed July 4 closure
        ("3 D", date(2026, 8, 3), date(2026, 7, 30)),  # month boundary
        ("2 D", date(2026, 3, 9), date(2026, 3, 6)),  # DST weekend
        ("2 D", date(2026, 11, 27), date(2026, 11, 25)),  # early close
        ("2 D", date(2022, 1, 3), date(2021, 12, 30)),  # observed New Year
        ("40 D", date(2026, 9, 4), date(2026, 7, 13)),  # run-190 boundary
    ],
)
def test_daily_duration_review_scope_uses_us_market_sessions(
    duration: str,
    end: date,
    expected_start: date,
) -> None:
    scope = build_historical_request_scope(
        required_start_date=end,
        required_end_date=end,
        duration=duration,
        bar_size="1 day",
        what_to_show="TRADES",
    )

    assert scope.reviewed_start_date == expected_start
    assert scope.reviewed_end_date == end


def test_scope_keeps_required_range_distinct_from_provider_footprint() -> None:
    scope = build_historical_request_scope(
        required_start_date=date(2026, 8, 20),
        required_end_date=date(2026, 9, 4),
        duration="20 D",
        bar_size="1 day",
        what_to_show="ADJUSTED_LAST",
    )

    assert scope.required_start_date == date(2026, 8, 20)
    assert scope.required_end_date == date(2026, 9, 4)
    assert scope.reviewed_start_date == date(2026, 8, 10)
    assert scope.reviewed_end_date == date(2026, 9, 4)


def test_scope_has_explicit_deterministic_ib_end_datetime() -> None:
    scope = build_historical_request_scope(
        required_start_date=date(2026, 8, 20),
        required_end_date=date(2026, 9, 4),
        duration="20 D",
        bar_size="1 day",
        what_to_show="TRADES",
    )

    assert scope.end_datetime == "20260904-23:59:59"
    assert scope.end_mode == HistoricalEndMode.EXPLICIT
    assert scope.to_dict() == {
        "required_start_date": "2026-08-20",
        "required_end_date": "2026-09-04",
        "reviewed_start_date": "2026-08-10",
        "reviewed_end_date": "2026-09-04",
        "duration": "20 D",
        "bar_size": "1 day",
        "what_to_show": "TRADES",
        "end_datetime": "20260904-23:59:59",
        "end_mode": "EXPLICIT",
        "reviewed_session_expiry": None,
    }


def test_adjusted_last_uses_current_end_with_reviewed_session_expiry() -> None:
    scope = build_historical_request_scope(
        required_start_date=date(2026, 8, 20),
        required_end_date=date(2026, 9, 4),
        duration="20 D",
        bar_size="1 day",
        what_to_show="ADJUSTED_LAST",
    )

    assert scope.end_mode == HistoricalEndMode.CURRENT
    assert scope.end_datetime == ""
    assert scope.reviewed_session_expiry == date(2026, 9, 4)
    validate_reviewed_session_current(scope, latest_completed_session=date(2026, 9, 4))
    with pytest.raises(ValueError, match="expired"):
        validate_reviewed_session_current(scope, latest_completed_session=date(2026, 9, 8))


def test_scope_rejects_non_session_daily_end() -> None:
    with pytest.raises(ValueError, match="trading session"):
        build_historical_request_scope(
            required_start_date=date(2026, 9, 4),
            required_end_date=date(2026, 9, 5),
            duration="2 D",
            bar_size="1 day",
            what_to_show="TRADES",
        )


def test_scope_dataclass_is_immutable() -> None:
    scope = HistoricalRequestScope(
        required_start_date=date(2026, 9, 4),
        required_end_date=date(2026, 9, 4),
        reviewed_start_date=date(2026, 9, 4),
        reviewed_end_date=date(2026, 9, 4),
        duration="1 D",
        bar_size="1 day",
        what_to_show="TRADES",
        end_datetime="20260904-23:59:59",
        end_mode=HistoricalEndMode.EXPLICIT,
        reviewed_session_expiry=None,
    )

    with pytest.raises(AttributeError):
        scope.duration = "2 D"  # type: ignore[misc]
