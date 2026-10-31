"""Bounded, run-free cached price charts; no capture or calculation authority."""

import math
from contextlib import nullcontext
from dataclasses import asdict
from datetime import UTC, datetime

import pandas as pd
from sqlalchemy.orm import Session

from app.services.market_clock_service import MarketClockService, clock_for_cutoff
from app.services.market_sector_intelligence_config import load_market_sector_intelligence_config
from app.services.market_sector_intelligence_coverage import _session
from app.services.market_sector_intelligence_read_window import bounded_cached_read as _bounded_read
from app.services.market_sector_intelligence_rotation_service import fingerprint
from app.services.price_bar_repository import load_preferred_ohlcv_frames
from app.services.us_market_calendar import (
    CALENDAR_VERSION,
    is_us_trading_day,
    subtract_us_trading_sessions,
)

WINDOWS = (5, 21, 63, 126)
METHOD = "cached-price-charts-v1"


class ChartEvidenceError(ValueError):
    """Stable public reason code; never an underlying database/provider message."""


def selected_symbols(value, config):
    allowed = [sector.etf_symbol for sector in config.sectors]
    if value is None:
        return allowed
    symbols = [symbol.strip().upper() for symbol in value.split(",")]
    if (
        not 1 <= len(symbols) <= 11
        or len(set(symbols)) != len(symbols)
        or any(symbol not in allowed for symbol in symbols)
    ):
        raise ValueError("INVALID_SECTOR_SELECTION")
    return [symbol for symbol in allowed if symbol in symbols]


def chart_sessions(target, *, calendar_version=CALENDAR_VERSION):
    if not is_us_trading_day(target, calendar_version=calendar_version):
        raise ValueError("TARGET_MUST_BE_US_SESSION")
    return [
        subtract_us_trading_sessions(target, n, calendar_version=calendar_version)
        for n in reversed(range(127))
    ]


def _positive(value):
    try:
        return not pd.api.types.is_bool(value) and math.isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError, OverflowError):
        return False


def price_window(prices, sessions):
    """Exact, continuous exchange-session span, never a row-count approximation."""
    if any(day not in prices for day in sessions):
        return None, "MISSING_SESSION"
    values = [prices[day] for day in sessions]
    if any(not _positive(value) for value in values):
        return None, "INVALID_OHLC"
    try:
        absolute = 100 * (values[-1] / values[0] - 1)
        points = [
            {"session": day.isoformat(), "index_100": 100 * value / values[0]}
            for day, value in zip(sessions, values, strict=True)
        ]
        if not math.isfinite(absolute) or any(not _positive(p["index_100"]) for p in points):
            raise ValueError
    except (OverflowError, ZeroDivisionError, ValueError):
        return None, "NONFINITE_METRICS"
    return {"absolute_pct": absolute, "points": points}, None


def relative_return(etf, spy, sessions):
    value = 100 * (
        (etf[sessions[-1]] / spy[sessions[-1]]) / (etf[sessions[0]] / spy[sessions[0]]) - 1
    )
    if not math.isfinite(value):
        raise ChartEvidenceError("NONFINITE_METRICS")
    return value


