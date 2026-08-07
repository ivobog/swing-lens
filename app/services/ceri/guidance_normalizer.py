from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.models.ceri_tables import CeriGuidanceEvent, CeriSourceRecord
from app.services.ceri.currency_conversion_service import decimal_or_none
from app.services.ceri.effective_session_service import CeriEffectiveSessionService
from app.services.ceri.enums import CeriConfidenceLabel, CeriMetric, CeriPeriodType, GuidanceAction

ACTION_ALIASES = {
    "RAISE": GuidanceAction.RAISED,
    "RAISED": GuidanceAction.RAISED,
    "UP": GuidanceAction.RAISED,
    "INITIATE": GuidanceAction.INITIATED,
    "INITIATED": GuidanceAction.INITIATED,
    "MAINTAIN": GuidanceAction.MAINTAINED,
    "MAINTAINED": GuidanceAction.MAINTAINED,
    "NARROW": GuidanceAction.NARROWED,
    "NARROWED": GuidanceAction.NARROWED,
    "WIDEN": GuidanceAction.WIDENED,
    "WIDENED": GuidanceAction.WIDENED,
    "LOWER": GuidanceAction.LOWERED,
    "LOWERED": GuidanceAction.LOWERED,
    "DOWN": GuidanceAction.LOWERED,
    "WITHDRAW": GuidanceAction.WITHDRAWN,
    "WITHDRAWN": GuidanceAction.WITHDRAWN,
}


class CeriGuidanceNormalizer:
    def __init__(self, effective_sessions: CeriEffectiveSessionService | None = None) -> None:
        self.effective_sessions = effective_sessions or CeriEffectiveSessionService()

    def normalize(self, source_record: CeriSourceRecord, *, company_id: int) -> CeriGuidanceEvent:
        payload = dict(source_record.raw_json or source_record.restricted_normalized_json or {})
        announced_at = _datetime(payload.get("announced_at")) or source_record.observed_at
        session = self.effective_sessions.resolve(
            timestamp=announced_at or source_record.published_at,
            source_date=_date(payload.get("source_date")),
        )
        return CeriGuidanceEvent(
            source_record_id=source_record.id,
            company_id=company_id,
            action=normalize_guidance_action(payload.get("action")).value,
            metric=_optional_enum_value(payload.get("metric"), CeriMetric),
            period_type=_optional_enum_value(payload.get("period_type"), CeriPeriodType),
            period_label=_text(payload.get("period_label")),
            low_value=decimal_or_none(
                payload.get("low_value")
                if payload.get("low_value") is not None
                else payload.get("low")
            ),
            high_value=decimal_or_none(
                payload.get("high_value")
                if payload.get("high_value") is not None
                else payload.get("high")
            ),
            point_value=decimal_or_none(
                payload.get("point_value")
                if payload.get("point_value") is not None
                else payload.get("point")
            ),
            unit=_text(
                payload.get("unit") if payload.get("unit") is not None else payload.get("units")
            ),
            currency=_text(payload.get("currency")),
            comparison_basis=_text(payload.get("comparison_basis")),
            confidence=_confidence(payload.get("confidence")).value,
            effective_at=session.effective_at,
            effective_session=session.effective_session,
            evidence_locator=_text(
                payload.get("evidence_locator") or payload.get("source_reference")
            ),
            filing_accession=_text(payload.get("filing_accession") or payload.get("accession")),
            quality_warnings_json=list(session.warnings) or None,
        )


def normalize_guidance_action(value: Any) -> GuidanceAction:
    if value in (None, ""):
        return GuidanceAction.UNKNOWN
    normalized = str(value).strip().upper().replace("-", "_").replace(" ", "_")
    return ACTION_ALIASES.get(normalized, GuidanceAction.UNKNOWN)


def _confidence(value: Any) -> CeriConfidenceLabel:
    if value in (None, ""):
        return CeriConfidenceLabel.NORMAL
    normalized = str(value).strip().lower()
    aliases = {
        "high": CeriConfidenceLabel.HIGH,
        "normal": CeriConfidenceLabel.NORMAL,
        "low": CeriConfidenceLabel.LOW,
        "insufficient": CeriConfidenceLabel.INSUFFICIENT,
    }
    return aliases.get(normalized, CeriConfidenceLabel.INSUFFICIENT)


def _optional_enum_value(value: Any, enum_type) -> str | None:
    if value in (None, ""):
        return None
    return enum_type(str(value).upper()).value


def _text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


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
