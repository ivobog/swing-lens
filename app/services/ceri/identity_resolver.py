from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriCompany, CeriCompanyAlias, CeriSourceRecord


@dataclass(frozen=True)
class IdentityResolution:
    company_id: int | None
    status: str
    reason: str | None = None
    matches: tuple[CeriCompany, ...] = ()

    @property
    def resolved(self) -> bool:
        return self.status == "RESOLVED" and self.company_id is not None


class CeriIdentityResolver:
    def __init__(
        self,
        *,
        companies: list[CeriCompany] | None = None,
        aliases: list[CeriCompanyAlias] | None = None,
    ) -> None:
        self._companies = companies
        self._aliases = aliases

    def resolve_source_record(
        self,
        db: Session,
        source_record: CeriSourceRecord,
    ) -> IdentityResolution:
        hints = _payload(source_record)
        as_of = _as_of(source_record, hints)
        matches = self._candidate_companies(db, source_record.provider, hints, as_of)
        if len(matches) == 1:
            return IdentityResolution(company_id=matches[0].id, status="RESOLVED", matches=matches)
        if not matches:
            source_record.quarantine_reason = (
                source_record.quarantine_reason or "identity_unresolved"
            )
            return IdentityResolution(
                company_id=None,
                status="UNRESOLVED",
                reason="identity_unresolved",
            )
        source_record.quarantine_reason = source_record.quarantine_reason or "identity_ambiguous"
        return IdentityResolution(
            company_id=None,
            status="AMBIGUOUS",
            reason="identity_ambiguous",
            matches=matches,
        )

    def _candidate_companies(
        self,
        db: Session,
        provider: str,
        hints: dict[str, Any],
        as_of: date | None,
    ) -> tuple[CeriCompany, ...]:
        companies = list(self._companies) if self._companies is not None else _load_companies(db)
        aliases = list(self._aliases) if self._aliases is not None else _load_aliases(db)
        matched_ids: set[int] = set()

        ticker = _upper(hints.get("ticker"))
        exchange = _upper(hints.get("exchange"))
        cik = _text(hints.get("cik"))
        provider_company_id = _text(hints.get("provider_company_id"))

        for company in companies:
            if ticker and company.ticker.upper() == ticker:
                if exchange is None or (company.exchange or "").upper() == exchange:
                    matched_ids.add(company.id)
            if cik and company.cik == cik:
                matched_ids.add(company.id)
            provider_ids = company.current_provider_ids_json or {}
            if provider_company_id and _provider_id_matches(
                provider_ids, provider, provider_company_id
            ):
                matched_ids.add(company.id)

        for alias in aliases:
            if alias.provider != provider:
                continue
            if not _alias_valid(alias, as_of):
                continue
            alias_value = alias.alias_value.upper()
            if alias.alias_type == "ticker" and ticker and alias_value == ticker:
                matched_ids.add(alias.company_id)
            if alias.alias_type == "provider_company_id" and provider_company_id:
                if alias.alias_value == provider_company_id:
                    matched_ids.add(alias.company_id)
            if alias.alias_type == "cik" and cik and alias.alias_value == cik:
                matched_ids.add(alias.company_id)

        return tuple(company for company in companies if company.id in matched_ids)


def _load_companies(db: Session) -> list[CeriCompany]:
    scalars = getattr(db, "scalars", None)
    if not callable(scalars):
        return []
    result = scalars(select(CeriCompany))
    return list(result.all() if hasattr(result, "all") else result)


def _load_aliases(db: Session) -> list[CeriCompanyAlias]:
    scalars = getattr(db, "scalars", None)
    if not callable(scalars):
        return []
    result = scalars(select(CeriCompanyAlias))
    return list(result.all() if hasattr(result, "all") else result)


def _payload(source_record: CeriSourceRecord) -> dict[str, Any]:
    payload = source_record.raw_json or source_record.restricted_normalized_json or {}
    return {**(source_record.company_hint_json or {}), **payload}


def _as_of(source_record: CeriSourceRecord, hints: dict[str, Any]) -> date | None:
    for value in (source_record.observed_at, source_record.published_at):
        if isinstance(value, datetime):
            return value.date()
    value = hints.get("observed_at") or hints.get("published_at") or hints.get("source_date")
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _alias_valid(alias: CeriCompanyAlias, as_of: date | None) -> bool:
    if as_of is None:
        return True
    if alias.valid_from is not None and as_of < alias.valid_from:
        return False
    if alias.valid_to is not None and as_of > alias.valid_to:
        return False
    return True


def _provider_id_matches(values: dict[str, Any], provider: str, expected: str) -> bool:
    candidate = values.get(provider)
    if isinstance(candidate, dict):
        candidate = candidate.get("id") or candidate.get("provider_company_id")
    return candidate is not None and str(candidate).upper() == expected.upper()


def _text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _upper(value: Any) -> str | None:
    text = _text(value)
    return text.upper() if text else None
