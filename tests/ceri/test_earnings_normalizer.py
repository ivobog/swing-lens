from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.ceri_tables import CeriSourceRecord
from app.services.ceri.earnings_normalizer import CeriEarningsNormalizer


def test_earnings_actual_normalizes_period_and_report_session() -> None:
    source = CeriSourceRecord(
        id=41,
        provider="manual",
        dataset="earnings",
        provider_record_id="earnings-1",
        raw_json={
            "metric": "EPS_DILUTED",
            "period_type": "QUARTERLY",
            "fiscal_year": 2027,
            "fiscal_quarter": 1,
            "fiscal_year_end_month": 1,
            "actual": "2.12",
            "report_at": "2026-08-01T10:00:00-04:00",
        },
        content_hash="hash",
        idempotency_key="key",
    )

    earnings = CeriEarningsNormalizer().normalize(source, company_id=42)

    assert earnings.metric == "EPS_DILUTED"
    assert earnings.fiscal_period_end == date(2026, 4, 30)
    assert earnings.report_session == date(2026, 8, 3)
    assert earnings.actual_value is not None


def test_earnings_actual_zero_is_preserved() -> None:
    source = CeriSourceRecord(
        id=42,
        provider="manual",
        dataset="earnings",
        provider_record_id="earnings-zero",
        raw_json={
            "metric": "EPS_DILUTED",
            "period_type": "QUARTERLY",
            "fiscal_year": 2026,
            "fiscal_quarter": 2,
            "actual": 0,
            "report_at": "2026-08-01T10:00:00-04:00",
        },
        content_hash="hash-zero",
        idempotency_key="key-zero",
    )

    earnings = CeriEarningsNormalizer().normalize(source, company_id=42)

    assert earnings.actual_value == Decimal("0")
