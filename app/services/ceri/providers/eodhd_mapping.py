from __future__ import annotations

from datetime import date
from typing import Any

PERIOD_MAP = {
    "0q": "CURRENT_QUARTER",
    "+1q": "NEXT_QUARTER",
    "0y": "CURRENT_FISCAL_YEAR",
    "+1y": "NEXT_FISCAL_YEAR",
    "0qtr": "CURRENT_QUARTER",
    "1q": "NEXT_QUARTER",
    "0fy": "CURRENT_FISCAL_YEAR",
    "1fy": "NEXT_FISCAL_YEAR",
}


def eodhd_symbol(ticker: str, exchange: str | None = None) -> str | None:
    value = ticker.strip().upper()
    suffix = {"US": "US", "NASDAQ": "US", "NYSE": "US", "AMEX": "US", "NYSEARCA": "US"}.get(
        (exchange or "").strip().upper()
    )
    if "." in value:
        if suffix and not value.endswith(f".{suffix}"):
            # US share classes use a dot in canonical ticker notation (MOG.A),
            # while EODHD expects a hyphen plus its exchange suffix (MOG-A.US).
            return f"{value.replace('.', '-')}.{suffix}"
        return value
    return f"{value}.{suffix}" if suffix else None


def canonical_ticker_from_eodhd_symbol(symbol: str) -> str:
    provider_ticker = symbol.strip().upper().rsplit(".", 1)[0]
    return provider_ticker.replace("-", ".")


def period_type(value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value).strip().lower()
    return PERIOD_MAP.get(raw, PERIOD_MAP.get(raw.replace(" ", "")))


def provider_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
