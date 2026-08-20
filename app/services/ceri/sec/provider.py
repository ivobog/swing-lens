from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.services.ceri.dtos import (
    CatalystRequest,
    CompanyQuery,
    EarningsRequest,
    EstimateRequest,
    GuidanceRequest,
    ProviderCapabilities,
    ProviderCompany,
    ProviderHealth,
    RawProviderRecord,
)
from app.services.ceri.enums import CeriDataset, CeriProviderCapability, ExportPolicy
from app.services.ceri.sec.client import SecClientConfig, SecEdgarClient
from app.services.ceri.sec.guidance_extractor import GuidanceExtractionService

GUIDANCE_FORMS = frozenset({"8-K", "10-Q", "10-K", "6-K", "20-F"})


@dataclass(frozen=True)
class SecGuidanceDocument:
    ticker: str
    cik: str
    accession_number: str
    document_name: str
    form: str
    filing_date: str


class SecCeriProvider:
    name = "sec"

    def __init__(
        self,
        *,
        client: SecEdgarClient | None = None,
        extractor: GuidanceExtractionService | None = None,
        guidance_lookback_days: int | None = None,
        guidance_max_documents_per_ticker: int | None = None,
    ) -> None:
        self.client = client or SecEdgarClient(
            config=SecClientConfig(
                user_agent=os.getenv("SEC_USER_AGENT", "SwingLens/0.1.0 operator@example.invalid"),
                requests_per_second=float(os.getenv("SEC_REQUESTS_PER_SECOND", "2")),
                timeout_seconds=int(os.getenv("SEC_HTTP_TIMEOUT_SECONDS", "30")),
            )
        )
        self.extractor = extractor or GuidanceExtractionService()
        self.guidance_lookback_days = guidance_lookback_days or int(
            os.getenv("SEC_GUIDANCE_LOOKBACK_DAYS", "730")
        )
        self.guidance_max_documents_per_ticker = guidance_max_documents_per_ticker or int(
            os.getenv("SEC_GUIDANCE_MAX_DOCUMENTS_PER_TICKER", "5")
        )
        self._ticker_ciks: dict[str, tuple[str, ...]] | None = None

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            self.name,
            frozenset(
                {
                    CeriProviderCapability.HEALTH,
                    CeriProviderCapability.IDENTITY,
                    CeriProviderCapability.GUIDANCE,
                    CeriProviderCapability.CATALYSTS,
                }
            ),
            frozenset({CeriDataset.GUIDANCE, CeriDataset.CATALYSTS}),
        )

    def health(self) -> ProviderHealth:
        healthy = self.client.failures == 0 or self.client.last_success_at is not None
        return ProviderHealth(
            self.name,
            healthy,
            datetime.now(UTC),
            f"{self.client.requests} requests",
            "SEC client ready" if healthy else "SEC requests are failing",
        )

    def safe_metadata(self) -> dict[str, Any]:
        metadata = {
            "provider": self.name,
            "user_agent_configured": bool(self.client.config.user_agent),
            "requests_per_second": self.client.config.requests_per_second,
            "timeout_seconds": self.client.config.timeout_seconds,
            "requests": self.client.requests,
            "failures": self.client.failures,
            "last_success_at": self.client.last_success_at.isoformat()
            if self.client.last_success_at
            else None,
        }
        stats = getattr(self.client, "stats", None)
        if callable(stats):
            snapshot = stats()
            metadata.update(
                {
                    "retries": snapshot.retries,
                    "timeouts": snapshot.timeouts,
                    "http_2xx": snapshot.http_2xx,
                    "http_403": snapshot.http_403,
                    "http_429": snapshot.http_429,
                    "http_5xx": snapshot.http_5xx,
                    "company_ticker_requests": snapshot.company_ticker_requests,
                    "submissions_requests": snapshot.submissions_requests,
                    "filing_document_requests": snapshot.filing_document_requests,
                    "other_requests": snapshot.other_requests,
                    "bytes_downloaded": snapshot.bytes_downloaded,
                    "pacing_sleep_ms": snapshot.pacing_sleep_ms,
                    "retry_sleep_ms": snapshot.retry_sleep_ms,
                    "http_wait_ms": snapshot.http_wait_ms,
                }
            )
        return metadata

    def resolve_company(self, query: CompanyQuery) -> list[ProviderCompany]:
        if not query.cik:
            return []
        return [
            ProviderCompany(
                self.name,
                str(query.cik).zfill(10),
                query.ticker or "",
                query.exchange,
                str(query.cik).zfill(10),
            )
        ]

    def fetch_estimate_snapshots(self, request: EstimateRequest) -> Iterable[RawProviderRecord]:
        return iter(())

    def fetch_earnings_actuals(self, request: EarningsRequest) -> Iterable[RawProviderRecord]:
        return iter(())

    def fetch_guidance(self, request: GuidanceRequest) -> Iterable[RawProviderRecord]:
        records: list[RawProviderRecord] = []
        for document in self.discover_guidance_documents(request):
            records.extend(self.extract_guidance_document(document))
        return iter(records)

    def resolve_cik(self, ticker: str) -> str | None:
        candidates = self.resolve_cik_candidates(ticker)
        return candidates[0] if len(candidates) == 1 else None

    def resolve_cik_candidates(self, ticker: str) -> tuple[str, ...]:
        if self._ticker_ciks is None:
            grouped: dict[str, set[str]] = {}
            for row in self.client.company_tickers().values():
                if not isinstance(row, dict) or not row.get("ticker"):
                    continue
                raw_cik = row.get("cik_str")
                if raw_cik is None:
                    continue
                symbol = str(row["ticker"]).strip().upper()
                cik = str(raw_cik).split(".")[0].zfill(10)
                grouped.setdefault(symbol, set()).add(cik)
            self._ticker_ciks = {
                symbol: tuple(sorted(ciks)) for symbol, ciks in grouped.items()
            }
        return self._ticker_ciks.get(ticker.upper(), ())

    def discover_guidance_documents(
        self, request: GuidanceRequest, *, cik: str | None = None
    ) -> tuple[SecGuidanceDocument, ...]:
        resolved_cik = cik or self._cik_for_ticker(request.ticker)
        if resolved_cik is None:
            return ()
        submissions = self.client.submissions(resolved_cik)
        recent = submissions.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accessions = recent.get("accessionNumber", [])
        documents = recent.get("primaryDocument", [])
        filing_dates = recent.get("filingDate", [])
        upper_bound = request.end or datetime.now(UTC).date()
        lower_bound = request.start or (upper_bound - timedelta(days=self.guidance_lookback_days))
        selected: list[SecGuidanceDocument] = []
        for form, accession, document, filing_date in zip(
            forms, accessions, documents, filing_dates, strict=False
        ):
            if form not in GUIDANCE_FORMS:
                continue
            parsed_filing_date = _parse_filing_date(filing_date)
            if parsed_filing_date is None:
                continue
            if parsed_filing_date < lower_bound or parsed_filing_date > upper_bound:
                continue
            selected.append(
                SecGuidanceDocument(
                    ticker=request.ticker.upper(),
                    cik=str(resolved_cik).zfill(10),
                    accession_number=str(accession),
                    document_name=str(document),
                    form=str(form),
                    filing_date=str(filing_date),
                )
            )
        selected.sort(
            key=lambda item: (item.filing_date, item.accession_number, item.document_name),
            reverse=True,
        )
        # Preserve form diversity (latest 8-K, 10-Q, 10-K, 6-K, 20-F as
        # applicable), then fill the remaining bound with the newest filings.
        prioritized: list[SecGuidanceDocument] = []
        seen_forms: set[str] = set()
        for item in selected:
            if item.form not in seen_forms:
                prioritized.append(item)
                seen_forms.add(item.form)
        prioritized.extend(item for item in selected if item not in prioritized)
        return tuple(prioritized[: self.guidance_max_documents_per_ticker])

    def download_guidance_document(self, document: SecGuidanceDocument) -> str:
        return self.client.archive_document(
            document.cik, document.accession_number, document.document_name
        )

    def extract_guidance_document(
        self, document: SecGuidanceDocument, *, text: str | None = None
    ) -> tuple[RawProviderRecord, ...]:
        filing_text = text if text is not None else self.download_guidance_document(document)
        records: list[RawProviderRecord] = []
        locator = f"{document.accession_number}/{document.document_name}"
        for extraction in self.extractor.extract(filing_text, locator=locator):
            payload = {
                "ticker": document.ticker,
                "provider_company_id": document.cik,
                "cik": document.cik,
                "action": "UNKNOWN",
                "management_claim": extraction.management_claim,
                "metric": extraction.metric,
                "period_type": extraction.period_label,
                "low_value": extraction.low_value,
                "high_value": extraction.high_value,
                "point_value": extraction.point_value,
                "unit": extraction.unit
                or _unit_from_text(extraction.matched_text, metric=extraction.metric),
                "currency": extraction.currency,
                "confidence": extraction.confidence,
                "extraction_confidence": extraction.confidence,
                "comparison_confidence": extraction.comparison_confidence,
                "manual_review_required": extraction.management_claim is not None,
                "announced_at": f"{document.filing_date}T00:00:00+00:00",
                "source_date": document.filing_date,
                "comparison_basis": extraction.matched_text,
                "source_reference": extraction.evidence_locator,
                "evidence_locator": extraction.evidence_locator,
                "filing_accession": document.accession_number,
                "source_timestamp": f"{document.filing_date}T00:00:00+00:00",
            }
            record_id = f"{document.accession_number}:{extraction.evidence_locator}"
            records.append(
                RawProviderRecord(
                    self.name,
                    CeriDataset.GUIDANCE,
                    record_id,
                    payload,
                    _date_time(document.filing_date),
                    _date_time(document.filing_date),
                    None,
                    ExportPolicy.RESTRICTED.value,
                )
            )
        return tuple(records)

    def fetch_catalysts(self, request: CatalystRequest) -> Iterable[RawProviderRecord]:
        return iter(())

    def _cik_for_ticker(self, ticker: str) -> str | None:
        return self.resolve_cik(ticker)


def _date_time(value: Any) -> datetime:
    return datetime.fromisoformat(str(value)[:10]).replace(tzinfo=UTC)


def _parse_filing_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _unit_from_text(text: str, *, metric: str | None = None) -> str | None:
    lowered = text.lower()
    if metric == "EPS_DILUTED" and any(
        token in lowered for token in ("per share", "diluted share", "eps")
    ):
        return "PER_SHARE"
    for unit in ("billion", "million", "%"):
        if unit in lowered:
            return unit
    return None
