from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.services.ceri.enums import CeriPeriodType


@dataclass(frozen=True)
class FiscalPeriodResult:
    period_type: CeriPeriodType
    fiscal_period_end: date
    fiscal_year: int | None
    fiscal_quarter: int | None
    original_label: str | None


class CeriFiscalPeriodNormalizer:
    def normalize(self, payload: dict[str, Any]) -> FiscalPeriodResult:
        period_type = _period_type(payload)
        fiscal_year = _optional_int(payload.get("fiscal_year") or payload.get("fy"))
        fiscal_quarter = _optional_int(payload.get("fiscal_quarter") or payload.get("fq"))
        fiscal_year_end_month = _optional_int(payload.get("fiscal_year_end_month")) or 12
        explicit_end = _optional_date(payload.get("fiscal_period_end") or payload.get("period_end"))
        original_label = _optional_text(payload.get("period_label") or payload.get("period"))

        if explicit_end is not None:
            return FiscalPeriodResult(
                period_type=period_type,
                fiscal_period_end=explicit_end,
                fiscal_year=fiscal_year or explicit_end.year,
                fiscal_quarter=fiscal_quarter,
                original_label=original_label,
            )
        if fiscal_year is None:
            raise ValueError("fiscal_year or fiscal_period_end is required")

        quarterly_periods = {
            CeriPeriodType.QUARTERLY,
            CeriPeriodType.CURRENT_QUARTER,
            CeriPeriodType.NEXT_QUARTER,
        }
        if period_type in quarterly_periods:
            if fiscal_quarter not in {1, 2, 3, 4}:
                raise ValueError("fiscal_quarter must be 1-4 for quarterly periods")
            period_end = _quarter_end(fiscal_year, fiscal_quarter, fiscal_year_end_month)
        else:
            period_end = _month_end(fiscal_year, fiscal_year_end_month)

        return FiscalPeriodResult(
            period_type=period_type,
            fiscal_period_end=period_end,
            fiscal_year=fiscal_year,
            fiscal_quarter=fiscal_quarter,
            original_label=original_label,
        )


def _quarter_end(fiscal_year: int, fiscal_quarter: int, fiscal_year_end_month: int) -> date:
    end_month = ((fiscal_year_end_month + (fiscal_quarter - 4) * 3 - 1) % 12) + 1
    year = fiscal_year - 1 if end_month > fiscal_year_end_month else fiscal_year
    return _month_end(year, end_month)


def _month_end(year: int, month: int) -> date:
    return date(year, month, monthrange(year, month)[1])


def _period_type(payload: dict[str, Any]) -> CeriPeriodType:
    value = payload.get("period_type") or payload.get("period")
    if value is None:
        raise ValueError("period_type is required")
    return CeriPeriodType(str(value).upper())


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _optional_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _optional_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)
