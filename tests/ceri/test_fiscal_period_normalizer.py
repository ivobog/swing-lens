from __future__ import annotations

from datetime import date

from app.services.ceri.enums import CeriPeriodType
from app.services.ceri.fiscal_period_normalizer import CeriFiscalPeriodNormalizer


def test_non_calendar_fiscal_year_quarter_normalizes_to_provider_fiscal_end() -> None:
    result = CeriFiscalPeriodNormalizer().normalize(
        {
            "period_type": "QUARTERLY",
            "fiscal_year": 2027,
            "fiscal_quarter": 1,
            "fiscal_year_end_month": 1,
            "period_label": "FY27 Q1",
        }
    )

    assert result.period_type is CeriPeriodType.QUARTERLY
    assert result.fiscal_period_end == date(2026, 4, 30)
    assert result.fiscal_year == 2027
    assert result.fiscal_quarter == 1
    assert result.original_label == "FY27 Q1"
