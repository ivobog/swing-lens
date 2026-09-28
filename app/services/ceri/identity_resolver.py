from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select, text, union_all
from sqlalchemy.orm import Session

from app.models.ceri_tables import (
    CeriCatalystEvent,
    CeriCatalystEventRevision,
    CeriCompany,
    CeriCompanyAlias,
    CeriEarningsActual,
    CeriEstimateSnapshot,
    CeriGuidanceEvent,
    CeriSourceRecord,
)
from app.services.canonical_evidence import CanonicalEvidenceSerializer
from app.services.domain_mutation import MutationDomain, MutationSemanticMode
from app.services.source_mutation_authority import source_mutation_writer


@dataclass(frozen=True)
class IdentityResolution:
    company_id: int | None
    status: str
    reason: str | None = None
    matches: tuple[CeriCompany, ...] = ()

    @property
    def resolved(self) -> bool:
        return self.status == "RESOLVED" and self.company_id is not None


@dataclass(frozen=True)
class ProviderIdentityEvidence:
    company_id: int
    ticker: str
    provider: str
    identities: tuple[str, ...]
    source_record_ids: tuple[int, ...]
    ingestion_run_ids: tuple[int, ...]
    datasets: tuple[str, ...]
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    evidence_fingerprint: str
    conflicts: tuple[str, ...] = ()

    @property
    def authoritative_identity(self) -> str | None:
        if self.conflicts or len(self.identities) != 1:
            return None
        return self.identities[0]


@dataclass(frozen=True)
class ProviderIdentityReconciliationResult:
    company_id: int
    ticker: str
    provider: str
    status: str
    provider_identity: str | None = None
    evidence: ProviderIdentityEvidence | None = None
    alias_id: int | None = None
    reason: str | None = None

    @property
    def resolved(self) -> bool:
        return self.status in {"ALREADY_RESOLVED", "RECONCILED"}


