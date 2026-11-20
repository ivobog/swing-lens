"""Inactive, capture-scoped SEC authority for new EODHD revenue observations."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.ceri_tables import (
    CeriCompany,
    CeriCompanyAlias,
    CeriIngestionRun,
    CeriSecFilingDocument,
    CeriSourceRecord,
)
from app.services.ceri.dtos import RawProviderRecord
from app.services.ceri.enums import CeriSourceProjection
from app.services.ceri.pit_eligibility import (
    source_record_historical_eligibility_at,
    source_record_known_at,
)
from app.services.ceri.provider_registry import SEC_REPORTING_CURRENCY_FIELDS
from app.services.ceri.sec.content_cache import SecDocumentContentCache
from app.services.ceri.sec.issuer_succession import effective_symbol, ownership_conflict
from app.services.ceri.sec.provider import SecGuidanceDocument
from app.services.ceri.sec.statement_currency import CURRENCIES, extract_presentation_currency
from app.services.ceri.source_record_service import (
    CeriSourceRecordService,
    source_record_content_hash,
)

POLICY = "sec-reporting-currency-v1"
DATASET = CeriSourceProjection.SEC_REPORTING_CURRENCY.value
PILOT = frozenset({"AAPL.US", "MSFT.US", "AMZN.US", "ABBV.US", "SPOT.US", "LIN.US", "HG.US"})
PERIODIC = frozenset({"10-K", "10-Q", "20-F", "40-F", "6-K"})
CANDIDATE_FORMS = PERIODIC | {f"{form}/A" for form in PERIODIC}
ANNUAL = frozenset({"ANNUAL", "CURRENT_FISCAL_YEAR", "NEXT_FISCAL_YEAR"})
QUARTERLY = frozenset({"QUARTERLY", "CURRENT_QUARTER", "NEXT_QUARTER"})


@dataclass(frozen=True)
class StatementReview:
    """Exact SEC filing selection for one identified issuer's new capture."""

    document: CeriSecFilingDocument
    fiscal_end: date
    period_family: str
    submissions: dict
    submissions_received_at: datetime


@dataclass(frozen=True)
class CurrencyBinding:
    currency: str
    source_record_id: int
    known_at: datetime


def _time(value: object) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if isinstance(value, datetime) and value.utcoffset() is not None:
        return value
    return None


def _matching_filing(review: StatementReview, company: CeriCompany) -> datetime | None:
    document = review.document
    if document.cik != company.cik:
        return None
    if str(review.submissions.get("cik")).zfill(10) != company.cik:
        return None
    if company.ticker not in review.submissions.get("tickers", []):
        return None
    recent = review.submissions.get("filings", {}).get("recent", {})
    keys = ("accessionNumber", "primaryDocument", "form", "acceptanceDateTime", "reportDate")
    columns = [recent.get(key, []) for key in keys]
    if not columns[0] or len({len(col) for col in columns}) != 1:
        return None
    relevant = [
        (acc, name, form, _time(when), report_date)
        for acc, name, form, when, report_date in zip(*columns, strict=True)
        if form in CANDIDATE_FORMS
    ]
    if any(when is None for _, _, _, when, _ in relevant):
        return None
    matches = [
        when
        for acc, name, form, when, report_date in relevant
        if (acc, name, form) == (document.accession_number, document.document_name, document.form)
        and (form == "6-K" or report_date == review.fiscal_end.isoformat())
    ]
    if len(matches) != 1:
        return None
    accepted = matches[0]
    if any(
        accepted <= when <= review.submissions_received_at and acc != document.accession_number
        for acc, _, _, when, _ in relevant
    ):
        return None
    return accepted


