from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriPriceResponseFeature
from app.models.tables import PriceBar
from app.services.ceri.config import CeriConfig, load_ceri_config
from app.services.ceri.effective_session_service import CeriEffectiveSessionService


@dataclass(frozen=True)
class PriceResponseResult:
    quality: float | None
    event_key: str
    event_type: str
    reaction_session: date | None
    metrics: dict[str, Any]
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    price_bar_ids: tuple[int, ...]
    unavailable_reason: str | None = None


class CeriPriceResponseService:
    """Read-only price reaction calculation over cached SwingLens IBKR bars."""

    def __init__(self, config: CeriConfig | None = None) -> None:
        self.config = config or load_ceri_config()
        self.sessions = CeriEffectiveSessionService(self.config.engine.timezone)

    def calculate(
        self,
        db: Session,
        *,
        company_id: int,
        ticker: str,
        event_type: str,
        event_id: int | None,
        event_effective_at: datetime | None = None,
        event_effective_session: date | None = None,
    ) -> PriceResponseResult:
        reaction = self.reaction_session(event_effective_at, event_effective_session)
        event_key = _event_key(
            company_id,
            event_type,
            event_id,
            reaction,
            self.config.config_hash,
            self.config.engine.calculation_version,
        )
        if reaction is None:
            return PriceResponseResult(
                None,
                event_key,
                event_type,
                None,
                {},
                (),
                ("event_session_unavailable",),
                (),
                "EVENT_TIMESTAMP_UNRESOLVED",
            )
        stock = self._bars(db, ticker)
        benchmark = self._bars(db, self.config.price_response.benchmark)
        if not stock:
            return PriceResponseResult(
                None,
                event_key,
                event_type,
                reaction,
                {},
                (),
                ("stock_bars_unavailable",),
                (),
                "PRICE_DATA_MISSING",
            )
        if not benchmark:
            return PriceResponseResult(
                None,
                event_key,
                event_type,
                reaction,
                {},
                (),
                ("benchmark_bars_unavailable",),
                (),
                "PRICE_DATA_MISSING",
            )

        stock_by_date = {bar.bar_date: bar for bar in stock}
        benchmark_by_date = {bar.bar_date: bar for bar in benchmark}
        prior_dates = [day for day in stock_by_date if day < reaction]
        reaction_dates = [day for day in stock_by_date if day >= reaction]
        if not prior_dates or not reaction_dates:
            reason = (
                "WINDOW_NOT_ELAPSED"
                if stock_by_date and reaction > max(stock_by_date)
                else "PRICE_DATA_MISSING"
            )
            return PriceResponseResult(
                None,
                event_key,
                event_type,
                reaction,
                {},
                (),
                ("reaction_bars_unavailable",),
                (),
                reason,
            )
        prior_date = max(prior_dates)
        reaction_date = min(reaction_dates)
        prior = stock_by_date[prior_date]
        first = stock_by_date[reaction_date]
        if prior.close is None or first.open is None:
            return PriceResponseResult(
                None,
                event_key,
                event_type,
                reaction,
                {},
                (),
                ("reaction_prices_unavailable",),
                (),
                "PRICE_DATA_MISSING",
            )

        metrics: dict[str, Any] = {
            "reaction_session": reaction_date.isoformat(),
            "gap_pct": _return(first.open, prior.close),
            "volume_ratio": _volume_ratio(
                stock, reaction_date, self.config.price_response.trailing_volume_sessions
            ),
            "close_location": _close_location(first),
            "benchmark": self.config.price_response.benchmark,
        }
        reasons: list[str] = []
        warnings: list[str] = []
        benchmark_prior = benchmark_by_date.get(prior_date)
        for window in self.config.price_response.windows:
            target = _nth_session(sorted(stock_by_date), reaction_date, window - 1)
            stock_end = stock_by_date.get(target) if target else None
            benchmark_end = benchmark_by_date.get(target) if target else None
            stock_return = (
                _return(stock_end.close, prior.close)
                if stock_end and stock_end.close is not None
                else None
            )
            benchmark_return = (
                _return(benchmark_end.close, benchmark_prior.close)
                if benchmark_end
                and benchmark_prior
                and benchmark_end.close is not None
                and benchmark_prior.close is not None
                else None
            )
            relative = (
                stock_return - benchmark_return
                if stock_return is not None and benchmark_return is not None
                else None
            )
            metrics[f"return_{window}d"] = stock_return
            metrics[f"benchmark_return_{window}d"] = benchmark_return
            metrics[f"relative_return_{window}d"] = relative
            if relative is None:
                warnings.append(f"relative_return_{window}d_unavailable")

        one_day = metrics.get("relative_return_1d")
        volume_ratio = metrics.get("volume_ratio")
        close_location = metrics.get("close_location")
        if one_day is None:
            return PriceResponseResult(
                None,
                event_key,
                event_type,
                reaction,
                metrics,
                tuple(reasons),
                tuple(sorted(set(warnings))),
                _bar_ids(stock, benchmark, reaction_date, self.config.price_response.windows),
                "WINDOW_NOT_ELAPSED",
            )
        score = 5.0
        if one_day >= self.config.price_response.strong_relative_return_threshold:
            score += 2.0
            reasons.append("strong_positive_relative_1d")
        elif one_day >= self.config.price_response.positive_relative_return_threshold:
            score += 1.0
            reasons.append("positive_relative_1d")
        elif one_day <= -self.config.price_response.strong_relative_return_threshold:
            score -= 2.0
            reasons.append("negative_relative_1d")
        elif one_day < 0:
            score -= 1.0
            reasons.append("weak_relative_1d")
        if (
            volume_ratio is not None
            and volume_ratio >= self.config.price_response.volume_confirmation_threshold
        ):
            score += 1.0
            reasons.append("strong_volume_confirmation")
        if close_location is not None:
            if close_location >= 0.6:
                score += 0.5
                reasons.append("constructive_close")
            elif close_location <= 0.4:
                score -= 0.5
                reasons.append("weak_close")
        return PriceResponseResult(
            max(0.0, min(10.0, score)),
            event_key,
            event_type,
            reaction,
            metrics,
            tuple(reasons),
            tuple(sorted(set(warnings))),
            _bar_ids(stock, benchmark, reaction_date, self.config.price_response.windows),
        )

    def unavailable(
        self,
        *,
        company_id: int,
        event_type: str,
        reason: str,
    ) -> PriceResponseResult:
        return PriceResponseResult(
            quality=None,
            event_key=_event_key(
                company_id,
                event_type,
                reason,
                self.config.config_hash,
                self.config.engine.calculation_version,
            ),
            event_type=event_type,
            reaction_session=None,
            metrics={},
            reasons=(),
            warnings=(reason,),
            price_bar_ids=(),
            unavailable_reason=reason,
        )

    def persist(
        self,
        db: Session,
        *,
        result: PriceResponseResult,
        company_id: int,
        ticker: str,
        event_id: int | None,
        event_effective_at: datetime | None,
        event_effective_session: date | None,
    ) -> CeriPriceResponseFeature:
        existing = _maybe_scalar(
            db,
            select(CeriPriceResponseFeature).where(
                CeriPriceResponseFeature.event_key == result.event_key
            ),
        )
        payload = {
            "quality": result.quality,
            "event_key": result.event_key,
            "metrics": result.metrics,
            "reasons": result.reasons,
            "warnings": result.warnings,
            "price_bar_ids": result.price_bar_ids,
            "unavailable_reason": result.unavailable_reason,
            "config_hash": self.config.config_hash,
            "calculation_version": self.config.engine.calculation_version,
        }
        if existing is None:
            existing = CeriPriceResponseFeature(
                company_id=company_id,
                ticker=ticker.upper(),
                event_type=result.event_type,
                event_id=event_id,
                event_effective_at=event_effective_at,
                event_effective_session=event_effective_session,
                reaction_session=result.reaction_session,
                benchmark=self.config.price_response.benchmark,
                event_key=result.event_key,
                config_version=self.config.engine.config_version,
                config_hash=self.config.config_hash,
                calculation_version=self.config.engine.calculation_version,
                evidence_hash=_hash(payload),
            )
            db.add(existing)
        existing.metrics_json = {
            **result.metrics,
            "quality": result.quality,
            "unavailable_reason": result.unavailable_reason,
        }
        existing.reasons_json = [
            *result.reasons,
            *([result.unavailable_reason] if result.unavailable_reason else []),
        ] or None
        existing.warnings_json = list(result.warnings) or None
        existing.price_bar_ids_json = list(result.price_bar_ids) or None
        existing.evidence_hash = _hash(payload)
        db.flush()
        return existing

    def reaction_session(
        self,
        effective_at: datetime | None,
        effective_session: date | None,
    ) -> date | None:
        if effective_at is not None:
            return self.sessions.resolve(
                timestamp=effective_at, source_date=effective_session
            ).effective_session
        if effective_session is None:
            return None
        return self.sessions.next_trading_session(effective_session)

    def _bars(self, db: Session, ticker: str) -> list[PriceBar]:
        rows = _scalars(
            db,
            select(PriceBar)
            .where(PriceBar.ticker == ticker.upper())
            .where(func.lower(PriceBar.timeframe).in_(("1d", "1 day", "day", "daily")))
            .where(
                func.lower(PriceBar.source).in_(("ib", "ibkr", "interactive_brokers"))
            )
            .where(PriceBar.close.is_not(None))
            .order_by(PriceBar.bar_date),
        )
        # Retain the same filtering for lightweight test/session adapters that
        # do not execute SQLAlchemy predicates themselves.
        return sorted(
            [
                row
                for row in rows
                if row.ticker.upper() == ticker.upper()
                and row.timeframe.lower() in {"1d", "1 day", "day", "daily"}
                and row.source.lower() in {"ib", "ibkr", "interactive_brokers"}
                and row.close is not None
            ],
            key=lambda row: row.bar_date,
        )


