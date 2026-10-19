from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from enum import StrEnum

from app.services.us_market_calendar import (
    is_us_trading_day,
    subtract_us_trading_sessions,
)

STRICT_RESPONSE_V1 = "ib-response-strict-v1"
DAILY_BAR_ADMISSION_V2 = "ib-daily-reviewed-subset-v2"


class HistoricalEndMode(StrEnum):
    EXPLICIT = "EXPLICIT"
    CURRENT = "CURRENT"


@dataclass(frozen=True)
class HistoricalRequestScope:
    """Authorized persistence interval, distinct from the provider's response."""

    required_start_date: date | None
    required_end_date: date | None
    reviewed_start_date: date
    reviewed_end_date: date
    duration: str
    bar_size: str
    what_to_show: str
    end_datetime: str
    end_mode: HistoricalEndMode
    reviewed_session_expiry: date | None
    response_admission_policy: str = DAILY_BAR_ADMISSION_V2

    def to_dict(self) -> dict[str, object]:
        return {
            key: (
                value.isoformat()
                if isinstance(value, date)
                else value.value
                if isinstance(value, StrEnum)
                else value
            )
            for key, value in asdict(self).items()
        }


def build_historical_request_scope(
    *,
    required_start_date: date | None,
    required_end_date: date | None,
    duration: str,
    bar_size: str,
    what_to_show: str,
    reviewed_end_date: date | None = None,
    response_admission_policy: str = DAILY_BAR_ADMISSION_V2,
) -> HistoricalRequestScope:
    """Freeze the shared persistence interval; duration does not bound raw returns.

    The daily session calculation defines admitted coverage, not a promise about
    IB's earliest returned observation. V2 discards earlier observations while
    retaining the reviewed upper boundary and the required session coverage.
    """

    end = reviewed_end_date or required_end_date
    if end is None:
        raise ValueError("reviewed_end_date is required for a historical request")
    if bar_size.strip().lower() == "1 day" and not is_us_trading_day(end):
        raise ValueError("daily historical request end must be a US trading session")

    start = reviewed_start_for_duration(duration, end=end, bar_size=bar_size)
    if start is None:
        raise ValueError(f"unsupported historical request duration: {duration!r}")
    adjusted_last = what_to_show.strip().upper() == "ADJUSTED_LAST"
    end_mode = HistoricalEndMode.CURRENT if adjusted_last else HistoricalEndMode.EXPLICIT
    return HistoricalRequestScope(
        required_start_date=required_start_date,
        required_end_date=required_end_date,
        reviewed_start_date=start,
        reviewed_end_date=end,
        duration=duration,
        bar_size=bar_size,
        what_to_show=what_to_show,
        end_datetime="" if adjusted_last else f"{end:%Y%m%d}-23:59:59",
        end_mode=end_mode,
        reviewed_session_expiry=end if adjusted_last else None,
        response_admission_policy=response_admission_policy,
    )


def validate_reviewed_session_current(
    scope: HistoricalRequestScope,
    *,
    latest_completed_session: date,
) -> None:
    """Fail closed when a current-ended request outlives its reviewed session."""

    if scope.end_mode != HistoricalEndMode.CURRENT:
        return
    expected = scope.reviewed_session_expiry
    if expected is None:
        raise ValueError("Current-ended historical request has no reviewed session expiry.")
    if latest_completed_session > expected:
        raise ValueError(
            "Current-ended historical request scope expired: "
            f"reviewed session {expected}, latest completed session {latest_completed_session}."
        )
    if latest_completed_session < expected:
        raise ValueError(
            "Current-ended historical request scope is not yet executable: "
            f"reviewed session {expected}, latest completed session {latest_completed_session}."
        )


def reviewed_start_for_duration(
    duration: str | None,
    *,
    end: date,
    bar_size: str,
) -> date | None:
    if not duration:
        return None
    parts = duration.strip().upper().split()
    if len(parts) != 2 or not parts[0].isdigit():
        return None
    amount = int(parts[0])
    if amount <= 0:
        return None
    unit = parts[1]
    if unit == "D" and bar_size.strip().lower() == "1 day":
        return subtract_us_trading_sessions(end, amount - 1)
    calendar_days = {
        "D": amount,
        "W": amount * 7,
        "M": amount * 31,
        "Y": amount * 366,
    }.get(unit)
    return end - timedelta(days=calendar_days - 1) if calendar_days else None
