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
        observation_timestamp = observed_at or source_record.published_at
        if observation_timestamp is not None or source_date is not None:
            session = self.effective_sessions.resolve(
                timestamp=observation_timestamp,
                source_date=source_date,
            )
            effective_at = session.effective_at
            effective_session = session.effective_session
            session_warnings = list(session.warnings)
        else:
            # EODHD trend fields may be relative to the provider observation
            # point without exposing that point.  Keep the estimate usable,
            # but do not invent a fiscal-period-relative session.
            effective_at = None
            effective_session = None
            session_warnings = ["missing_observation_timestamp"]
        source_currency = _currency(payload.get("source_currency") or payload.get("currency"))
        source_scale = (
            decimal_or_none(payload.get("source_scale") or payload.get("scale"))
            or Decimal("1")
        )
        canonical_currency = _currency(payload.get("canonical_currency")) or source_currency
        canonical_scale = decimal_or_none(payload.get("canonical_scale")) or source_scale
        currency_basis = _text(payload.get("currency_basis"))
        if currency_basis is None and source_currency is not None:
            currency_basis = "provider_reported"
        conversion_source = _text(payload.get("conversion_source")) or currency_basis
        conversion_effective_at = _datetime(payload.get("conversion_effective_at"))
        conversion_source_record_id = int_or_none(payload.get("conversion_source_record_id"))
        conversion_rate = decimal_or_none(payload.get("conversion_rate"))

        metric = CeriMetric(str(payload.get("metric") or "EPS_DILUTED")).value
        consensus = decimal_or_none(payload.get("consensus"))
        same_provider_relative_eps = bool(
            metric == CeriMetric.EPS_DILUTED.value
            and _text(payload.get("current_observation_reference"))
        )
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
        quality_flags = [*session_warnings, *consensus_conversion.warnings]
        normalized_consensus = consensus_conversion.canonical_value
        normalized_scale = consensus_conversion.canonical_scale
        if (
            same_provider_relative_eps
            and consensus is not None
            and consensus_conversion.canonical_currency is None
        ):
            # Preserve the provider-scale number for a same-response relative
            # comparison. Currency remains unknown, so absolute and cross-source
            # comparability continue to fail closed.
            normalized_consensus = consensus
            normalized_scale = source_scale
            quality_flags.append("relative_value_only")
        if source_currency is None:
            quality_flags.append("currency_missing")
        if consensus is None:
            quality_flags.append("consensus_missing")

        baseline_origin = _text(payload.get("baseline_origin"))
        retrieved_at = source_record.retrieved_at or source_record.ingested_at
        provider_reference_at = (
            _datetime(payload.get("provider_observation_at"))
            or _datetime(payload.get("provider_observed_at"))
            or source_record.source_timestamp
        )
        reference_at = _datetime(payload.get("reference_at")) or effective_at
        known_at = (
            retrieved_at
            if baseline_origin in {"PROVIDER_RELATIVE_WINDOW", "PROVIDER_RETROSPECTIVE_WINDOW"}
            else provider_reference_at or effective_at or retrieved_at
        )

        return CeriEstimateSnapshot(
            source_record_id=source_record.id,
            company_id=company_id,
            metric=metric,
            fiscal_period_end=period.fiscal_period_end,
            period_type=period.period_type.value,
            canonical_period_slot=period.period_type.value,
            fiscal_year=period.fiscal_year,
            fiscal_quarter=period.fiscal_quarter,
            consensus=normalized_consensus,
            high=(
                decimal_or_none(payload.get("high"))
                if same_provider_relative_eps and source_currency is None
                else self._convert_value(
                    payload.get("high"),
                    source_currency,
                    source_scale,
                    canonical_currency,
                    canonical_scale,
                    payload,
                )
            ),
            low=(
                decimal_or_none(payload.get("low"))
                if same_provider_relative_eps and source_currency is None
                else self._convert_value(
                    payload.get("low"),
                    source_currency,
                    source_scale,
                    canonical_currency,
                    canonical_scale,
                    payload,
                )
            ),
            analyst_count=int_or_none(payload.get("analyst_count")),
            upward_count=int_or_none(payload.get("upward_count")),
            downward_count=int_or_none(payload.get("downward_count")),
            source_currency=source_currency,
            source_scale=source_scale,
            canonical_currency=consensus_conversion.canonical_currency,
            currency_basis=currency_basis,
            currency_verified=(
                consensus_conversion.canonical_currency is not None
                and currency_basis in self.currency_conversion.config.allowed_sources
            ),
            canonical_scale=normalized_scale,
            conversion_rate=consensus_conversion.conversion_rate,
            conversion_source_record_id=consensus_conversion.conversion_source_record_id,
            conversion_effective_at=consensus_conversion.conversion_effective_at,
            effective_at=effective_at,
            reference_at=reference_at,
            known_at=known_at,
            retrieved_at=retrieved_at,
            effective_session=effective_session,
            provider_observed_at=(
                _datetime(payload.get("provider_observed_at"))
                or source_record.source_timestamp
                or source_record.observed_at
            ),
            source_timestamp=source_record.source_timestamp,
            trend_baseline_window_days=_optional_int(
                payload.get("trend_baseline_window_days")
                if payload.get("trend_baseline_window_days") is not None
                else payload.get("trend_baseline_days")
            ),
            baseline_origin=baseline_origin,
            source_provider=source_record.provider,
            current_observation_reference=_text(payload.get("current_observation_reference")),
            canonical_observation_key=canonical_estimate_key(
                company_id=company_id,
                metric=metric,
                period_type=period.period_type.value,
                fiscal_period_end=period.fiscal_period_end,
                currency=consensus_conversion.canonical_currency or source_currency or "UNVERIFIED",
                scale=normalized_scale or source_scale,
                effective_session=effective_session,
            ),
            original_fields_json=payload,
            quality_flags_json=quality_flags or None,
            normalization_version="ceri-normalization-1.2.0",
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
            conversion_source=(
                _text(payload.get("conversion_source"))
                or _text(payload.get("currency_basis"))
                or ("provider_reported" if source_currency is not None else None)
            ),
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
    effective_session: date | None,
) -> str:
    return ":".join(
        [
            str(company_id),
            metric.upper(),
            period_type.upper(),
            fiscal_period_end.isoformat(),
            currency.upper(),
            str(scale.normalize()),
            effective_session.isoformat() if effective_session is not None else "UNKNOWN",
        ]
    )


def _payload(source_record: CeriSourceRecord) -> dict[str, Any]:
    return dict(source_record.raw_json or source_record.restricted_normalized_json or {})


def _text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
