from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriPriceResponseFeature
from app.models.tables import PriceBar
from app.services.canonical_evidence import CanonicalEvidenceSerializer
from app.services.ceri.config import CeriConfig, load_ceri_config
from app.services.ceri.effective_session_service import CeriEffectiveSessionService
from app.services.ceri.pit_eligibility import (
    price_bar_is_eligible,
    price_bar_knowledge_predicates,
)
from app.services.market_clock_service import MarketClockService, SessionTimestampPolicy
from app.services.operational_metrics import operational_metrics
from app.services.us_market_calendar import next_us_trading_day, previous_us_trading_day

REACTION_POLICY_VERSION = "daily-open-causal-v1"


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
        stock_bars: list[PriceBar] | None = None,
        benchmark_bars: list[PriceBar] | None = None,
        feature_as_of_session: date | None = None,
        cutoff_at: datetime | None = None,
    ) -> PriceResponseResult:
        reaction = self.reaction_session(event_effective_at, event_effective_session)
        if event_effective_at is not None and reaction is not None:
            effective = MarketClockService().canonical_session_for_timestamp(
                event_effective_at,
                policy=SessionTimestampPolicy.EVENT_EFFECTIVE_SESSION,
            )
            if reaction != effective:
                operational_metrics.increment(
                    "swinglens_ceri_reaction_sessions_total",
                    result="shifted_to_next_session",
                )
        event_key = _event_key(
            company_id,
            event_type,
            event_id,
            reaction,
            self.config.config_hash,
            self.config.engine.calculation_version,
            REACTION_POLICY_VERSION,
            *([cutoff_at.isoformat()] if cutoff_at is not None else []),
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
        stock = (
            stock_bars
            if stock_bars is not None
            else self._bars(
                db,
                ticker,
                max_session=feature_as_of_session,
                cutoff_at=cutoff_at,
            )
        )
        benchmark = (
            benchmark_bars
            if benchmark_bars is not None
            else self._bars(
                db,
                self.config.price_response.benchmark,
                max_session=feature_as_of_session,
                cutoff_at=cutoff_at,
            )
        )
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
        prior_date = previous_us_trading_day(reaction)
        reaction_date = reaction
        if prior_date not in stock_by_date or reaction_date not in stock_by_date:
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
            "prior_reference_session": prior_date.isoformat(),
            "reaction_policy_version": REACTION_POLICY_VERSION,
            "window_session_map": {},
        }
        reasons: list[str] = []
        warnings: list[str] = []
        benchmark_prior = benchmark_by_date.get(prior_date)
        for window in self.config.price_response.windows:
            target = _trading_window_session(reaction_date, window - 1)
            metrics["window_session_map"][f"H{window}"] = target.isoformat()
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
        cutoff_at: datetime | None = None,
    ) -> PriceResponseResult:
        return PriceResponseResult(
            quality=None,
            event_key=_event_key(
                company_id,
                event_type,
                reason,
                self.config.config_hash,
                self.config.engine.calculation_version,
                REACTION_POLICY_VERSION,
                *([cutoff_at.isoformat()] if cutoff_at is not None else []),
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
        feature_as_of_session: date | None = None,
        cutoff_at: datetime | None = None,
        calculation_context_id: int | None = None,
        calendar_version: str | None = None,
    ) -> CeriPriceResponseFeature:
        existing = _maybe_scalar(
            db,
            select(CeriPriceResponseFeature).where(
                CeriPriceResponseFeature.event_key == result.event_key
            ),
        )
        feature = self.build_feature(
            result=result,
            company_id=company_id,
            ticker=ticker,
            event_id=event_id,
            event_effective_at=event_effective_at,
            event_effective_session=event_effective_session,
            feature_as_of_session=feature_as_of_session,
            cutoff_at=cutoff_at,
            calculation_context_id=calculation_context_id,
            calendar_version=calendar_version,
        )
        if existing is None:
            existing = feature
            db.add(existing)
        else:
            existing.metrics_json = feature.metrics_json
            existing.reasons_json = feature.reasons_json
            existing.warnings_json = feature.warnings_json
            existing.price_bar_ids_json = feature.price_bar_ids_json
            existing.evidence_hash = feature.evidence_hash
        db.flush()
        return existing

    def build_feature(
        self,
        *,
        result: PriceResponseResult,
        company_id: int,
        ticker: str,
        event_id: int | None,
        event_effective_at: datetime | None,
        event_effective_session: date | None,
        feature_as_of_session: date | None = None,
        cutoff_at: datetime | None = None,
        calculation_context_id: int | None = None,
        calendar_version: str | None = None,
    ) -> CeriPriceResponseFeature:
        """Build a persistence row without querying or flushing the database."""
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
        return CeriPriceResponseFeature(
            company_id=company_id,
            ticker=ticker.upper(),
            event_type=result.event_type,
            event_id=event_id,
            event_effective_at=event_effective_at,
            event_effective_session=event_effective_session,
            reaction_session=result.reaction_session,
            feature_as_of_session=feature_as_of_session,
            calculation_cutoff_at=cutoff_at,
            calculation_context_id=calculation_context_id,
            calendar_version=calendar_version,
            reaction_start_session=result.reaction_session,
            prior_reference_session=(
                date.fromisoformat(str(result.metrics["prior_reference_session"]))
                if result.metrics.get("prior_reference_session")
                else None
            ),
            window_session_map_json=dict(result.metrics.get("window_session_map") or {}),
            reaction_policy_version=REACTION_POLICY_VERSION,
            benchmark=self.config.price_response.benchmark,
            metrics_json={
                **result.metrics,
                "quality": result.quality,
                "unavailable_reason": result.unavailable_reason,
            },
            reasons_json=[
                *result.reasons,
                *([result.unavailable_reason] if result.unavailable_reason else []),
            ]
            or None,
            warnings_json=list(result.warnings) or None,
            price_bar_ids_json=list(result.price_bar_ids) or None,
            event_key=result.event_key,
            config_version=self.config.engine.config_version,
            config_hash=self.config.config_hash,
            calculation_version=self.config.engine.calculation_version,
            evidence_hash=_hash(payload),
        )

    def reaction_session(
        self,
        effective_at: datetime | None,
        effective_session: date | None,
    ) -> date | None:
        if effective_at is not None:
            return MarketClockService().canonical_session_for_timestamp(
                effective_at,
                policy=SessionTimestampPolicy.NEXT_MARKET_OPEN_AFTER_EVENT,
            )
        # A date-only event cannot establish whether the information arrived
        # before or after the market open.  Fail closed instead of assigning a
        # same-day reaction window that may precede the event.
        return None

    def _bars(
        self,
        db: Session,
        ticker: str,
        *,
        max_session: date | None = None,
        cutoff_at: datetime | None = None,
    ) -> list[PriceBar]:
        statement = (
            select(PriceBar)
            .where(PriceBar.ticker == ticker.upper())
            .where(func.lower(PriceBar.timeframe).in_(("1d", "1 day", "day", "daily")))
            .where(func.lower(PriceBar.source).in_(("ib", "ibkr", "interactive_brokers")))
            .where(PriceBar.close.is_not(None))
        )
        if max_session is not None:
            statement = statement.where(PriceBar.bar_date <= max_session)
        if cutoff_at is not None:
            if max_session is None:
                raise ValueError("max_session is required for point-in-time price-bar loading")
            statement = statement.where(
                *price_bar_knowledge_predicates(
                    latest_completed_session=max_session,
                    cutoff_at=cutoff_at,
                )
            )
        rows = _scalars(
            db,
            statement.order_by(PriceBar.bar_date),
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
                and (max_session is None or row.bar_date <= max_session)
                and (
                    cutoff_at is None
                    or price_bar_is_eligible(
                        row,
                        latest_completed_session=max_session,
                        cutoff_at=cutoff_at,
                    )
                )
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


def _trading_window_session(start: date, offset: int) -> date:
    current = start
    for _ in range(max(0, offset)):
        current = next_us_trading_day(current)
    return current


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
        dates.add(_trading_window_session(reaction, window - 1))
    return tuple(
        sorted(
            {row.id for row in [*stock, *benchmark] if row.id is not None and row.bar_date in dates}
        )
    )


def _event_key(*parts: Any) -> str:
    return _hash({"parts": parts})


def _hash(value: Any) -> str:
    return CanonicalEvidenceSerializer.fingerprint(value)


def _scalars(db: Session, statement: Any) -> list[Any]:
    scalars = getattr(db, "scalars", None)
    if not callable(scalars):
        return []
    result = scalars(statement)
    return list(result.all() if hasattr(result, "all") else result)


def _maybe_scalar(db: Session, statement: Any) -> Any | None:
    scalar = getattr(db, "scalar", None)
    return scalar(statement) if callable(scalar) else None
