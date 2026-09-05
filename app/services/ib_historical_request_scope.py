from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta

from app.services.us_market_calendar import (
    is_us_trading_day,
    subtract_us_trading_sessions,
)


@dataclass(frozen=True)
class HistoricalRequestScope:
    """Operator-reviewed footprint for one deterministic IB historical request."""

    required_start_date: date | None
    required_end_date: date | None
    reviewed_start_date: date
    reviewed_end_date: date
    duration: str
    bar_size: str
    what_to_show: str
    end_datetime: str

    def to_dict(self) -> dict[str, object]:
        return {
            key: value.isoformat() if isinstance(value, date) else value
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
) -> HistoricalRequestScope:
    """Build the exact reviewed footprint shared by planner and executor.

    Observed IB behavior for ``N D`` requests with ``1 day`` bars is N daily
    trading observations, inclusive of the final session. Other duration units
    retain IB's documented elapsed-time interpretation.
    """

    end = reviewed_end_date or required_end_date
    if end is None:
        raise ValueError("reviewed_end_date is required for a historical request")
    if bar_size.strip().lower() == "1 day" and not is_us_trading_day(end):
        raise ValueError("daily historical request end must be a US trading session")

    start = reviewed_start_for_duration(duration, end=end, bar_size=bar_size)
    if start is None:
        raise ValueError(f"unsupported historical request duration: {duration!r}")
    return HistoricalRequestScope(
        required_start_date=required_start_date,
        required_end_date=required_end_date,
        reviewed_start_date=start,
        reviewed_end_date=end,
        duration=duration,
        bar_size=bar_size,
        what_to_show=what_to_show,
        end_datetime=f"{end:%Y%m%d}-23:59:59",
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
