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
        self.terms_version = str(config.pop("terms_version", settings.eodhd_terms_version))
        self._clock = clock or (lambda: datetime.now(UTC))
        self.client = client or EodhdHttpClient(
            EodhdClientConfig(
                api_key=key,
                base_url=str(config.pop("base_url", settings.eodhd_base_url)),
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
        observation_at = self._clock()
        fiscal_start = request.start or observation_at.date() - timedelta(days=120)
        fiscal_end_limit = request.end or observation_at.date() + timedelta(days=550)
        requested_periods = {value.value for value in request.period_types}
        requested_metrics = {value.value for value in request.metrics}
        for row in rows:
            ptype = period_type(row.get("period"))
            fiscal_end = provider_date(row.get("date"))
            if (
                not ptype
                or ptype not in requested_periods
                or fiscal_end is None
                or fiscal_end < fiscal_start
                or fiscal_end > fiscal_end_limit
            ):
                continue
            base = self._base_payload(
                symbol,
                row,
                fiscal_end,
                ptype,
                fallback_observed_at=observation_at,
            )
            for metric, prefix in (
                ("EPS_DILUTED", "earningsEstimate"),
                ("REVENUE", "revenueEstimate"),
            ):
                if metric not in requested_metrics:
                    continue
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
                payload["current_observation_reference"] = f"{symbol}:{ptype}:{fiscal_end}:{metric}"
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
                                observed_at.isoformat() if observed_at is not None else None
                            ),
                            "reference_at": (
                                baseline_at.isoformat() if baseline_at is not None else None
                            ),
                            "effective_at": (
                                baseline_at.isoformat() if baseline_at is not None else None
                            ),
                            "trend_baseline_days": days,
                            "trend_baseline_window_days": days,
                            "baseline_origin": "PROVIDER_RETROSPECTIVE_WINDOW",
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
        today = self._clock().date()
        historical_start = request.start or today - timedelta(days=548)
        historical_end = min(request.end, today) if request.end is not None else today
        upcoming_start = max(request.start, today) if request.start is not None else today
        upcoming_end = request.end or today + timedelta(days=120)
        policies = (
            ("REPORTED", historical_start, historical_end),
            ("UPCOMING", upcoming_start, upcoming_end),
        )
        seen: set[str] = set()
        for policy_kind, start, end in policies:
            if end < start:
                continue
            rows = _rows(
                self.client.get_json(
                    "/api/calendar/earnings",
                    {"symbols": symbol, "from": start.isoformat(), "to": end.isoformat()},
                )
            )
            for row in rows:
                yield from self._earnings_record(
                    symbol=symbol,
                    row=row,
                    today=today,
                    policy_kind=policy_kind,
                    seen=seen,
                )

    def _earnings_record(
        self,
        *,
        symbol: str,
        row: dict[str, Any],
        today: date,
        policy_kind: str,
        seen: set[str],
    ) -> Iterable[RawProviderRecord]:
        report_date = provider_date(_first_present(row, "report_date", "reportDate", "date"))
        if report_date is None:
            return
        provider_id = str(row.get("id") or f"{symbol}:{report_date}")
        if provider_id in seen:
            return
        event_kind = "UPCOMING" if report_date > today else "REPORTED"
        if event_kind != policy_kind:
            return
        seen.add(provider_id)
        ptype = period_type(row.get("period")) or "CURRENT_QUARTER"
        actual = _first_present(row, "actual", "epsActual")
        estimate = _first_present(row, "estimate", "epsEstimate")
        provider_surprise = _first_present(row, "percent", "surprisePercent")
        consensus_semantics = (
            "REPORT_TIME_CONSENSUS"
            if event_kind == "REPORTED"
            and actual is not None
            and estimate is not None
            and provider_surprise is not None
            else None
        )
        payload = {
            "ticker": symbol.split(".")[0],
            "provider_company_id": symbol,
            "metric": "EPS_DILUTED",
            "period_type": ptype,
            "fiscal_period_end": provider_date(_first_present(row, "date", "fiscalPeriodEnd"))
            or report_date,
            "report_at": _report_at(row, report_date),
            "source_date": report_date.isoformat(),
            "actual_value": actual,
            "estimate": estimate,
            "surprise_percent": provider_surprise,
            "report_time": _first_present(row, "before_after_market", "beforeAfterMarket"),
            "source_currency": row.get("currency"),
            "event_kind": event_kind,
            "acquisition_policy": policy_kind,
            "provider_consensus_semantics": consensus_semantics,
        }
        yield self._record(
            CeriDataset.EARNINGS,
            provider_id,
            payload,
            _first_present(row, "report_date", "reportDate"),
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
            expected_date = provider_date(
                row.get("expectedDate") or row.get("eventDate") or row.get("scheduledDate")
            )
            lifecycle_status = _news_status(text, expected_date=expected_date)
            issuer_relevance, relevance_reason = _issuer_relevance(
                symbol.split(".")[0],
                row.get("relatedTickers") or row.get("symbols"),
                title,
            )
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
                "status": lifecycle_status,
                "direction": "UNKNOWN",
                "materiality": row.get("materiality"),
                "expected_date": expected_date.isoformat() if expected_date else None,
                "tags": row.get("tags") or row.get("categories"),
                "related_tickers": row.get("relatedTickers") or row.get("symbols"),
                "issuer_relevance": issuer_relevance,
                "issuer_relevance_reason": relevance_reason,
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
        self,
        symbol: str,
        row: dict[str, Any],
        fiscal_end: date,
        ptype: str,
        *,
        fallback_observed_at: datetime,
    ) -> dict[str, Any]:
        supplied_provider_observed_at = (
            _datetime(row.get("observedAt"))
            or _datetime(row.get("updatedAt"))
            or _datetime(row.get("lastUpdated"))
        )
        provider_observed_at = supplied_provider_observed_at or fallback_observed_at
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
            "provider_observation_time_basis": (
                "PROVIDER_SUPPLIED"
                if supplied_provider_observed_at is not None
                else "RETRIEVAL_FALLBACK"
            ),
        }
        return payload

    def _record(
        self, dataset: CeriDataset, provider_id: str, payload: dict[str, Any], observed: Any
    ) -> RawProviderRecord:
        retrieved_at = self._clock()
        observed_at = _datetime(payload.get("observed_at")) or _datetime(
            payload.get("provider_observation_at")
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
        return _flatten_rows(value)
    if isinstance(value, dict):
        for key in ("data", "results", "earnings", "news", "trends"):
            if isinstance(value.get(key), list):
                return _flatten_rows(value[key])
        return [value]
    return []


def _first_present(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
    return None


def _flatten_rows(value: list[Any]) -> list[dict[str, Any]]:
    """Flatten provider containers such as ``trends: [[...rows...]]``."""
    rows: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            rows.append(dict(item))
        elif isinstance(item, list):
            rows.extend(_flatten_rows(item))
    return rows


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
        ("GUIDANCE", ("guidance", "outlook", "forecast"), "NORMAL"),
        ("EARNINGS", ("earnings", "quarterly results", "earnings call", "eps"), "NORMAL"),
        ("LEGAL", ("lawsuit", "ruling", "settlement", "injunction"), "NORMAL"),
        (
            "REGULATORY",
            ("fda decision", "regulatory decision", "regulator", "investigation"),
            "NORMAL",
        ),
        ("FINANCING", ("offering", "debt", "refinanc", "covenant"), "NORMAL"),
        ("CAPITAL_ALLOCATION", ("buyback", "dividend", "repurchase"), "NORMAL"),
        ("CONTRACT", ("contract", "award", "partnership", "renewal"), "LOW"),
        ("PRODUCT", ("fda approval", "launch", "product", "milestone", "recall"), "LOW"),
        ("ANALYST_ACTION", ("upgrade", "downgrade", "price target", "analyst"), "LOW"),
    )
    for category, words, confidence in rules:
        for word in words:
            if word in lowered:
                return category, re.sub(r"[^a-z0-9]+", "_", word), confidence
    return "ANALYST_ACTION", "news", "LOW"


def _issuer_relevance(
    requested_ticker: str,
    related_tickers: Any,
    title: str,
) -> tuple[bool | None, str]:
    requested = requested_ticker.upper()
    related: set[str] = set()
    if isinstance(related_tickers, str):
        related = {
            token.split(".")[0].upper() for token in re.split(r"[,;\s]+", related_tickers) if token
        }
    elif isinstance(related_tickers, (list, tuple, set)):
        related = {
            str(token).split(".")[0].upper() for token in related_tickers if token not in (None, "")
        }
    if related:
        if requested in related:
            return True, "PROVIDER_RELATED_TICKER_MATCH"
        return False, "ISSUER_RELEVANCE_MISMATCH"
    headline_tickers = {match.upper() for match in re.findall(r"\(([A-Z][A-Z0-9.-]{0,9})\)", title)}
    if requested in headline_tickers:
        return True, "HEADLINE_TICKER_MATCH"
    if headline_tickers and requested not in headline_tickers:
        return False, "ISSUER_RELEVANCE_MISMATCH"
    return None, "ISSUER_RELEVANCE_UNVERIFIED"


def _news_status(text: str, *, expected_date: date | None) -> str:
    lowered = text.lower()
    completed_terms = (
        "completed",
        "reported results",
        "reports results",
        "outcome announced",
        "approval granted",
        "ruling issued",
        "settlement reached",
    )
    if any(term in lowered for term in completed_terms):
        return "COMPLETED"
    scheduled_terms = (
        "scheduled",
        "decision expected",
        "hearing on",
        "vote on",
    )
    if expected_date is not None and any(term in lowered for term in scheduled_terms):
        return "SCHEDULED"
    return "ANNOUNCED"
