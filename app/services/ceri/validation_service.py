from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from app.services.ceri.dtos import CatalystRequest, CompanyQuery, EarningsRequest, EstimateRequest
from app.services.ceri.enums import CeriMetric, CeriPeriodType
from app.services.ceri.provider_protocol import CeriProvider

DEFAULT_VALIDATION_SAMPLE = (
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "META",
    "JPM",
    "XOM",
    "UNH",
    "WMT",
    "COST",
    "ADBE",
    "INTU",
    "CAT",
    "DE",
    "MU",
    "PLTR",
    "SNOW",
    "RIVN",
    "SOFI",
    "IONQ",
    "AVGO",
    "ORCL",
    "CRM",
    "QCOM",
    "CSCO",
    "GE",
    "RTX",
    "LMT",
    "BA",
    "NKE",
    "SBUX",
    "TGT",
    "LOW",
    "F",
    "GM",
    "PYPL",
    "SQ",
    "SHOP",
    "ABNB",
    "UBER",
    "DDOG",
    "NET",
    "CRWD",
    "MARA",
    "DKNG",
    "CVNA",
    "GME",
    "BBAI",
    "HIMS",
    "RKLB",
)


@dataclass(frozen=True)
class CeriValidationSummary:
    provider: str
    started_at: datetime
    completed_at: datetime
    sample_size: int
    identity_successes: int
    estimate_records: int
    earnings_records: int
    catalyst_records: int
    estimate_coverage: int
    missing_consensus: int
    missing_analyst_counts: int
    missing_baselines: int
    errors: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "errors": list(self.errors)}


class CeriProviderValidationService:
    """Runs the pre-alert provider audition using fixtures or a live adapter."""

    def validate(
        self, provider: CeriProvider, tickers: tuple[str, ...] = DEFAULT_VALIDATION_SAMPLE
    ) -> CeriValidationSummary:
        started = datetime.now(UTC)
        identity = estimates = earnings = catalysts = coverage = missing_consensus = (
            missing_analysts
        ) = missing_baselines = 0
        errors: list[dict[str, Any]] = []
        for ticker in tuple(dict.fromkeys(ticker.upper() for ticker in tickers)):
            try:
                identity += int(
                    bool(provider.resolve_company(CompanyQuery(ticker=ticker, exchange="NASDAQ")))
                )
                records = list(
                    provider.fetch_estimate_snapshots(
                        EstimateRequest(
                            None,
                            ticker,
                            (CeriMetric.EPS_DILUTED, CeriMetric.REVENUE),
                            tuple(CeriPeriodType),
                        )
                    )
                )
                estimates += len(records)
                coverage += int(bool(records))
                for record in records:
                    if record.payload.get("consensus") in (None, ""):
                        missing_consensus += 1
                    if record.payload.get("analyst_count") in (None, ""):
                        missing_analysts += 1
                    if (
                        record.dataset.value == "estimates"
                        and record.payload.get("metric") == "EPS_DILUTED"
                        and not any(
                            record.payload.get(key) not in (None, "")
                            for key in (
                                "eps_trend_7d",
                                "eps_trend_30d",
                                "eps_trend_90d",
                                "trend_baseline_days",
                            )
                        )
                    ):
                        missing_baselines += 1
                earnings += len(
                    list(provider.fetch_earnings_actuals(EarningsRequest(None, ticker)))
                )
                catalysts += len(list(provider.fetch_catalysts(CatalystRequest(None, ticker))))
            except Exception as exc:
                errors.append({"ticker": ticker, "error": str(exc).replace("\n", " ")[:300]})
        completed = datetime.now(UTC)
        return CeriValidationSummary(
            provider.name,
            started,
            completed,
            len(tickers),
            identity,
            estimates,
            earnings,
            catalysts,
            coverage,
            missing_consensus,
            missing_analysts,
            missing_baselines,
            tuple(errors),
        )
