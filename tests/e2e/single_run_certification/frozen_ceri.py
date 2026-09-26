from __future__ import annotations

from app.services.ceri.enums import CeriDataset
from app.services.ceri.provider_registry import CeriProviderRegistry
from app.services.ceri.providers.manual_provider import ManualCeriProvider

EXACT_TEN = ("BHE", "BLLN", "KLIC", "LSCC", "PDFS", "ACMR", "RDVT", "AVT", "DVN", "JNJ")


def frozen_ceri_registry() -> CeriProviderRegistry:
    """Return deterministic external-provider responses for the retained-clone proof."""

    tickers = tuple(dict.fromkeys((*EXACT_TEN, *(f"F{index:04d}" for index in range(90)))))
    observed = "2026-09-23T19:00:00Z"
    records = {dataset: [] for dataset in CeriDataset}
    for index, ticker in enumerate(tickers, start=1):
        base = 1.0 + index / 100.0
        # Cover each configured revision window with observations attributable
        # to a completed exchange session.
        for suffix, consensus, published in (
            ("90d", base, "2026-06-26T19:00:00Z"),
            ("30d", base * 1.04, "2026-08-25T19:00:00Z"),
            ("7d", base * 1.08, "2026-09-17T19:00:00Z"),
            ("current", base * 1.12, "2026-09-24T19:00:00Z"),
        ):
            records[CeriDataset.ESTIMATES].append(
                {
                    "provider_record_id": f"{ticker}-eps-{suffix}",
                    "provider_company_id": f"{ticker}.US",
                    "ticker": ticker,
                    "exchange": "US",
                    "metric": "EPS_DILUTED",
                    "period_type": "CURRENT_FISCAL_YEAR",
                    "fiscal_period_end": "2026-12-31",
                    "consensus": str(round(consensus, 4)),
                    "high": str(round(consensus * 1.1, 4)),
                    "low": str(round(consensus * 0.9, 4)),
                    "analyst_count": 10 + index,
                    "upward_count": 8,
                    "downward_count": 1,
                    "currency": "USD",
                    "published_at": published,
                    "observed_at": published,
                }
            )
        records[CeriDataset.GUIDANCE].append(
            {
                "provider_record_id": f"{ticker}-sec-guidance",
                "ticker": ticker,
                "action": "RAISED",
                "metric": "REVENUE",
                "period_type": "CURRENT_FISCAL_YEAR",
                "point": str(100 + index),
                "currency": "USD",
                "confidence": "high",
                "announced_at": observed,
                "published_at": observed,
                "filing_accession": f"0000000000-26-{index:06d}",
                "source_reference": f"frozen-sec:{ticker}",
            }
        )
        records[CeriDataset.CATALYSTS].append(
            {
                "provider_record_id": f"{ticker}-catalyst",
                "ticker": ticker,
                "category": "PRODUCT",
                "subtype": "launch",
                "subject_key": f"{ticker.lower()}-launch",
                "canonical_text": "Frozen provider product launch",
                "status": "ANNOUNCED",
                "direction": "POSITIVE",
                "materiality": 0.7,
                "announced_at": observed,
                "expected_date": "2026-10-15",
                "date_confidence": "EXACT_DATE",
                "source_confidence": "HIGH",
                "published_at": observed,
            }
        )

    class FrozenEodhdProvider(ManualCeriProvider):
        name = "eodhd"

    class FrozenSecProvider(ManualCeriProvider):
        name = "sec"

    return CeriProviderRegistry(
        providers={
            "eodhd": FrozenEodhdProvider(
                {
                    CeriDataset.ESTIMATES: records[CeriDataset.ESTIMATES],
                    CeriDataset.EARNINGS: records[CeriDataset.EARNINGS],
                    CeriDataset.CATALYSTS: records[CeriDataset.CATALYSTS],
                }
            ),
            "sec": FrozenSecProvider({CeriDataset.GUIDANCE: records[CeriDataset.GUIDANCE]}),
        }
    )
