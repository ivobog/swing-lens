"""Synthetic, contiguous US sessions and explicit PIT evidence; never provider data."""

from copy import deepcopy
from datetime import UTC, datetime
from functools import lru_cache

import numpy as np
import pandas as pd

from app.services.market_clock_service import MarketClockService
from app.services.market_sector_intelligence_config import load_market_sector_intelligence_config
from app.services.market_sector_intelligence_rotation_service import fingerprint
from app.services.us_market_calendar import subtract_us_trading_sessions

NOW = datetime(2026, 10, 21, 20, 30, tzinfo=UTC)
CUTOFF = MarketClockService().cutoff_for(NOW, reason="D3_SYNTHETIC_TEST")


def symbols():
    return [row.etf_symbol for row in load_market_sector_intelligence_config().sectors]


@lru_cache
def sessions(count=320):
    return tuple(
        subtract_us_trading_sessions(CUTOFF.latest_completed_session, i)
        for i in reversed(range(count))
    )


def frame(ticker, count=320, basis="TRADES"):
    index = (symbols() + ["SPY", "QQQ"]).index(ticker)
    t = np.arange(count)
    close = 90 + (0.035 + index * 0.007) * t + 2 * np.sin(t / (7 + index / 3))
    result = pd.DataFrame(
        {
            "date": sessions(count),
            "open": close - 0.15,
            "high": close + 1.1,
            "low": close - 1.2,
            "close": close,
            "volume": 2_000_000 + 50_000 * np.cos(t / 4),
        }
    )
    result.attrs.update(price_basis=basis, volume_basis=basis)
    return result


def receipt(source, ticker, kwargs):
    source.attrs["pit_source_manifest"] = {
        "ticker": ticker,
        "what_to_show": source.attrs.get("price_basis"),
        "timeframe": "1 day",
        "as_of": kwargs["as_of"].isoformat(),
        "max_session": kwargs["max_session"].isoformat(),
        "acquisition_authority": None,
        "states": [
            {
                "id": i,
                "fingerprint": fingerprint(
                    {
                        "ticker": ticker,
                        "date": str(row["date"]),
                        **{k: str(row[k]) for k in ("open", "high", "low", "close", "volume")},
                    }
                ),
            }
            for i, (_, row) in enumerate(source.iterrows())
        ],
    }
    return source


def loader(overrides=None, calls=None):
    frames = {symbol: frame(symbol) for symbol in symbols() + ["SPY", "QQQ"]}
    frames.update(overrides or {})

    def load(_db, ticker, **kwargs):
        if calls is not None:
            calls.append((ticker, kwargs))
        value = frames[ticker]
        if isinstance(value, Exception):
            raise value
        selected = deepcopy(value)
        if not selected.empty and set(("date", "open", "high", "low", "close", "volume")).issubset(
            selected
        ):
            receipt(selected, ticker, kwargs)
        return selected, None

    return load


@lru_cache
def native_inputs():
    from app.services import market_sector_intelligence_technical_adapter as adapter

    policy = adapter.scoped_policy(symbols())
    market = {
        symbol: adapter.local_features(
            frame(symbol),
            None,
            symbol,
            CUTOFF.latest_completed_session,
            policy,
        ).latest
        for symbol in ("SPY", "QQQ")
    }
    base = adapter.build_base(
        "XLB",
        frame("XLB"),
        None,
        frame("SPY"),
        market["SPY"],
        market["QQQ"],
        CUTOFF.latest_completed_session,
        policy,
    )
    return policy, base, market
