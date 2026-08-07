from __future__ import annotations

import os
from collections.abc import Iterable
from datetime import UTC, datetime
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


class SecCeriProvider:
    name = "sec"

    def __init__(
        self,
        *,
        client: SecEdgarClient | None = None,
        extractor: GuidanceExtractionService | None = None,
    ) -> None:
        self.client = client or SecEdgarClient(
            config=SecClientConfig(
                user_agent=os.getenv("SEC_USER_AGENT", "SwingLens/0.1.0 operator@example.invalid"),
                requests_per_second=float(os.getenv("SEC_REQUESTS_PER_SECOND", "2")),
                timeout_seconds=int(os.getenv("SEC_HTTP_TIMEOUT_SECONDS", "30")),
            )
        )
        self.extractor = extractor or GuidanceExtractionService()
        self._ticker_cik: dict[str, str] | None = None

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
        return {
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
        cik = self._cik_for_ticker(request.ticker)
        if cik is None:
            return iter(())
        submissions = self.client.submissions(cik)
        recent = submissions.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accessions = recent.get("accessionNumber", [])
        documents = recent.get("primaryDocument", [])
        filing_dates = recent.get("filingDate", [])
        records: list[RawProviderRecord] = []
        for form, accession, document, filing_date in zip(
            forms, accessions, documents, filing_dates, strict=False
        ):
            if form not in {"8-K", "10-Q", "10-K", "6-K", "20-F"}:
                continue
            if request.start and str(filing_date)[:10] < request.start.isoformat():
                continue
            if request.end and str(filing_date)[:10] > request.end.isoformat():
                continue
            text = self.client.archive_document(cik, accession, document)
            for extraction in self.extractor.extract(text, locator=f"{accession}/{document}"):
                payload = {
                    "ticker": request.ticker.upper(),
                    "provider_company_id": cik,
                    "cik": cik,
                    "action": "UNKNOWN",
                    "management_claim": extraction.management_claim,
                    "metric": extraction.metric,
                    "period_type": extraction.period_label,
                    "low_value": extraction.low_value,
                    "high_value": extraction.high_value,
                    "point_value": extraction.point_value,
                    "unit": _unit_from_text(extraction.matched_text),
                    "confidence": extraction.confidence,
                    "extraction_confidence": extraction.confidence,
                    "comparison_confidence": extraction.comparison_confidence,
                    "manual_review_required": extraction.management_claim is not None,
                    "announced_at": f"{filing_date}T00:00:00+00:00",
                    "source_date": str(filing_date),
                    "comparison_basis": extraction.matched_text,
                    "source_reference": extraction.evidence_locator,
                    "evidence_locator": extraction.evidence_locator,
                    "filing_accession": accession,
                    "source_timestamp": f"{filing_date}T00:00:00+00:00",
                }
                record_id = f"{accession}:{extraction.evidence_locator}"
                records.append(
                    RawProviderRecord(
                        self.name,
                        CeriDataset.GUIDANCE,
                        record_id,
                        payload,
                        _date_time(filing_date),
                        _date_time(filing_date),
                        None,
                        ExportPolicy.RESTRICTED.value,
                    )
                )
        return iter(records)

    def fetch_catalysts(self, request: CatalystRequest) -> Iterable[RawProviderRecord]:
        return iter(())

    def _cik_for_ticker(self, ticker: str) -> str | None:
        if self._ticker_cik is None:
            raw = self.client.company_tickers()
            self._ticker_cik = {
                str(row.get("ticker", "")).upper(): str(row.get("cik_str", ""))
                .split(".")[0]
                .zfill(10)
                for row in raw.values()
                if isinstance(row, dict) and row.get("ticker") and row.get("cik_str") is not None
            }
        return self._ticker_cik.get(ticker.upper())


def _date_time(value: Any) -> datetime:
    return datetime.fromisoformat(str(value)[:10]).replace(tzinfo=UTC)


def _unit_from_text(text: str) -> str | None:
    lowered = text.lower()
    for unit in ("billion", "million", "%"):
        if unit in lowered:
            return unit
    return None
