from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.models.ceri_tables import CeriEarningsActual, CeriSourceRecord
from app.services.ceri.currency_conversion_service import decimal_or_none
from app.services.ceri.effective_session_service import CeriEffectiveSessionService
from app.services.ceri.enums import CeriMetric
from app.services.ceri.fiscal_period_normalizer import CeriFiscalPeriodNormalizer


class CeriEarningsNormalizer:
    def __init__(
        self,
        *,
        fiscal_periods: CeriFiscalPeriodNormalizer | None = None,
        effective_sessions: CeriEffectiveSessionService | None = None,
    ) -> None:
        self.fiscal_periods = fiscal_periods or CeriFiscalPeriodNormalizer()
        self.effective_sessions = effective_sessions or CeriEffectiveSessionService()

    def normalize(self, source_record: CeriSourceRecord, *, company_id: int) -> CeriEarningsActual:
        payload = dict(source_record.raw_json or source_record.restricted_normalized_json or {})
        period = self.fiscal_periods.normalize(payload)
        report_at = _datetime(payload.get("report_at")) or source_record.observed_at
        session = self.effective_sessions.resolve(
            timestamp=report_at or source_record.published_at,
            source_date=_date(payload.get("source_date")),
        )
        warnings = list(session.warnings)
        actual = decimal_or_none(
            payload.get("actual_value")
            if payload.get("actual_value") is not None
            else payload.get("actual")
        )
        provider_consensus = decimal_or_none(
            payload.get("estimate")
            if payload.get("estimate") is not None
            else payload.get("consensus_at_report")
        )
        provider_surprise = decimal_or_none(
            payload.get("surprise_percent")
            if payload.get("surprise_percent") is not None
            else payload.get("surprise_pct")
        )
        if actual is None:
            warnings.append("actual_missing")
        return CeriEarningsActual(
            source_record_id=source_record.id,
            company_id=company_id,
            metric=CeriMetric(str(payload.get("metric") or "EPS_DILUTED")).value,
            period_type=period.period_type.value,
            fiscal_period_end=period.fiscal_period_end,
            report_at=session.effective_at,
            report_session=session.effective_session,
            actual_value=actual,
            provider_consensus_value=provider_consensus,
            provider_surprise_pct=provider_surprise,
            event_kind=_text(payload.get("event_kind")) or (
                "REPORTED" if actual is not None else "UPCOMING"
            ),
            acquisition_policy=_text(payload.get("acquisition_policy")),
            provider_consensus_semantics=_text(
                payload.get("provider_consensus_semantics")
            ),
            quality_warnings_json=warnings or None,
        )


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


def _text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)