def project_statement(
    *,
    run: CeriIngestionRun,
    company: CeriCompany,
    review: StatementReview,
    cache_dir: Path,
    derived_at: datetime,
) -> RawProviderRecord | None:
    """Verify cached bytes before creating a new, non-backdated source projection."""
    document = review.document
    started = _time(run.started_at)
    discovery = _time(review.submissions_received_at)
    receipt = _time(document.last_downloaded_at)
    if not (
        run.id
        and run.provider == "eodhd"
        and run.dataset == "estimates"
        and run.status == "RUNNING"
        and f"{company.ticker}.US" in PILOT
        and company.cik
        and company.sec_applicability == "REQUIRED"
        and started
        and discovery
        and receipt
        and _time(derived_at)
        and started <= discovery <= derived_at
        and receipt <= discovery
        and document.form in PERIODIC
        and document.filing_date
        and review.fiscal_end <= document.filing_date <= discovery.date()
        and document.last_content_hash
        and document.last_content_bytes
        and 0 < document.last_content_bytes <= 16_000_000
        and review.period_family in {"ANNUAL", "QUARTERLY"}
        and recheck_family(document.form, review.period_family)
    ):
        return None
    accepted = _matching_filing(review, company)
    if not accepted or accepted > receipt:
        return None
    reporting_entity = review.submissions.get("name")
    if not isinstance(reporting_entity, str) or not reporting_entity.strip():
        return None
    item = SecGuidanceDocument(
        company.ticker,
        company.cik,
        document.accession_number,
        document.document_name,
        document.form,
        document.filing_date.isoformat(),
    )
    body = SecDocumentContentCache(cache_dir).load(item, expected_hash=document.last_content_hash)
    if body is None:
        return None
    currency = extract_presentation_currency(
        body,
        cik=company.cik,
        fiscal_end=review.fiscal_end,
        annual=review.period_family == "ANNUAL",
        reviewed_interim=document.form == "6-K",
    )
    if not currency:
        return None
    payload = {
        "policy_version": POLICY,
        "ticker": company.ticker,
        "provider_company_id": f"{company.ticker}.US",
        "company_id": company.id,
        "cik": company.cik,
        "reporting_entity": reporting_entity,
        "eodhd_ingestion_run_id": run.id,
        "filing_registry_id": document.id,
        "filing_accession": document.accession_number,
        "form": document.form,
        "document_name": document.document_name,
        "fiscal_period_end": review.fiscal_end.isoformat(),
        "period_family": review.period_family,
        "currency": currency,
        "comparison_basis": "consolidated_primary_income_statement",
        "document_sha256": document.last_content_hash,
        "filing_date": document.filing_date.isoformat(),
        "accepted_at": accepted.isoformat(),
        "document_received_at": receipt.isoformat(),
        "submissions_received_at": discovery.isoformat(),
        "submissions_sha256": source_record_content_hash(review.submissions),
    }
    return RawProviderRecord(
        provider="sec",
        dataset=CeriSourceProjection.SEC_REPORTING_CURRENCY,
        provider_record_id=f"{POLICY}:{run.id}:{company.cik}:{document.accession_number}",
        payload=payload,
        published_at=accepted,
        observed_at=None,
        retrieved_at=derived_at,
        export_policy="restricted",
    )


def recheck_family(form: str, family: str) -> bool:
    return (form in {"10-Q", "6-K"} and family == "QUARTERLY") or (
        form in {"10-K", "20-F", "40-F"} and family == "ANNUAL"
    )


def _issuer_conflict(db: Session, company: CeriCompany, as_of: date) -> bool:
    if ownership_conflict(db, company.ticker, company.cik):
        return True
    aliases = db.scalars(
        select(CeriCompanyAlias.alias_value).where(
            CeriCompanyAlias.company_id == company.id,
            CeriCompanyAlias.provider == "sec",
            CeriCompanyAlias.alias_type == "cik",
            or_(CeriCompanyAlias.valid_from.is_(None), CeriCompanyAlias.valid_from <= as_of),
            or_(CeriCompanyAlias.valid_to.is_(None), CeriCompanyAlias.valid_to >= as_of),
        )
    )
    expected = effective_symbol(company.cik, as_of)
    return bool(expected and expected != company.ticker) or any(
        str(alias).zfill(10) != company.cik for alias in aliases
    )


def register_statement(
    db: Session,
    *,
    run_id: int,
    company_id: int,
    review: StatementReview,
    cache_dir: Path,
) -> int | None:
    """Use the existing SEC/CERI source writer in an explicitly authorized transaction."""
    run = db.get(CeriIngestionRun, run_id)
    company = db.get(CeriCompany, company_id)
    document = db.get(CeriSecFilingDocument, review.document.id)
    if not run or not company or document is not review.document or not company.cik:
        return None
    if _issuer_conflict(db, company, datetime.now(UTC).date()):
        return None
    record = project_statement(
        run=run,
        company=company,
        review=review,
        cache_dir=cache_dir,
        derived_at=datetime.now(UTC),
    )
    if record is None:
        return None
    result = CeriSourceRecordService().store_source_record(
        db,
        ingestion_run_id=None,
        record=replace(record, retrieved_at=datetime.now(UTC)),
        raw_payload_allowed=False,
    )
    return None if result.quarantined else result.source_record.id


@dataclass(frozen=True)
class _PreparedAuthority:
    source: CeriSourceRecord
    earliest_competing_filing: datetime | None
    superseded_at: datetime | None = None


