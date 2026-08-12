from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.models.ceri_tables import CeriSourceRecord
from app.services.ceri.estimate_normalizer import CeriEstimateNormalizer


def test_currency_uncertainty_prevents_canonical_comparison_and_warns() -> None:
    source = _source(
        {
            "ticker": "SAP",
            "metric": "EPS_DILUTED",
            "period_type": "ANNUAL",
            "fiscal_year": 2026,
            "consensus": "3.25",
            "currency": "EUR",
            "canonical_currency": "USD",
            "published_at": "2026-08-03T12:00:00-04:00",
        }
    )

    estimate = CeriEstimateNormalizer().normalize(source, company_id=42)

    assert estimate.consensus is None
    assert estimate.source_currency == "EUR"
    assert estimate.canonical_currency is None
    assert "currency_conversion_unavailable" in estimate.quality_flags_json


def test_provider_zero_is_distinguishable_from_missing() -> None:
    zero = CeriEstimateNormalizer().normalize(
        _source(
            {
                "metric": "EPS_DILUTED",
                "period_type": "ANNUAL",
                "fiscal_year": 2026,
                "consensus": "0",
                "currency": "USD",
                "source_date": "2026-08-03",
            }
        ),
        company_id=42,
    )
    missing = CeriEstimateNormalizer().normalize(
        _source(
            {
                "metric": "EPS_DILUTED",
                "period_type": "ANNUAL",
                "fiscal_year": 2026,
                "currency": "USD",
                "source_date": "2026-08-03",
            }
        ),
        company_id=42,
    )

    assert zero.consensus == Decimal("0")
    assert "consensus_missing" not in (zero.quality_flags_json or [])
    assert missing.consensus is None
    assert "consensus_missing" in missing.quality_flags_json


def test_missing_currency_is_not_inferred_from_us_ticker_suffix() -> None:
    estimate = CeriEstimateNormalizer().normalize(
        _source(
            {
                "ticker": "NWE.US",
                "metric": "EPS_DILUTED",
                "period_type": "ANNUAL",
                "fiscal_year": 2026,
                "consensus": "4.25",
                "source_date": "2026-08-03",
            }
        ),
        company_id=42,
    )

    assert estimate.consensus is None
    assert estimate.source_currency is None
    assert estimate.canonical_currency is None
    assert estimate.currency_verified is False
    assert "currency_missing" in estimate.quality_flags_json


def test_verified_currency_conversion_is_traceable() -> None:
    source = _source(
        {
            "metric": "EPS_DILUTED",
            "period_type": "ANNUAL",
            "fiscal_year": 2026,
            "consensus": "10",
            "currency": "EUR",
            "canonical_currency": "USD",
            "conversion_rate": "1.2",
            "conversion_source": "manual_verified",
            "conversion_source_record_id": 77,
            "conversion_effective_at": "2026-08-03T12:00:00-04:00",
            "source_date": "2026-08-03",
        }
    )

    estimate = CeriEstimateNormalizer().normalize(source, company_id=42)

    assert estimate.consensus == Decimal("12.0")
    assert estimate.canonical_currency == "USD"
    assert estimate.conversion_source_record_id == 77
    assert estimate.conversion_effective_at is not None


def test_provider_decimal_formatted_counts_are_normalized_as_integers() -> None:
    estimate = CeriEstimateNormalizer().normalize(
        _source(
            {
                "metric": "EPS_DILUTED",
                "period_type": "ANNUAL",
                "fiscal_year": 2026,
                "consensus": "10",
                "currency": "USD",
                "analyst_count": "21.0000",
                "upward_count": "3.0000",
                "downward_count": "2.0000",
                "source_date": "2026-08-03",
            }
        ),
        company_id=42,
    )

    assert estimate.analyst_count == 21
    assert estimate.upward_count == 3
    assert estimate.downward_count == 2


def _source(payload: dict) -> CeriSourceRecord:
    published_at = payload.get("published_at")
    return CeriSourceRecord(
        id=11,
        provider="manual",
        dataset="estimates",
        provider_record_id="estimate-1",
        raw_json=payload,
        published_at=datetime.fromisoformat(published_at) if published_at else None,
        observed_at=None,
        content_hash="hash",
        idempotency_key="key",
        ingested_at=datetime(2026, 8, 1, tzinfo=ZoneInfo("UTC")),
    )
