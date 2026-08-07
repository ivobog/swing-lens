from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from app.services.ceri.dtos import CatalystRequest, CompanyQuery, EarningsRequest, EstimateRequest
from app.services.ceri.enums import CeriMetric, CeriPeriodType, CeriProviderCapability
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
    unique_sample_size: int = 0
    duplicate_provider_records: int = 0
    invalid_estimate_ranges: int = 0
    anomalies: int = 0
    missing_earnings_dates: int = 0
    missing_earnings_actuals: int = 0
    ready: bool = False
    blocking_reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "errors": list(self.errors)}


class CeriProviderValidationService:
    """Runs the pre-alert provider audition using fixtures or a live adapter."""

    def validate(
        self, provider: CeriProvider, tickers: tuple[str, ...] = DEFAULT_VALIDATION_SAMPLE
    ) -> CeriValidationSummary:
        started = datetime.now(UTC)
        normalized_tickers = tuple(
            dict.fromkeys(ticker.strip().upper() for ticker in tickers if ticker.strip())
        )
        identity = estimates = earnings = catalysts = coverage = missing_consensus = (
            missing_analysts
        ) = missing_baselines = 0
        duplicate_provider_records = invalid_estimate_ranges = anomalies = 0
        missing_earnings_dates = missing_earnings_actuals = 0
        errors: list[dict[str, Any]] = []
        estimate_capable = _supports(provider, CeriProviderCapability.ESTIMATES)
        provider_record_ids: set[tuple[str, str, str]] = set()
        for ticker in normalized_tickers:
            try:
                identities = list(
                    provider.resolve_company(CompanyQuery(ticker=ticker, exchange="NASDAQ"))
                )
                identity += int(bool(identities))
                if len(identities) != len({
                    (item.provider_company_id, item.exchange, item.cik) for item in identities
                }):
                    anomalies += 1
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
                    record_key = (ticker, record.dataset.value, record.provider_record_id)
                    if record_key in provider_record_ids:
                        duplicate_provider_records += 1
                    provider_record_ids.add(record_key)
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
                    if (
                        record.payload.get("high") is not None
                        and record.payload.get("low") is not None
                    ):
                        try:
                            if float(record.payload["high"]) < float(record.payload["low"]):
                                invalid_estimate_ranges += 1
                        except (TypeError, ValueError):
                            anomalies += 1
                    if record.payload.get("analyst_count") is not None:
                        try:
                            if int(record.payload["analyst_count"]) < 0:
                                anomalies += 1
                        except (TypeError, ValueError):
                            anomalies += 1
                earnings_records = list(
                    provider.fetch_earnings_actuals(EarningsRequest(None, ticker))
                )
                earnings += len(earnings_records)
                for record in earnings_records:
                    record_key = (ticker, record.dataset.value, record.provider_record_id)
                    if record_key in provider_record_ids:
                        duplicate_provider_records += 1
                    provider_record_ids.add(record_key)
                    if not any(
                        record.payload.get(key) not in (None, "")
                        for key in ("report_at", "source_date")
                    ):
                        missing_earnings_dates += 1
                    if record.payload.get("actual_value") in (None, ""):
                        missing_earnings_actuals += 1
                catalysts += len(list(provider.fetch_catalysts(CatalystRequest(None, ticker))))
            except Exception as exc:
                errors.append({"ticker": ticker, "error": str(exc).replace("\n", " ")[:300]})
        completed = datetime.now(UTC)
        blocking_reasons: list[str] = []
        if errors:
            blocking_reasons.append("provider_errors")
        if identity != len(normalized_tickers):
            blocking_reasons.append("identity_coverage_incomplete")
        if estimate_capable and coverage != len(normalized_tickers):
            blocking_reasons.append("estimate_coverage_incomplete")
        if estimate_capable and missing_consensus:
            blocking_reasons.append("consensus_missing")
        if invalid_estimate_ranges:
            blocking_reasons.append("estimate_ranges_invalid")
        if duplicate_provider_records:
            blocking_reasons.append("duplicate_provider_records")
        return CeriValidationSummary(
            provider=provider.name,
            started_at=started,
            completed_at=completed,
            sample_size=len(normalized_tickers),
            identity_successes=identity,
            estimate_records=estimates,
            earnings_records=earnings,
            catalyst_records=catalysts,
            estimate_coverage=coverage,
            missing_consensus=missing_consensus,
            missing_analyst_counts=missing_analysts,
            missing_baselines=missing_baselines,
            errors=tuple(errors),
            unique_sample_size=len(normalized_tickers),
            duplicate_provider_records=duplicate_provider_records,
            invalid_estimate_ranges=invalid_estimate_ranges,
            anomalies=anomalies,
            missing_earnings_dates=missing_earnings_dates,
            missing_earnings_actuals=missing_earnings_actuals,
            ready=not blocking_reasons,
            blocking_reasons=tuple(blocking_reasons),
        )


def _supports(provider: CeriProvider, capability: CeriProviderCapability) -> bool:
    try:
        return provider.capabilities().supports(capability)
    except (AttributeError, TypeError):
        return True