def collect_provider_identity_evidence(
    db: Session,
    *,
    company_id: int,
    provider: str,
) -> ProviderIdentityEvidence:
    """Collect exact provider IDs from normalized rows already bound to a company."""

    company = db.get(CeriCompany, company_id)
    if company is None:
        raise ValueError(f"CERI_PROVIDER_IDENTITY_COMPANY_UNKNOWN:{company_id}")
    normalized_provider = str(provider).strip().lower()
    if not normalized_provider:
        raise ValueError("CERI_PROVIDER_IDENTITY_PROVIDER_REQUIRED")

    linked_source_ids = union_all(
        select(CeriEstimateSnapshot.source_record_id).where(
            CeriEstimateSnapshot.company_id == company_id
        ),
        select(CeriEarningsActual.source_record_id).where(
            CeriEarningsActual.company_id == company_id
        ),
        select(CeriGuidanceEvent.source_record_id).where(
            CeriGuidanceEvent.company_id == company_id
        ),
        select(CeriCatalystEventRevision.source_record_id)
        .join(
            CeriCatalystEvent,
            CeriCatalystEvent.id == CeriCatalystEventRevision.catalyst_event_id,
        )
        .where(
            CeriCatalystEvent.company_id == company_id,
            CeriCatalystEventRevision.source_record_id.is_not(None),
        ),
    ).subquery()
    records = list(
        db.scalars(
            select(CeriSourceRecord)
            .where(
                CeriSourceRecord.id.in_(select(linked_source_ids.c.source_record_id)),
                func.lower(CeriSourceRecord.provider) == normalized_provider,
            )
            .order_by(CeriSourceRecord.id)
        )
    )
    ticker = company.ticker.strip().upper()
    candidates: list[str] = []
    conflicts: list[str] = []
    evidence_rows: list[dict[str, Any]] = []
    evidence_records: list[CeriSourceRecord] = []
    for record in records:
        hints = record.company_hint_json or {}
        identity = _upper(hints.get("provider_company_id"))
        hinted_ticker = _upper(hints.get("ticker"))
        if identity is None:
            continue
        evidence_records.append(record)
        candidates.append(identity)
        if hinted_ticker != ticker:
            conflicts.append(f"TICKER_HINT:{record.id}:{hinted_ticker or 'MISSING'}")
        if not _provider_identity_maps_to_ticker(
            provider=normalized_provider,
            provider_identity=identity,
            ticker=ticker,
        ):
            conflicts.append(f"IDENTITY_TICKER:{record.id}:{identity}")
        if not _provider_record_matches_identity(
            provider=normalized_provider,
            dataset=record.dataset,
            provider_identity=identity,
            provider_record_id=record.provider_record_id,
        ):
            conflicts.append(f"RECORD_IDENTITY:{record.id}:{identity}")
        evidence_rows.append(
            {
                "source_record_id": int(record.id),
                "ingestion_run_id": (
                    int(record.ingestion_run_id) if record.ingestion_run_id is not None else None
                ),
                "dataset": record.dataset,
                "provider_record_id": record.provider_record_id,
                "provider_company_id": identity,
                "ticker": hinted_ticker,
                "content_hash": record.content_hash,
                "normalized_hash": record.normalized_hash,
                "ingested_at": record.ingested_at,
            }
        )
    distinct_candidates = set(candidates)
    if len(distinct_candidates) > 1:
        conflicts.append(
            "MULTIPLE_PROVIDER_IDENTITIES:" + ",".join(sorted(distinct_candidates))
        )
    observed = [
        record.ingested_at for record in evidence_records if record.ingested_at is not None
    ]
    return ProviderIdentityEvidence(
        company_id=int(company.id),
        ticker=ticker,
        provider=normalized_provider,
        identities=tuple(sorted(distinct_candidates)),
        source_record_ids=tuple(int(record.id) for record in evidence_records),
        ingestion_run_ids=tuple(
            sorted(
                {
                    int(record.ingestion_run_id)
                    for record in evidence_records
                    if record.ingestion_run_id is not None
                }
            )
        ),
        datasets=tuple(sorted({record.dataset for record in evidence_records})),
        first_seen_at=min(observed) if observed else None,
        last_seen_at=max(observed) if observed else None,
        evidence_fingerprint=CanonicalEvidenceSerializer.fingerprint(evidence_rows),
        conflicts=tuple(sorted(set(conflicts))),
    )


