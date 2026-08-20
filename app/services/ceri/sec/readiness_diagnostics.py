from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriCompany, CeriSecSyncState
from app.services.ceri.enums import CeriDataset

_VALID_TICKER = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,15}$")


class SecTickerReadinessCategory(StrEnum):
    READY = "READY"
    CIK_MISSING = "CIK_MISSING"
    SYNC_STATE_MISSING = "SYNC_STATE_MISSING"
    SIGNATURE_MISMATCH = "SIGNATURE_MISMATCH"
    SEC_NOT_APPLICABLE = "SEC_NOT_APPLICABLE"
    UNRESOLVED_MAPPING = "UNRESOLVED_MAPPING"
    INVALID_TICKER = "INVALID_TICKER"
    OTHER_BLOCKING_REASON = "OTHER_BLOCKING_REASON"


@dataclass(frozen=True)
class SecTickerReadiness:
    ticker: str
    category: SecTickerReadinessCategory
    ciks: tuple[str, ...] = ()
    available_signatures: tuple[str, ...] = ()
    reason: str | None = None

    @property
    def accepted(self) -> bool:
        return self.category in {
            SecTickerReadinessCategory.READY,
            SecTickerReadinessCategory.SEC_NOT_APPLICABLE,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "category": self.category.value,
            "ciks": list(self.ciks),
            "available_signatures": list(self.available_signatures),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class SecUniverseReadiness:
    processor_signature: str
    tickers: tuple[SecTickerReadiness, ...]

    @property
    def requested_tickers(self) -> int:
        return len(self.tickers)

    @property
    def ready_tickers(self) -> int:
        return sum(item.accepted for item in self.tickers)

    @property
    def complete(self) -> bool:
        return self.ready_tickers == self.requested_tickers

    @property
    def blocking_tickers(self) -> tuple[str, ...]:
        return tuple(item.ticker for item in self.tickers if not item.accepted)

    def counts(self) -> dict[str, int]:
        counts = Counter(item.category.value for item in self.tickers)
        return {
            category.value: int(counts.get(category.value, 0))
            for category in SecTickerReadinessCategory
        }

    def as_dict(self, *, include_tickers: bool = True) -> dict[str, Any]:
        value: dict[str, Any] = {
            "processor_signature": self.processor_signature,
            "requested_tickers": self.requested_tickers,
            "ready_tickers": self.ready_tickers,
            "complete": self.complete,
            "counts": self.counts(),
            "blocking_tickers": list(self.blocking_tickers),
        }
        if include_tickers:
            value["tickers"] = [item.as_dict() for item in self.tickers]
        return value


def diagnose_sec_readiness(
    db: Session,
    *,
    tickers: Iterable[str],
    processor_signature: str,
) -> SecUniverseReadiness:
    symbols = tuple(sorted({str(value).strip().upper() for value in tickers if value}))
    valid_symbols = tuple(symbol for symbol in symbols if _VALID_TICKER.fullmatch(symbol))
    companies = list(
        db.scalars(select(CeriCompany).where(CeriCompany.ticker.in_(valid_symbols))).all()
    )
    by_ticker: dict[str, list[CeriCompany]] = {}
    for company in companies:
        by_ticker.setdefault(company.ticker.upper(), []).append(company)
    ciks = {str(company.cik) for company in companies if company.cik}
    sync_rows = (
        list(
            db.scalars(
                select(CeriSecSyncState).where(
                    CeriSecSyncState.cik.in_(ciks),
                    CeriSecSyncState.dataset == CeriDataset.GUIDANCE.value,
                )
            ).all()
        )
        if ciks
        else []
    )
    signatures_by_cik: dict[str, set[str]] = {}
    for row in sync_rows:
        signatures_by_cik.setdefault(str(row.cik), set()).add(row.processor_signature)

    diagnostics: list[SecTickerReadiness] = []
    for ticker in symbols:
        if not _VALID_TICKER.fullmatch(ticker):
            diagnostics.append(
                SecTickerReadiness(
                    ticker,
                    SecTickerReadinessCategory.INVALID_TICKER,
                    reason="Ticker is not in the supported canonical symbol format.",
                )
            )
            continue
        matching = by_ticker.get(ticker, [])
        if not matching:
            diagnostics.append(
                SecTickerReadiness(
                    ticker,
                    SecTickerReadinessCategory.UNRESOLVED_MAPPING,
                    reason="No CERI company mapping exists.",
                )
            )
            continue
        required = [
            company
            for company in matching
            if getattr(company, "sec_applicability", "REQUIRED") != "NOT_APPLICABLE"
        ]
        if not required:
            reasons = sorted(
                {
                    str(company.sec_applicability_reason)
                    for company in matching
                    if company.sec_applicability_reason
                }
            )
            diagnostics.append(
                SecTickerReadiness(
                    ticker,
                    SecTickerReadinessCategory.SEC_NOT_APPLICABLE,
                    reason="; ".join(reasons) or "Explicitly classified as SEC not applicable.",
                )
            )
            continue
        ticker_ciks = tuple(sorted({str(company.cik) for company in required if company.cik}))
        if not ticker_ciks:
            diagnostics.append(
                SecTickerReadiness(
                    ticker,
                    SecTickerReadinessCategory.CIK_MISSING,
                    reason="SEC applicability requires a persisted CIK.",
                )
            )
            continue
        available = tuple(
            sorted(
                {
                    signature
                    for cik in ticker_ciks
                    for signature in signatures_by_cik.get(cik, set())
                }
            )
        )
        missing_sync_state = any(not signatures_by_cik.get(cik) for cik in ticker_ciks)
        all_ciks_ready = all(
            processor_signature in signatures_by_cik.get(cik, set())
            for cik in ticker_ciks
        )
        if all_ciks_ready:
            category = SecTickerReadinessCategory.READY
            reason = None
        elif missing_sync_state:
            category = SecTickerReadinessCategory.SYNC_STATE_MISSING
            reason = "At least one required CIK has no successful SEC bootstrap sync state."
        elif available:
            category = SecTickerReadinessCategory.SIGNATURE_MISMATCH
            reason = "At least one required CIK is ready only for a different signature."
        else:
            category = SecTickerReadinessCategory.SYNC_STATE_MISSING
            reason = "No successful SEC bootstrap sync state exists."
        diagnostics.append(
            SecTickerReadiness(
                ticker=ticker,
                category=category,
                ciks=ticker_ciks,
                available_signatures=available,
                reason=reason,
            )
        )
    return SecUniverseReadiness(processor_signature, tuple(diagnostics))
