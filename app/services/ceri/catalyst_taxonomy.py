from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from app.models.ceri_tables import CeriSourceRecord
from app.services.ceri.config import CatalystTaxonomyConfig, load_ceri_config
from app.services.ceri.dtos import NormalizedCatalystRecord
from app.services.ceri.effective_session_service import CeriEffectiveSessionService
from app.services.ceri.enums import (
    CatalystCategory,
    CatalystDirection,
    CatalystStatus,
    CeriConfidenceLabel,
)


class CeriCatalystTaxonomy:
    def __init__(
        self,
        *,
        taxonomy: CatalystTaxonomyConfig | None = None,
        effective_sessions: CeriEffectiveSessionService | None = None,
    ) -> None:
        self.taxonomy = taxonomy or load_ceri_config().taxonomy
        self.effective_sessions = effective_sessions or CeriEffectiveSessionService()

    def normalize(
        self,
        source_record: CeriSourceRecord,
        *,
        company_id: int,
    ) -> NormalizedCatalystRecord:
        payload = dict(source_record.raw_json or source_record.restricted_normalized_json or {})
        category = self.normalize_category(payload)
        status = _enum_or_default(payload.get("status"), CatalystStatus, CatalystStatus.ANNOUNCED)
        direction = _enum_or_default(
            payload.get("direction"),
            CatalystDirection,
            CatalystDirection.UNKNOWN,
        )
        announced_at = _datetime(payload.get("announced_at")) or source_record.observed_at
        expected_date = _date(payload.get("expected_date"))
        source_date = _date(payload.get("source_date")) or expected_date
        session = self.effective_sessions.resolve(
            timestamp=announced_at or source_record.published_at,
            source_date=source_date,
        )
        subtype = _subject_text(payload.get("subtype")) or _infer_subtype(payload, category)
        subject = _subject_text(payload.get("subject") or payload.get("title") or subtype)
        return NormalizedCatalystRecord(
            source_record_id=source_record.id,
            company_id=company_id,
            category=category,
            subtype=subtype,
            subject_key=subject_key(subject),
            status=status,
            direction=direction,
            materiality=_optional_float(payload.get("materiality")),
            confidence=_confidence(payload.get("confidence")),
            date_confidence=session.date_confidence,
            announced_at=session.effective_at,
            expected_date=expected_date,
            effective_session=session.effective_session,
            canonical_text=subject,
            conflict_flags=tuple(session.warnings),
        )

    def normalize_category(self, payload: dict[str, Any]) -> CatalystCategory:
        raw_category = payload.get("category")
        if raw_category not in (None, ""):
            return CatalystCategory(str(raw_category).upper())
        text = " ".join(str(payload.get(key) or "") for key in ("subtype", "subject", "title"))
        lowered = text.lower()
        for category, config in self.taxonomy.categories.items():
            if any(example.lower() in lowered for example in config.examples):
                return category
        raise ValueError("catalyst category is required or must match taxonomy examples")

    def validate_transition(self, current: CatalystStatus, next_status: CatalystStatus) -> None:
        allowed = self.taxonomy.status_transitions[current]
        if next_status not in allowed:
            raise ValueError(f"invalid catalyst transition {current.value} -> {next_status.value}")


def subject_key(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or "unknown"


def _infer_subtype(payload: dict[str, Any], category: CatalystCategory) -> str:
    text = " ".join(str(payload.get(key) or "") for key in ("subject", "title")).lower()
    for example in load_ceri_config().taxonomy.categories[category].examples:
        if example.lower() in text:
            return example
    return "unknown"


def _subject_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return re.sub(r"\s+", " ", str(value)).strip()


def _confidence(value: Any) -> CeriConfidenceLabel:
    if value in (None, ""):
        return CeriConfidenceLabel.NORMAL
    normalized = str(value).strip().lower()
    for label in CeriConfidenceLabel:
        if label.value.lower() == normalized:
            return label
    return CeriConfidenceLabel.INSUFFICIENT


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _enum_or_default(value: Any, enum_type, default):
    if value in (None, ""):
        return default
    return enum_type(str(value).upper())


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