def _source(frame, symbol, cutoff, sessions):
    if frame.empty:
        raise ChartEvidenceError("MISSING_DATA")
    basis = frame.attrs.get("price_basis")
    if basis not in {"TRADES", "ADJUSTED_LAST"}:
        raise ChartEvidenceError("UNKNOWN_PRICE_BASIS")
    if not {"date", "open", "high", "low", "close"}.issubset(frame.columns):
        raise ChartEvidenceError("INVALID_COLUMNS")
    manifest = frame.attrs.get("pit_source_manifest")
    if not isinstance(manifest, dict) or len(manifest.get("states", ())) != len(frame):
        raise ChartEvidenceError("PIT_UNAVAILABLE")
    if (
        manifest.get("ticker") != symbol
        or manifest.get("what_to_show") != basis
        or manifest.get("timeframe") != "1 day"
        or pd.Timestamp(manifest.get("as_of")) != cutoff.cutoff_at
        or manifest.get("max_session") != cutoff.latest_completed_session.isoformat()
        or manifest.get("acquisition_authority") is not None
    ):
        raise ChartEvidenceError("PIT_BOUNDARY_MISMATCH")
    dates = [_session(value) for value in frame["date"]]
    if len(set(dates)) != len(dates) or any(
        not is_us_trading_day(day, calendar_version=cutoff.calendar_version) for day in dates
    ):
        raise ChartEvidenceError("INVALID_OR_DUPLICATE_SESSION")
    if any(day > cutoff.latest_completed_session for day in dates):
        raise ChartEvidenceError("FUTURE_SESSION")
    prices, material = {}, []
    for index, day in enumerate(dates):
        if day < sessions[0]:
            continue
        row = frame.iloc[index]
        for column in ("created_at", "first_seen_at"):
            if column in frame:
                stamp = pd.Timestamp(row[column])
                if pd.isna(stamp) or stamp.tzinfo is None or stamp > cutoff.cutoff_at:
                    raise ChartEvidenceError("POST_CUTOFF_OBSERVATION")
        digest = manifest["states"][index].get("fingerprint")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise ChartEvidenceError("PIT_UNAVAILABLE")
        valid = all(_positive(row[key]) for key in ("open", "high", "low", "close"))
        if valid:
            opening, high, low, close = (
                float(row[key]) for key in ("open", "high", "low", "close")
            )
            valid = low <= min(opening, close) <= max(opening, close) <= high
        prices[day] = float(row["close"]) if valid else None
        material.append(
            {"session": day.isoformat(), "close": prices[day], "source_fingerprint": digest}
        )
    return {
        "symbol": symbol,
        "price_basis": basis,
        "prices": prices,
        "bar_count": len(prices),
        "first_session": min(prices).isoformat() if prices else None,
        "last_session": max(prices).isoformat() if prices else None,
        "source_digest": fingerprint(
            {"symbol": symbol, "basis": basis, "bars": sorted(material, key=lambda p: p["session"])}
        ),
        "reasons": [],
    }