class SecRevenueCurrencyCapture:
    """Explicit opt-in for one new EODHD acquisition. No live caller constructs it."""

    def __init__(self, *, ingestion_run_id: int, authority_source_ids: tuple[int, ...]) -> None:
        if len(authority_source_ids) > len(PILOT):
            raise ValueError("SEC currency capture exceeds the pilot scope")
        self.ingestion_run_id = ingestion_run_id
        self.authority_source_ids = authority_source_ids

    def prepare(self, db: Session) -> dict[int, _PreparedAuthority]:
        if not self.authority_source_ids:
            return {}
        prepared = {}
        duplicates = set()
        replacement_times = dict(
            db.execute(
                select(
                    CeriSourceRecord.supersedes_id,
                    func.min(
                        func.coalesce(CeriSourceRecord.retrieved_at, CeriSourceRecord.ingested_at)
                    ),
                )
                .where(CeriSourceRecord.supersedes_id.in_(self.authority_source_ids))
                .group_by(CeriSourceRecord.supersedes_id)
            ).all()
        )
        sources = db.scalars(
            select(CeriSourceRecord).where(
                CeriSourceRecord.id.in_(self.authority_source_ids),
                CeriSourceRecord.provider == "sec",
                CeriSourceRecord.dataset == DATASET,
            )
        )
        for source in sources:
            raw = source.restricted_normalized_json or {}
            if (
                source.quarantine_reason
                or source.supersedes_id
                or raw.get("policy_version") != POLICY
                or raw.get("eodhd_ingestion_run_id") != self.ingestion_run_id
                or raw.get("currency") not in CURRENCIES
                or raw.keys() != SEC_REPORTING_CURRENCY_FIELDS
                or raw["comparison_basis"] != "consolidated_primary_income_statement"
                or source.content_hash != source_record_content_hash(raw)
            ):
                continue
            company = db.get(CeriCompany, raw["company_id"])
            document = db.get(CeriSecFilingDocument, raw["filing_registry_id"])
            accepted = _time(raw["accepted_at"])
            receipt = _time(raw["document_received_at"])
            discovery = _time(raw["submissions_received_at"])
            known = source_record_known_at(source)
            if not (
                company
                and company.cik == raw["cik"]
                and company.sec_applicability == "REQUIRED"
                and f"{company.ticker}.US" in PILOT
                and f"{company.ticker}.US" == raw["provider_company_id"]
                and accepted
                and receipt
                and discovery
                and known
                and accepted == source.published_at
                and accepted <= receipt <= discovery <= known
                and document
                and document.cik == raw["cik"]
                and document.accession_number == raw["filing_accession"]
                and document.document_name == raw["document_name"]
                and document.form == raw["form"]
                and document.last_content_hash == raw["document_sha256"]
                and document.last_downloaded_at == receipt
                and document.filing_date
                and document.filing_date.isoformat() == raw["filing_date"]
                and recheck_family(raw["form"], raw["period_family"])
                and not _issuer_conflict(db, company, known.date())
            ):
                continue
            if company.id in prepared:
                duplicates.add(company.id)
            first_new = db.scalar(
                select(func.min(CeriSecFilingDocument.first_seen_at)).where(
                    CeriSecFilingDocument.cik == company.cik,
                    CeriSecFilingDocument.form.in_(sorted(CANDIDATE_FORMS)),
                    CeriSecFilingDocument.filing_date >= document.filing_date,
                    CeriSecFilingDocument.accession_number != raw["filing_accession"],
                )
            )
            prepared[company.id] = _PreparedAuthority(
                source, first_new, replacement_times.get(source.id)
            )
        return {
            company: authority
            for company, authority in prepared.items()
            if company not in duplicates
        }

    def resolve(
        self, source: CeriSourceRecord, authority: _PreparedAuthority | None
    ) -> CurrencyBinding | None:
        if (
            authority is None
            or source.provider != "eodhd"
            or source.dataset != "estimates"
            or source.ingestion_run_id != self.ingestion_run_id
            or source.quarantine_reason
        ):
            return None
        estimate = source.raw_json or source.restricted_normalized_json or {}
        sec = authority.source
        details = sec.restricted_normalized_json
        symbol = estimate.get("provider_company_id")
        if (
            estimate.get("metric") != "REVENUE"
            or symbol not in PILOT
            or symbol != details["provider_company_id"]
            or not (
                source.provider_record_id == symbol
                or source.provider_record_id.startswith(symbol + ":")
            )
            or estimate.get("trend_baseline_window_days") is not None
            or estimate.get("baseline_origin")
            or any(
                estimate.get(key) is not None
                for key in (
                    "source_currency",
                    "currency",
                    "canonical_currency",
                    "currency_basis",
                    "conversion_source",
                    "conversion_rate",
                    "conversion_source_record_id",
                )
            )
        ):
            return None
        try:
            if any(
                Decimal(str(estimate[key])) != Decimal(1)
                for key in ("source_scale", "canonical_scale", "scale")
                if estimate.get(key) is not None
            ):
                return None
        except InvalidOperation:
            return None
        observed = _time(source.retrieved_at)
        known = source_record_historical_eligibility_at(sec)
        if (
            not observed
            or not known
            or known > observed
            or (
                authority.earliest_competing_filing
                and authority.earliest_competing_filing <= observed
            )
            or (authority.superseded_at and authority.superseded_at <= observed)
        ):
            return None
        label = estimate.get("period_type")
        if label not in ANNUAL | QUARTERLY:
            return None
        try:
            target = date.fromisoformat(estimate["fiscal_period_end"])
            statement_end = date.fromisoformat(details["fiscal_period_end"])
        except (KeyError, TypeError, ValueError):
            return None
        family = "ANNUAL" if label in ANNUAL else "QUARTERLY"
        if target <= observed.date() and (
            target != statement_end or family != details["period_family"]
        ):
            return None
        return CurrencyBinding(details["currency"], sec.id, known)