@source_mutation_writer(
    MutationDomain.CERI_SOURCE, "provider_source", mode=MutationSemanticMode.MAINTENANCE
)
def reconcile_missing_provider_identity(
    db: Session,
    *,
    company_id: int,
    provider: str,
) -> ProviderIdentityReconciliationResult:
    """Promote unanimous durable source evidence without guessing or overwriting."""

    company = db.scalar(
        select(CeriCompany).where(CeriCompany.id == company_id).with_for_update()
    )
    if company is None:
        raise ValueError(f"CERI_PROVIDER_IDENTITY_COMPANY_UNKNOWN:{company_id}")
    normalized_provider = str(provider).strip().lower()
    evidence = collect_provider_identity_evidence(
        db,
        company_id=company_id,
        provider=normalized_provider,
    )
    candidate = evidence.authoritative_identity
    if evidence.conflicts or len(evidence.identities) > 1:
        return ProviderIdentityReconciliationResult(
            int(company.id),
            company.ticker.upper(),
            normalized_provider,
            "CONFLICT",
            evidence=evidence,
            reason=";".join(evidence.conflicts) or "MULTIPLE_PROVIDER_IDENTITIES",
        )
    if candidate is None or len(evidence.source_record_ids) < 2:
        return ProviderIdentityReconciliationResult(
            int(company.id),
            company.ticker.upper(),
            normalized_provider,
            "INSUFFICIENT_EVIDENCE",
            evidence=evidence,
            reason=(
                "At least two normalized durable source records with one provider ID are required."
            ),
        )

    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
        {"scope": f"ceri-provider-identity:{normalized_provider}:{candidate}"},
    )
    conflict = _provider_identity_conflict(
        db,
        company_id=int(company.id),
        provider=normalized_provider,
        provider_identity=candidate,
    )
    if conflict is not None:
        return ProviderIdentityReconciliationResult(
            int(company.id),
            company.ticker.upper(),
            normalized_provider,
            "CONFLICT",
            provider_identity=candidate,
            evidence=evidence,
            reason=conflict,
        )

    provider_ids = dict(company.current_provider_ids_json or {})
    existing = _provider_id(provider_ids, normalized_provider)
    if existing is not None and existing != candidate:
        return ProviderIdentityReconciliationResult(
            int(company.id),
            company.ticker.upper(),
            normalized_provider,
            "CONFLICT",
            provider_identity=candidate,
            evidence=evidence,
            reason=f"Existing provider identity is {existing}.",
        )

    alias = db.scalar(
        select(CeriCompanyAlias).where(
            CeriCompanyAlias.company_id == company.id,
            CeriCompanyAlias.provider == normalized_provider,
            CeriCompanyAlias.alias_type == "provider_company_id",
            func.upper(CeriCompanyAlias.alias_value) == candidate,
        )
    )
    changed = existing is None
    if changed:
        provider_ids[normalized_provider] = candidate
        company.current_provider_ids_json = provider_ids
        company.updated_at = datetime.now(UTC)
    if alias is None:
        provenance = {
            "schema_version": "ceri-provider-identity-reconciliation-v1",
            "company_id": int(company.id),
            "ticker": company.ticker.upper(),
            "provider": normalized_provider,
            "provider_identity": candidate,
            "source_record_ids": list(evidence.source_record_ids),
            "ingestion_run_ids": list(evidence.ingestion_run_ids),
            "datasets": list(evidence.datasets),
            "first_seen_at": CanonicalEvidenceSerializer.canonicalize(evidence.first_seen_at),
            "last_seen_at": CanonicalEvidenceSerializer.canonicalize(evidence.last_seen_at),
            "evidence_fingerprint": evidence.evidence_fingerprint,
        }
        alias = CeriCompanyAlias(
            company_id=company.id,
            provider=normalized_provider,
            alias_type="provider_company_id",
            alias_value=candidate,
            exchange=company.exchange,
            source=json.dumps(provenance, sort_keys=True, separators=(",", ":")),
            confidence="High",
        )
        db.add(alias)
    db.flush()
    return ProviderIdentityReconciliationResult(
        int(company.id),
        company.ticker.upper(),
        normalized_provider,
        "RECONCILED" if changed else "ALREADY_RESOLVED",
        provider_identity=candidate,
        evidence=evidence,
        alias_id=int(alias.id),
    )


def validate_certification_provider_identities(
    db: Session,
    *,
    tickers: tuple[str, ...],
    providers: tuple[str, ...],
) -> dict[str, dict[str, str]]:
    """Fail before certification admission if canonical provider identity is unusable."""

    resolved: dict[str, dict[str, str]] = {}
    for ticker in tickers:
        symbol = ticker.strip().upper()
        companies = list(
            db.scalars(
                select(CeriCompany)
                .where(func.upper(CeriCompany.ticker) == symbol)
                .order_by(CeriCompany.id)
            )
        )
        if not companies:
            raise ValueError(f"CERI_CERTIFICATION_PROVIDER_IDENTITY_MISSING:{symbol}")
        if len(companies) != 1:
            raise ValueError(f"CERI_CERTIFICATION_PROVIDER_IDENTITY_CONFLICT:{symbol}")
        company = companies[0]
        values: dict[str, str] = {}
        for provider in providers:
            normalized_provider = provider.strip().lower()
            identity = _provider_id(company.current_provider_ids_json or {}, normalized_provider)
            if identity is None:
                evidence = collect_provider_identity_evidence(
                    db,
                    company_id=int(company.id),
                    provider=normalized_provider,
                )
                code = (
                    "CONFLICT"
                    if evidence.conflicts or len(evidence.identities) > 1
                    else "MISSING"
                )
                raise ValueError(f"CERI_CERTIFICATION_PROVIDER_IDENTITY_{code}:{symbol}")
            if not _provider_identity_maps_to_ticker(
                provider=normalized_provider,
                provider_identity=identity,
                ticker=symbol,
            ):
                raise ValueError(f"CERI_CERTIFICATION_PROVIDER_IDENTITY_CONFLICT:{symbol}")
            conflict = _provider_identity_conflict(
                db,
                company_id=int(company.id),
                provider=normalized_provider,
                provider_identity=identity,
            )
            if conflict is not None:
                raise ValueError(f"CERI_CERTIFICATION_PROVIDER_IDENTITY_CONFLICT:{symbol}")
            values[normalized_provider] = identity
        resolved[symbol] = values
    return resolved