class MarketSectorChartService:
    def __init__(self, *, clock=None, frame_loader=None):
        self.clock = clock or MarketClockService()
        self.frame_loader = frame_loader or load_preferred_ohlcv_frames

    def build(self, db, *, symbols=None, horizon=126, config=None, cutoff=None):
        config = config or load_market_sector_intelligence_config()
        if type(horizon) is not int or horizon not in WINDOWS:
            raise ValueError("INVALID_CHART_HORIZON")
        chosen = selected_symbols(symbols, config)
        cutoff = cutoff or self.clock.cutoff_for(datetime.now(UTC), reason="MARKET_SECTOR_CHART_D4")
        clock_for_cutoff(cutoff)
        sessions = chart_sessions(
            cutoff.latest_completed_session, calendar_version=cutoff.calendar_version
        )
        sources = {}
        guard = _bounded_read(db, sessions[0]) if isinstance(db, Session) else nullcontext()
        with guard:
            for symbol in chosen + ["SPY"]:
                data = dict(
                    symbol=symbol,
                    price_basis=None,
                    prices={},
                    bar_count=0,
                    first_session=None,
                    last_session=None,
                    source_digest=None,
                    reasons=[],
                )
                if not config.enabled:
                    data["reasons"] = ["FEATURE_DISABLED"]
                else:
                    try:
                        price, _volume = self.frame_loader(
                            db,
                            symbol,
                            timeframe=config.timeframe,
                            max_session=cutoff.latest_completed_session,
                            as_of=cutoff.cutoff_at,
                        )
                        data = _source(price, symbol, cutoff, sessions)
                    except Exception as exc:
                        data["reasons"] = [
                            str(exc)
                            if isinstance(exc, ChartEvidenceError)
                            else "CHART_EVIDENCE_UNAVAILABLE"
                        ]
                sources[symbol] = data
        benchmark = sources["SPY"]
        rows = []
        definitions = {sector.etf_symbol: sector for sector in config.sectors}
        for symbol in chosen + ["SPY"]:
            data = sources[symbol]
            same_basis = (
                data["price_basis"] is not None and data["price_basis"] == benchmark["price_basis"]
            )
            metrics = {}
            points = []
            for window in WINDOWS:
                span = sessions[-window - 1 :]
                own, own_reason = price_window(data["prices"], span)
                spy, spy_reason = price_window(benchmark["prices"], span)
                reasons = list(data["reasons"])
                if own_reason:
                    reasons.append(own_reason)
                if spy_reason or benchmark["reasons"]:
                    reasons.append("BENCHMARK_UNAVAILABLE")
                if not same_basis and own and spy:
                    reasons.append("INCOMPATIBLE_PRICE_BASIS")
                relative = None
                if own and spy and same_basis:
                    try:
                        relative = relative_return(data["prices"], benchmark["prices"], span)
                    except (ValueError, OverflowError, ZeroDivisionError):
                        reasons.append("NONFINITE_METRICS")
                metrics[str(window)] = {
                    "absolute_pct": own["absolute_pct"] if own else None,
                    "spy_relative_pct": relative,
                    "start_session": span[0].isoformat(),
                    "end_session": span[-1].isoformat(),
                    "reasons": list(dict.fromkeys(reasons)),
                }
                if window == horizon and own and spy and same_basis and relative is not None:
                    points = own["points"]
            reasons = metrics[str(horizon)]["reasons"]
            status = (
                "DISABLED"
                if not config.enabled
                else "CURRENT"
                if points
                else "MISSING"
                if data["reasons"] == ["MISSING_DATA"]
                else "STALE"
                if data["last_session"] and data["last_session"] != sessions[-1].isoformat()
                else "INCOMPATIBLE_PRICE_BASIS"
                if "INCOMPATIBLE_PRICE_BASIS" in reasons
                else "INVALID_EVIDENCE"
                if data["reasons"] or "INVALID_OHLC" in reasons or "NONFINITE_METRICS" in reasons
                else "INSUFFICIENT_HISTORY"
            )
            rows.append(
                {
                    **({} if symbol == "SPY" else asdict(definitions[symbol])),
                    **{key: value for key, value in data.items() if key != "prices"},
                    "status": status,
                    "returns": metrics,
                    "points": points,
                    "reasons": reasons,
                }
            )
        result = {
            "source_type": "CHART_PRICE_HISTORY",
            "method_version": METHOD,
            "relative_method": "ratio_of_ratios_vs_spy_v1",
            "price_return_semantics": "cached_price_return_not_certified_total_return",
            "history_semantics": (
                "Cached history available as of the current read cutoff; not archived signals."
            ),
            "feature_enabled": config.enabled,
            "benchmark_symbol": "SPY",
            "market_session": cutoff.latest_completed_session.isoformat(),
            "as_of_utc": cutoff.cutoff_at.isoformat().replace("+00:00", "Z"),
            "calendar_version": cutoff.calendar_version,
            "bar_readiness_version": cutoff.bar_readiness_version,
            "configuration_digest": config.configuration_digest,
            "requested_horizon_sessions": horizon,
            "start_session": sessions[-horizon - 1].isoformat(),
            "calendar_sessions": [day.isoformat() for day in sessions],
            "cohort_status": "DISABLED"
            if not config.enabled
            else "COMPLETE"
            if all(row["points"] for row in rows)
            else "PARTIAL"
            if any(row["points"] for row in rows[:-1])
            else "UNAVAILABLE",
            "source_digests": {symbol: data["source_digest"] for symbol, data in sources.items()},
            "benchmark": rows[-1],
            "rows": rows[:-1],
        }
        result["evidence_digest"] = fingerprint(
            {
                "method": METHOD,
                "session": result["market_session"],
                "horizon": horizon,
                "configuration_digest": config.configuration_digest,
                "calendar_version": cutoff.calendar_version,
                "sources": {
                    symbol: {"digest": data["source_digest"], "reasons": data["reasons"]}
                    for symbol, data in sorted(sources.items())
                },
            }
        )
        fingerprint(result)  # Assert finite JSON at the final boundary.
        return result
