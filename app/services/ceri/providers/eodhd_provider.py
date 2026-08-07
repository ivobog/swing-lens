from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable
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
from app.services.ceri.providers.eodhd_client import EodhdClientConfig, EodhdHttpClient
from app.services.ceri.providers.eodhd_mapping import eodhd_symbol, period_type, provider_date
from app.settings import get_settings


class EodhdCeriProvider:
    name = "eodhd"
    credential_env_var = "EODHD_API_KEY"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: EodhdHttpClient | None = None,
        clock: Callable[[], datetime] | None = None,
        **config: Any,
    ) -> None:
        settings = get_settings()
        key = api_key if api_key is not None else settings.eodhd_api_key
        self.terms_version = str(
            config.pop("terms_version", settings.eodhd_terms_version)
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self.client = client or EodhdHttpClient(
            EodhdClientConfig(
                api_key=key,
                base_url=str(
                    config.pop("base_url", settings.eodhd_base_url)
                ),
                timeout_seconds=int(
                    config.pop("timeout_seconds", settings.eodhd_http_timeout_seconds)
                ),
                max_attempts=int(config.pop("max_attempts", settings.eodhd_max_attempts)),
                requests_per_minute=int(
                    config.pop("requests_per_minute", settings.eodhd_requests_per_minute)
                ),
                daily_call_budget=int(
                    config.pop("daily_call_budget", settings.eodhd_daily_call_budget)
                ),
            )
        )

    @property
    def configured(self) -> bool:
        return self.client.configured

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.name,
            capabilities=frozenset(
                {
                    CeriProviderCapability.HEALTH,
                    CeriProviderCapability.IDENTITY,
                    CeriProviderCapability.ESTIMATES,
                    CeriProviderCapability.EARNINGS,
                    CeriProviderCapability.CATALYSTS,
                }
            ),
            datasets=frozenset(
                {CeriDataset.ESTIMATES, CeriDataset.EARNINGS, CeriDataset.CATALYSTS}
            ),
        )

    def health(self) -> ProviderHealth:
        if not self.configured:
            return ProviderHealth(
                self.name,
                False,
                datetime.now(UTC),
                "credentials_missing",
                "Set EODHD_API_KEY to enable EODHD ingestion.",
            )
        stats = self.client.stats()
        healthy = stats.last_error is None or stats.successful_requests > 0
        return ProviderHealth(
            self.name,
            healthy,
            datetime.now(UTC),
            f"{stats.calls_used_today}/{self.client.config.daily_call_budget} calls today",
            stats.last_error or "EODHD configured",
        )

    def safe_metadata(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "credential_env_var": self.credential_env_var,
            "terms_version": self.terms_version,
            **self.client.safe_metadata(),
        }

    def resolve_company(self, query: CompanyQuery) -> list[ProviderCompany]:
        ticker = query.ticker or ""
        symbol = eodhd_symbol(ticker, query.exchange)
        if symbol is None and query.provider_company_id and "." in query.provider_company_id:
            symbol = query.provider_company_id.upper()
        if symbol is None:
            return []
        return [
            ProviderCompany(
                self.name, symbol, symbol.split(".")[0], symbol.split(".", 1)[1], query.cik
            )
        ]

    def fetch_estimate_snapshots(self, request: EstimateRequest) -> Iterable[RawProviderRecord]:
        symbol = self._symbol(request.ticker)
        rows = _rows(self.client.get_json("/api/calendar/trends", {"symbols": symbol}))
        for row in rows:
            ptype = period_type(row.get("period"))
            fiscal_end = provider_date(row.get("date"))
            if not ptype or fiscal_end is None:
                continue
            base = self._base_payload(symbol, row, fiscal_end, ptype)
            for metric, prefix in (
                ("EPS_DILUTED", "earningsEstimate"),
                ("REVENUE", "revenueEstimate"),
            ):
                consensus = row.get(f"{prefix}Avg")
                if (
                    consensus is None
                    and metric == "REVENUE"
                    and not any(str(k).lower().startswith("revenue") for k in row)
                ):
                    continue
                payload = {
                    **base,
                    "metric": metric,
                    "consensus": consensus,
                    "high": row.get(f"{prefix}High"),
                    "low": row.get(f"{prefix}Low"),
                    "analyst_count": row.get(f"{prefix}NumberOfAnalysts"),
                }
                payload["current_observation_reference"] = (
                    f"{symbol}:{ptype}:{fiscal_end}:{metric}"
                )
                if metric == "REVENUE":
                    payload["growth"] = (
                        row.get("revenueGrowth")
                        if row.get("revenueGrowth") is not None
                        else row.get("revenueEstimateGrowth")
                    )
                if metric == "EPS_DILUTED":
                    payload.update(
                        {
                            "eps_trend_current": row.get("epsTrendCurrent"),
                            "eps_trend_7d": row.get("epsTrend7daysAgo"),
                            "eps_trend_30d": row.get("epsTrend30daysAgo"),
                            "eps_trend_60d": row.get("epsTrend60daysAgo"),
                            "eps_trend_90d": row.get("epsTrend90daysAgo"),
                            "upward_count": row.get(
                                "epsRevisionsUpLast30days", row.get("epsRevisionsUpLast7days")
                            ),
                            "downward_count": row.get("epsRevisionsDownLast30days"),
                        }
                    )
                yield self._record(
                    CeriDataset.ESTIMATES,
                    f"{symbol}:{ptype}:{fiscal_end}:{metric}",
                    payload,
                    None,
                )
                if metric == "EPS_DILUTED":
                    # Earnings Trends includes historical consensus points in
                    # the same response.  Materialize those points as dated
                    # observations so the existing point-in-time baseline
                    # selector can calculate real 7/30/90-day features.
                    for days in (7, 30, 60, 90):
                        baseline = row.get(f"epsTrend{days}daysAgo")
                        if baseline is None:
                            continue
                        observed_at = _datetime(base.get("provider_observed_at"))
                        baseline_at = (
                            observed_at - timedelta(days=days) if observed_at is not None else None
                        )
                        baseline_payload = {
                            **payload,
                            "consensus": baseline,
                            # EODHD's trend points are observations relative to
                            # the provider response time, never the fiscal end.
                            "provider_observation_at": (
                                observed_at.isoformat() if observed_at is not None else None
                            ),
                            "observed_at": (
                                baseline_at.isoformat() if baseline_at is not None else None
                            ),
                            "effective_at": (
                                baseline_at.isoformat() if baseline_at is not None else None
                            ),
                            "trend_baseline_days": days,
                            "trend_baseline_window_days": days,
                            "baseline_origin": "PROVIDER_RELATIVE_WINDOW",
                            "current_observation_reference": (
                                f"{symbol}:{ptype}:{fiscal_end}:EPS_DILUTED"
                            ),
                        }
                        yield self._record(
                            CeriDataset.ESTIMATES,
                            f"{symbol}:{ptype}:{fiscal_end}:EPS_DILUTED:baseline:{days}",
                            baseline_payload,
                            None,
                        )

    def fetch_earnings_actuals(self, request: EarningsRequest) -> Iterable[RawProviderRecord]:
        symbol = self._symbol(request.ticker)
        start = request.start or date.today() - timedelta(days=365)
        end = request.end or date.today() + timedelta(days=120)
        rows = _rows(
            self.client.get_json(
                "/api/calendar/earnings",
                {"symbols": symbol, "from": start.isoformat(), "to": end.isoformat()},
            )
        )
        for row in rows:
            report_date = provider_date(row.get("reportDate") or row.get("date"))
            if report_date is None:
                continue
            ptype = period_type(row.get("period")) or "CURRENT_QUARTER"
            payload = {
                "ticker": symbol.split(".")[0],
                "provider_company_id": symbol,
                "metric": "EPS_DILUTED",
                "period_type": ptype,
                "fiscal_period_end": provider_date(row.get("fiscalPeriodEnd") or row.get("date"))
                or report_date,
                "report_at": _report_at(row, report_date),
                "source_date": report_date.isoformat(),
                "actual_value": row.get("epsActual"),
                "estimate": row.get("epsEstimate"),
                "surprise_percent": row.get("surprisePercent"),
                "report_time": row.get("beforeAfterMarket"),
            }
            yield self._record(
                CeriDataset.EARNINGS,
                str(row.get("id") or f"{symbol}:{report_date}"),
                payload,
                row.get("reportDate"),
            )

    def fetch_guidance(self, request: GuidanceRequest) -> Iterable[RawProviderRecord]:
        return iter(())

    def fetch_catalysts(self, request: CatalystRequest) -> Iterable[RawProviderRecord]:
        symbol = self._symbol(request.ticker)
        start = request.start or date.today() - timedelta(days=2)
        end = request.end or date.today()
        rows = _rows(
            self.client.get_json(
                "/api/news",
                {"s": symbol, "from": start.isoformat(), "to": end.isoformat()},
                call_cost=5,
            )
        )
        for row in rows:
            published = row.get("date") or row.get("publishedAt")
            title = str(row.get("title") or row.get("headline") or "").strip()
            text = f"{title} {row.get('content') or row.get('text') or ''}"
            category, subtype, confidence = _classify_news(text)
            payload = {
                "ticker": symbol.split(".")[0],
                "provider_company_id": symbol,
                "provider_terms_version": self.terms_version,
                "category": category,
                "subtype": subtype,
                "title": title,
                "subject": title or subtype,
                "canonical_text": title,
                "confidence": confidence,
                "announced_at": published,
                "source_date": str(published)[:10] if published else None,
                "source_reference": str(row.get("source") or row.get("site") or "EODHD"),
                "source_url": row.get("link") or row.get("url"),
                "status": "ANNOUNCED",
                "direction": "UNKNOWN",
                "materiality": 0.0,
                "tags": row.get("tags") or row.get("categories"),
                "related_tickers": row.get("relatedTickers") or row.get("symbols"),
                "sentiment": row.get("sentiment") or row.get("sentimentScore"),
            }
            provider_id = str(
                row.get("id")
                or row.get("news_id")
                or hashlib.sha256(
                    f"{payload['source_url']}|{published}|{title}".encode()
                ).hexdigest()
            )
            yield self._record(CeriDataset.CATALYSTS, provider_id, payload, published)

    def _symbol(self, ticker: str) -> str:
        symbol = eodhd_symbol(ticker, "US")
        if symbol is None:
            raise ValueError(f"ambiguous EODHD identity for ticker {ticker}")
        return symbol

    def _base_payload(
        self, symbol: str, row: dict[str, Any], fiscal_end: date, ptype: str
    ) -> dict[str, Any]:
        provider_observed_at = (
            _datetime(row.get("observedAt"))
            or _datetime(row.get("updatedAt"))
            or _datetime(row.get("lastUpdated"))
        )
        payload = {
            "ticker": symbol.split(".")[0],
            "provider_company_id": symbol,
            "provider_terms_version": self.terms_version,
            "period_type": ptype,
            "fiscal_period_end": fiscal_end.isoformat(),
            "currency": row.get("currency") or row.get("currencyCode"),
            "source_currency": row.get("currency") or row.get("currencyCode"),
            "provider_observed_at": (
                provider_observed_at.isoformat() if provider_observed_at is not None else None
            ),
            "observed_at": (
                provider_observed_at.isoformat() if provider_observed_at is not None else None
            ),
        }
        return payload

    def _record(
        self, dataset: CeriDataset, provider_id: str, payload: dict[str, Any], observed: Any
    ) -> RawProviderRecord:
        retrieved_at = self._clock()
        observed_at = (
            _datetime(payload.get("observed_at"))
            or _datetime(payload.get("provider_observation_at"))
        )
        published_at = (
            _datetime(payload.get("published_at"))
            or _datetime(payload.get("announced_at"))
            or _datetime(payload.get("source_timestamp"))
            or _datetime(observed)
        )
        return RawProviderRecord(
            provider=self.name,
            dataset=dataset,
            provider_record_id=provider_id,
            payload={**payload, "provider_terms_version": self.terms_version},
            published_at=published_at,
            observed_at=observed_at,
            source_url=payload.get("source_url"),
            export_policy=ExportPolicy.RESTRICTED.value,
            retrieved_at=retrieved_at,
            source_timestamp=_datetime(payload.get("source_timestamp"))
            or _datetime(payload.get("provider_observed_at")),
        )


