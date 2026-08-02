from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.models.ceri_tables import CeriEstimateSnapshot, CeriSourceRecord
from app.services.ceri.currency_conversion_service import (
    CeriCurrencyConversionService,
    decimal_or_none,
    int_or_none,
)
from app.services.ceri.effective_session_service import CeriEffectiveSessionService
from app.services.ceri.enums import CeriMetric
from app.services.ceri.fiscal_period_normalizer import CeriFiscalPeriodNormalizer


class CeriEstimateNormalizer:
    def __init__(
        self,
        *,
        fiscal_periods: CeriFiscalPeriodNormalizer | None = None,
        effective_sessions: CeriEffectiveSessionService | None = None,
        currency_conversion: CeriCurrencyConversionService | None = None,
    ) -> None:
        self.fiscal_periods = fiscal_periods or CeriFiscalPeriodNormalizer()
        self.effective_sessions = effective_sessions or CeriEffectiveSessionService()
        self.currency_conversion = currency_conversion or CeriCurrencyConversionService()

    def normalize(
        self,
        source_record: CeriSourceRecord,
        *,
        company_id: int,
    ) -> CeriEstimateSnapshot:
        payload = _payload(source_record)
        period = self.fiscal_periods.normalize(payload)
        observed_at = _datetime(payload.get("effective_at")) or source_record.observed_at
        source_date = _date(payload.get("source_date")) or _date(payload.get("published_date"))
        session = self.effective_sessions.resolve(
            timestamp=observed_at or source_record.published_at,
            source_date=source_date,
        )
        source_currency = _currency(payload.get("source_currency") or payload.get("currency"))
        source_scale = (
            decimal_or_none(payload.get("source_scale") or payload.get("scale"))
            or Decimal("1")
        )
        canonical_currency = _currency(payload.get("canonical_currency")) or source_currency
        canonical_scale = decimal_or_none(payload.get("canonical_scale")) or source_scale
        conversion_source = _text(payload.get("conversion_source"))
        conversion_effective_at = _datetime(payload.get("conversion_effective_at"))
        conversion_source_record_id = int_or_none(payload.get("conversion_source_record_id"))
        conversion_rate = decimal_or_none(payload.get("conversion_rate"))

        consensus = decimal_or_none(payload.get("consensus"))
        consensus_conversion = self.currency_conversion.convert(
            consensus,
            source_currency=source_currency,
            source_scale=source_scale,
            canonical_currency=canonical_currency,
            canonical_scale=canonical_scale,
            conversion_rate=conversion_rate,
            conversion_source=conversion_source,
            conversion_source_record_id=conversion_source_record_id,
            conversion_effective_at=conversion_effective_at,
        )
        quality_flags = [*session.warnings, *consensus_conversion.warnings]
        if consensus is None:
            quality_flags.append("consensus_missing")

        return CeriEstimateSnapshot(
            source_record_id=source_record.id,
            company_id=company_id,
            metric=CeriMetric(str(payload.get("metric") or "EPS_DILUTED")).value,
            fiscal_period_end=period.fiscal_period_end,
            period_type=period.period_type.value,
            fiscal_year=period.fiscal_year,
            fiscal_quarter=period.fiscal_quarter,
            consensus=consensus_conversion.canonical_value,
            high=self._convert_value(
                payload.get("high"),
                source_currency,
                source_scale,
                canonical_currency,
                canonical_scale,
                payload,
            ),
            low=self._convert_value(
                payload.get("low"),
                source_currency,
                source_scale,
                canonical_currency,
                canonical_scale,
                payload,
            ),
            analyst_count=int_or_none(payload.get("analyst_count")),
            upward_count=int_or_none(payload.get("upward_count")),
            downward_count=int_or_none(payload.get("downward_count")),
            source_currency=source_currency,
            source_scale=source_scale,
            canonical_currency=consensus_conversion.canonical_currency,
            canonical_scale=consensus_conversion.canonical_scale,
            conversion_rate=consensus_conversion.conversion_rate,
            conversion_source_record_id=consensus_conversion.conversion_source_record_id,
            conversion_effective_at=consensus_conversion.conversion_effective_at,
            effective_at=session.effective_at,
            effective_session=session.effective_session,
            canonical_observation_key=canonical_estimate_key(
                company_id=company_id,
                metric=str(payload.get("metric") or "EPS_DILUTED"),
                period_type=period.period_type.value,
                fiscal_period_end=period.fiscal_period_end,
                currency=consensus_conversion.canonical_currency or source_currency or "UNVERIFIED",
                scale=consensus_conversion.canonical_scale or source_scale,
                effective_session=session.effective_session,
            ),
            original_fields_json=payload,
            quality_flags_json=quality_flags or None,
        )

    def _convert_value(
        self,
        value: Any,
        source_currency: str | None,
        source_scale: Decimal,
        canonical_currency: str | None,
        canonical_scale: Decimal,
        payload: dict[str, Any],
    ) -> Decimal | None:
        conversion = self.currency_conversion.convert(
            decimal_or_none(value),
            source_currency=source_currency,
            source_scale=source_scale,
            canonical_currency=canonical_currency,
            canonical_scale=canonical_scale,
            conversion_rate=decimal_or_none(payload.get("conversion_rate")),
            conversion_source=_text(payload.get("conversion_source")),
            conversion_source_record_id=int_or_none(payload.get("conversion_source_record_id")),
            conversion_effective_at=_datetime(payload.get("conversion_effective_at")),
        )
        return conversion.canonical_value


def canonical_estimate_key(
    *,
    company_id: int,
    metric: str,
    period_type: str,
    fiscal_period_end: date,
    currency: str,
    scale: Decimal,
    effective_session: date,
) -> str:
    return ":".join(
        [
            str(company_id),
            metric.upper(),
            period_type.upper(),
            fiscal_period_end.isoformat(),
            currency.upper(),
            str(scale.normalize()),
            effective_session.isoformat(),
        ]
    )


def _payload(source_record: CeriSourceRecord) -> dict[str, Any]:
    return dict(source_record.raw_json or source_record.restricted_normalized_json or {})


def _text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _currency(value: Any) -> str | None:
    text = _text(value)
    return text.upper() if text else None


def _datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])