def _return(end: Decimal | float | None, start: Decimal | float | None) -> float | None:
    if end is None or start in (None, 0):
        return None
    return float((Decimal(str(end)) - Decimal(str(start))) / abs(Decimal(str(start))))


def _nth_session(days: list[date], start: date, offset: int) -> date | None:
    future = [day for day in days if day >= start]
    return future[offset] if offset < len(future) else None


def _volume_ratio(rows: list[PriceBar], reaction: date, trailing: int) -> float | None:
    current = next((row for row in rows if row.bar_date == reaction), None)
    prior = [row.volume for row in rows if row.bar_date < reaction and row.volume is not None][
        -trailing:
    ]
    if current is None or current.volume is None or not prior:
        return None
    average = sum(Decimal(str(value)) for value in prior) / Decimal(len(prior))
    return float(Decimal(str(current.volume)) / average) if average else None


def _close_location(bar: PriceBar) -> float | None:
    if bar.high is None or bar.low is None or bar.close is None or bar.high == bar.low:
        return None
    return float(
        (Decimal(str(bar.close)) - Decimal(str(bar.low)))
        / (Decimal(str(bar.high)) - Decimal(str(bar.low)))
    )


def _bar_ids(
    stock: list[PriceBar], benchmark: list[PriceBar], reaction: date, windows: tuple[int, ...]
) -> tuple[int, ...]:
    dates = {reaction}
    for window in windows:
        target = _nth_session(sorted({row.bar_date for row in stock}), reaction, window - 1)
        if target:
            dates.add(target)
    return tuple(
        sorted(
            {row.id for row in [*stock, *benchmark] if row.id is not None and row.bar_date in dates}
        )
    )


def _event_key(*parts: Any) -> str:
    return _hash({"parts": parts})


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def _scalars(db: Session, statement: Any) -> list[Any]:
    scalars = getattr(db, "scalars", None)
    if not callable(scalars):
        return []
    result = scalars(statement)
    return list(result.all() if hasattr(result, "all") else result)


def _maybe_scalar(db: Session, statement: Any) -> Any | None:
    scalar = getattr(db, "scalar", None)
    return scalar(statement) if callable(scalar) else None
