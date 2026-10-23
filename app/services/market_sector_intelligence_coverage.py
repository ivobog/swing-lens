"""Independent cache freshness only: no acquisition, scores or persisted context."""

import logging
import math
from contextlib import nullcontext
from datetime import UTC, date, datetime, time
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.services.market_clock_service import (
    MarketCalculationCutoff,
    MarketClockService,
    clock_for_cutoff,
)
from app.services.market_sector_intelligence_config import (
    MarketSectorIntelligenceConfig,
    load_market_sector_intelligence_config,
)
from app.services.price_bar_repository import load_preferred_ohlcv_frames, load_price_bars_frame
from app.services.us_market_calendar import is_us_trading_day

logger = logging.getLogger(__name__)


class CoverageEvidenceError(ValueError):
    """A safe reason code, without connection strings or provider diagnostics."""


class MarketSectorIntelligenceCoverageService:
    def __init__(self, *, clock=None, frame_loader=None, basis_loader=None):
        self.clock = clock or MarketClockService()
        self.frame_loader = frame_loader or load_preferred_ohlcv_frames
        self.basis_loader = basis_loader or load_price_bars_frame

    def build(
        self,
        db: Session,
        *,
        config: MarketSectorIntelligenceConfig | None = None,
        cutoff: MarketCalculationCutoff | None = None,
    ) -> dict[str, Any]:
        config = config or load_market_sector_intelligence_config()
        cutoff = cutoff or self.clock.cutoff_for(
            datetime.now(UTC), reason="MARKET_SECTOR_INTELLIGENCE_COVERAGE"
        )
        clock_for_cutoff(cutoff)
        rows = [
            self._row(
                "sector",
                sector.etf_symbol,
                sector.run_canonical_sector,
                sector.standard_market_sector,
            )
            for sector in config.sectors
        ]
        rows.append(self._row("benchmark", config.benchmark_symbol, None, None))
        # Prevent a caller's pending ORM changes from being autoflushed by reads.
        with db.no_autoflush if isinstance(db, Session) else nullcontext():
            for row in rows:
                if not config.enabled:
                    row.update(status="DISABLED", reason="feature_disabled")
                    continue
                try:
                    price, _volume = self.frame_loader(
                        db,
                        row["ticker"],
                        timeframe=config.timeframe,
                        max_session=cutoff.latest_completed_session,
                        as_of=cutoff.cutoff_at,
                    )
                    self._evaluate(row, price, cutoff)
                except CoverageEvidenceError as exc:
                    row.update(status="UNAVAILABLE", reason=str(exc))
                except Exception as exc:
                    # Do not expose exception messages (which may include credentials).
                    logger.warning(
                        "ETF coverage failed for %s (%s)", row["ticker"], type(exc).__name__
                    )
                    row.update(status="UNAVAILABLE", reason="cached_price_evaluation_failed")
            freshness = self._freshness(db, config, cutoff)
        return {
            "feature": "market_sector_intelligence",
            "enabled": config.enabled,
            "configuration_version": config.version,
            "configuration_digest": config.configuration_digest,
            "benchmark": config.benchmark_symbol,
            "timeframe": config.timeframe,
            "as_of": cutoff.cutoff_at.isoformat().replace("+00:00", "Z"),
            "latest_completed_session": cutoff.latest_completed_session.isoformat(),
            "exchange_timezone": cutoff.exchange_timezone,
            "symbols": rows,
            "freshness": freshness,
        }

    def _freshness(self, db, config, cutoff):
        """Verify each required basis independently, without changing model preference.

        The canonical reader applies receipt/revision PIT visibility. Invalid
        closes do not qualify as usable evidence; malformed session/receipt
        evidence makes that basis UNKNOWN rather than certifying freshness.
        """
        entries = []
        for sector in config.sectors:
            for basis in ("TRADES", "ADJUSTED_LAST"):
                row = self._row("sector", sector.etf_symbol, None, None)
                row["price_basis"] = basis
                if not config.enabled:
                    row.update(status="UNKNOWN", reason="feature_disabled")
                else:
                    try:
                        frame = self.basis_loader(
                            db, sector.etf_symbol, basis, timeframe=config.timeframe,
                            max_session=cutoff.latest_completed_session, as_of=cutoff.cutoff_at,
                        )
                        # Validate the full frame before considering usable closes.
                        try:
                            self._evaluate(dict(row), frame, cutoff)
                        except CoverageEvidenceError as exc:
                            if str(exc) != "invalid_latest_close":
                                raise
                        if not frame.empty:
                            def usable(value):
                                try:
                                    return (not pd.api.types.is_bool(value)
                                            and math.isfinite(float(value)) and float(value) > 0)
                                except (TypeError, ValueError, OverflowError):
                                    return False
                            frame = frame.loc[frame["close"].map(usable)].copy()
                            frame.attrs["price_basis"] = basis
                        self._evaluate(row, frame, cutoff)
                    except Exception:
                        row.update(status="UNKNOWN", reason="cached_basis_evaluation_failed",
                                   first_session=None, last_session=None, session_lag=None)
                entries.append(row)
        stale = [row for row in entries if row["status"] == "STALE"]
        missing = [row for row in entries if row["status"] == "MISSING"]
        unknown = [row for row in entries if row["status"] == "UNKNOWN"]
        status = "STALE" if stale else "UNKNOWN" if unknown else "MISSING" if missing else "CURRENT"
        return {
            "status": status, "expected_session": cutoff.latest_completed_session.isoformat(),
            "calendar_version": cutoff.calendar_version,
            "as_of": cutoff.cutoff_at.isoformat(),
            "required_bases": ["TRADES", "ADJUSTED_LAST"],
            "max_session_lag": max((row["session_lag"] for row in stale), default=None),
            "entries": entries,
        }

    @staticmethod
    def _row(kind, ticker, canonical, market):
        return {
            "kind": kind,
            "canonical_sector": canonical,
            "market_sector_label": market,
            "ticker": ticker,
            "status": "MISSING",
            "first_session": None,
            "last_session": None,
            "bar_count": 0,
            "price_basis": None,
            "session_lag": None,
            "reason": "no_pit_visible_cached_daily_bars",
        }

    def _evaluate(self, row, price: pd.DataFrame, cutoff: MarketCalculationCutoff):
        if price.empty:
            return
        if not {"date", "close"}.issubset(price.columns):
            raise CoverageEvidenceError("invalid_cached_price_columns")
        sessions = [_session(value) for value in price["date"]]
        if any(session > cutoff.latest_completed_session for session in sessions):
            raise CoverageEvidenceError("post_cutoff_session_detected")
        if len(set(sessions)) != len(sessions) or any(
            not is_us_trading_day(session, calendar_version=cutoff.calendar_version)
            for session in sessions
        ):
            raise CoverageEvidenceError("invalid_or_duplicate_market_session")
        # The approved loader enforces these timestamps before projecting OHLCV.
        # Check them too if an adapter supplies receipt columns explicitly.
        for column in ("created_at", "first_seen_at"):
            if column in price.columns:
                for value in price[column]:
                    timestamp = pd.Timestamp(value)
                    if pd.isna(timestamp) or timestamp.tzinfo is None:
                        raise CoverageEvidenceError("invalid_receipt_timestamp")
                    if timestamp > cutoff.cutoff_at:
                        raise CoverageEvidenceError("post_cutoff_observation_detected")
        first, last = min(sessions), max(sessions)
        basis = price.attrs.get("price_basis")
        row.update(
            first_session=first.isoformat(),
            last_session=last.isoformat(),
            bar_count=len(sessions),
            price_basis=basis if basis in {"TRADES", "ADJUSTED_LAST"} else None,
            session_lag=clock_for_cutoff(cutoff).trading_session_distance(
                last, cutoff.latest_completed_session
            ),
        )
        latest_close = price.iloc[sessions.index(last)]["close"]
        try:
            valid_close = (
                not pd.api.types.is_bool(latest_close)
                and math.isfinite(float(latest_close))
                and float(latest_close) > 0
            )
        except (TypeError, ValueError, OverflowError):
            valid_close = False
        if not valid_close:
            raise CoverageEvidenceError("invalid_latest_close")
        current = last == cutoff.latest_completed_session
        row.update(
            status="CURRENT" if current else "STALE",
            reason=(
                "latest_completed_session_cached"
                if current
                else "cached_daily_bars_behind_completed_session"
            ),
        )


def _session(value) -> date:
    if not isinstance(value, (str, date, datetime)):
        raise CoverageEvidenceError("invalid_cached_session_date")
    try:
        timestamp = pd.Timestamp(value)
        if pd.isna(timestamp) or timestamp.time() != time(0):
            raise ValueError("not a daily session label")
        return timestamp.date()
    except (TypeError, ValueError, OverflowError) as exc:
        raise CoverageEvidenceError("invalid_cached_session_date") from exc