class CeriIdentityResolver:
    def __init__(
        self,
        *,
        companies: list[CeriCompany] | None = None,
        aliases: list[CeriCompanyAlias] | None = None,
    ) -> None:
        self._companies = companies
        self._aliases = aliases

    def prepare(self, db: Session) -> CeriIdentityResolver:
        """Materialize stable identity snapshots once for a normalization batch."""
        if self._companies is None:
            self._companies = [_company_snapshot(row) for row in _load_companies(db)]
        if self._aliases is None:
            self._aliases = [_alias_snapshot(row) for row in _load_aliases(db)]
        return self

    @source_mutation_writer(
        MutationDomain.CERI_SOURCE, "provider_source", mode=MutationSemanticMode.MAINTENANCE
    )
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


def _company_snapshot(row: CeriCompany) -> CeriCompany:
    return CeriCompany(
        id=row.id,
        ticker=row.ticker,
        exchange=row.exchange,
        company_name=row.company_name,
        cik=row.cik,
        current_provider_ids_json=dict(row.current_provider_ids_json or {}),
    )


def _alias_snapshot(row: CeriCompanyAlias) -> CeriCompanyAlias:
    return CeriCompanyAlias(
        id=row.id,
        company_id=row.company_id,
        provider=row.provider,
        alias_type=row.alias_type,
        alias_value=row.alias_value,
        exchange=row.exchange,
        valid_from=row.valid_from,
        valid_to=row.valid_to,
        source=row.source,
        confidence=row.confidence,
    )


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


def _provider_id(values: dict[str, Any], provider: str) -> str | None:
    candidate = next(
        (
            value
            for key, value in values.items()
            if str(key).strip().lower() == provider.strip().lower()
        ),
        None,
    )
    if isinstance(candidate, dict):
        candidate = candidate.get("id") or candidate.get("provider_company_id")
    return _upper(candidate)


def _provider_identity_maps_to_ticker(
    *,
    provider: str,
    provider_identity: str,
    ticker: str,
) -> bool:
    if provider == "eodhd":
        return provider_identity.split(".", 1)[0].upper() == ticker.upper()
    return True


def _provider_record_matches_identity(
    *,
    provider: str,
    dataset: str,
    provider_identity: str,
    provider_record_id: str,
) -> bool:
    if provider == "eodhd" and dataset in {"earnings", "estimates"}:
        record_id = str(provider_record_id).upper()
        expected = provider_identity.upper()
        return record_id == expected or record_id.startswith(expected + ":")
    return True


def _provider_identity_conflict(
    db: Session,
    *,
    company_id: int,
    provider: str,
    provider_identity: str,
) -> str | None:
    for company in db.scalars(select(CeriCompany).where(CeriCompany.id != company_id)):
        if _provider_id(company.current_provider_ids_json or {}, provider) == provider_identity:
            return f"Provider identity belongs to company {company.id}."
    alias_company_ids = set(
        db.scalars(
            select(CeriCompanyAlias.company_id).where(
                func.lower(CeriCompanyAlias.provider) == provider.lower(),
                CeriCompanyAlias.alias_type == "provider_company_id",
                func.upper(CeriCompanyAlias.alias_value) == provider_identity,
                CeriCompanyAlias.company_id != company_id,
            )
        )
    )
    if alias_company_ids:
        return "Provider identity alias belongs to company " + ",".join(
            str(value) for value in sorted(alias_company_ids)
        )
    return None


def _text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _upper(value: Any) -> str | None:
    text = _text(value)
    return text.upper() if text else None