def _rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [dict(row) for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        for key in ("data", "results", "earnings", "news", "trends"):
            if isinstance(value.get(key), list):
                return [dict(row) for row in value[key] if isinstance(row, dict)]
        return [value]
    return []


def _datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _report_at(row: dict[str, Any], report_date: date) -> datetime:
    value = _datetime(row.get("reportDateTime") or row.get("report_at"))
    if value is not None:
        return value
    return datetime(report_date.year, report_date.month, report_date.day, 21, 0, tzinfo=UTC)


def _classify_news(text: str) -> tuple[str, str, str]:
    lowered = text.lower()
    rules = (
        ("REGULATORY", ("approval", "fda", "regulator", "investigation", "review"), "NORMAL"),
        ("LEGAL", ("lawsuit", "ruling", "settlement", "injunction"), "NORMAL"),
        ("FINANCING", ("offering", "debt", "refinanc", "covenant"), "NORMAL"),
        ("CAPITAL_ALLOCATION", ("buyback", "dividend", "repurchase"), "NORMAL"),
        ("CONTRACT", ("contract", "award", "partnership", "renewal"), "LOW"),
        ("PRODUCT", ("launch", "product", "milestone", "recall"), "LOW"),
        ("ANALYST_ACTION", ("upgrade", "downgrade", "price target", "analyst"), "LOW"),
        ("EARNINGS", ("earnings", "quarterly results", "eps", "revenue"), "NORMAL"),
        ("GUIDANCE", ("guidance", "outlook", "forecast"), "LOW"),
    )
    for category, words, confidence in rules:
        for word in words:
            if word in lowered:
                return category, re.sub(r"[^a-z0-9]+", "_", word), confidence
    return "ANALYST_ACTION", "news", "LOW"
