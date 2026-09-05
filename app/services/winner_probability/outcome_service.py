from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.models.tables import (
    EntryDataStatus,
    EntryModel,
    OutcomeStatus,
    PriceBar,
    WinnerForwardOutcome,
    WinnerPredictionSnapshot,
    WinnerTargetStopOutcome,
)
from app.services.bar_cache_service import price_bar_data_hash
from app.services.price_bar_repository import load_preferred_price_bar_rows
from app.services.sector_rotation_config import load_sector_rotation_config
from app.services.winner_probability.outcome_revision_service import OutcomeRevisionService
from app.services.winner_probability.target_stop_service import TargetStopService
from app.services.winner_probability.temporal_eligibility import temporal_eligibility_sql
from app.services.winner_probability.trading_session_service import (
    is_horizon_complete,
    next_regular_session,
)

BENCHMARK_TICKER = "SPY"


class OutcomeMaturationCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class OutcomeMaturationResult:
    processed: int = 0
    matured: int = 0
    pending: int = 0
    excluded: int = 0
    revised: int = 0
    target_stop_matured: int = 0
    warnings: int = 0
    failed: int = 0
    material_changes: tuple[MaterialOutcomeChange, ...] = ()
    reason_counts: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload = {key: value for key, value in self.__dict__.items() if key != "material_changes"}
        payload["material_evidence_changes"] = len(self.material_changes)
        return payload


@dataclass(frozen=True)
class MaterialOutcomeChange:
    prediction_id: int
    forward_outcome_id: int
    forward_revision_id: int
    target_stop_outcome_id: int
    target_stop_revision_id: int
    outcome_definition_id: int
    material_for_active_contract: bool = True


@dataclass(frozen=True)
class ForwardCalculation:
    values: dict[str, Any]
    ticker_bars: list[PriceBar]
    lineage_bars: list[PriceBar]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class OutcomeBatchContext:
    predictions: dict[int, WinnerPredictionSnapshot]
    bars_by_ticker: dict[str, list[PriceBar]]
    target_stops_by_prediction: dict[int, list[WinnerTargetStopOutcome]]


class OutcomeMaturationService:
    def __init__(
        self,
        *,
        repository: WinnerOutcomeRepository | None = None,
        revision_service: OutcomeRevisionService | None = None,
        target_stop_service: TargetStopService | None = None,
    ) -> None:
        self.repository = repository or WinnerOutcomeRepository()
        self.revision_service = revision_service or OutcomeRevisionService()
        self.target_stop_service = target_stop_service or TargetStopService()

    def process_due_outcomes(
        self,
        db: Session,
        *,
        now: datetime | None = None,
        limit: int = 500,
        should_cancel: Callable[[], bool] | None = None,
        entry_model: str | None = None,
        horizon_sessions: int | None = None,
        due_session: date | None = None,
    ) -> OutcomeMaturationResult:
        now = now or _utcnow()
        completed_on = _latest_completed_for(now)
        selector_kwargs: dict[str, Any] = {"completed_on": completed_on, "limit": limit}
        if entry_model is not None or horizon_sessions is not None or due_session is not None:
            selector_kwargs.update(
                entry_model=entry_model,
                horizon_sessions=horizon_sessions,
                due_session=due_session,
            )
        outcomes = self.repository.get_due_pending_forward_outcomes(db, **selector_kwargs)
        totals = _MutableOutcomeCounts()
        for outcome in outcomes:
            if should_cancel is not None and should_cancel():
                raise OutcomeMaturationCancelled("winner outcome maturation was cancelled")
            self.process_forward_outcome(db, outcome, now=now, totals=totals)
        return totals.to_result()

    def process_current_revisions(
        self,
        db: Session,
        *,
        now: datetime | None = None,
        limit: int = 500,
        forward_outcome_ids: tuple[int, ...] = (),
        should_cancel: Callable[[], bool] | None = None,
    ) -> OutcomeMaturationResult:
        now = now or _utcnow()
        outcomes = self.repository.get_current_matured_forward_outcomes(
            db, limit=limit, forward_outcome_ids=forward_outcome_ids
        )
        context = self.build_batch_context(db, outcomes)
        totals = _MutableOutcomeCounts()
        for outcome in outcomes:
            if should_cancel is not None and should_cancel():
                raise OutcomeMaturationCancelled("winner outcome revision check was cancelled")
            self.process_forward_outcome(db, outcome, now=now, totals=totals, context=context)
        return totals.to_result()

    def build_batch_context(
        self, db: Session, outcomes: list[WinnerForwardOutcome]
    ) -> OutcomeBatchContext:
        return self.repository.load_batch_context(db, outcomes)

    def process_forward_outcome(
        self,
        db: Session,
        outcome: WinnerForwardOutcome,
        *,
        now: datetime | None = None,
        totals: _MutableOutcomeCounts | None = None,
        context: OutcomeBatchContext | None = None,
    ) -> OutcomeMaturationResult:
        now = now or _utcnow()
        totals = totals or _MutableOutcomeCounts()
        totals.processed += 1
        outcome.last_attempted_at = now

        if not is_horizon_complete(outcome.due_session, now):
            _mark_pending(outcome, "horizon_not_complete", now=now)
            totals.pending += 1
            totals.add_reason("horizon_not_complete")
            return totals.to_result()

        prediction = (
            context.predictions.get(outcome.prediction_id)
            if context is not None
            else self.repository.get_prediction(db, outcome.prediction_id)
        )
        if prediction is None:
            _mark_excluded(outcome, "prediction_not_found")
            totals.excluded += 1
            totals.add_reason("prediction_not_found")
            return totals.to_result()

        calculation = self._calculate_forward(db, prediction, outcome, now=now, context=context)
        if calculation is None:
            if outcome.status == OutcomeStatus.EXCLUDED:
                totals.excluded += 1
                totals.add_reason(
                    str((outcome.metadata_json or {}).get("exclusion_reason") or "excluded")
                )
            else:
                totals.pending += 1
                totals.add_reason(outcome.pending_reason_code or "retryable_prerequisite")
            return totals.to_result()

        before_revision = outcome.revision
        matured_outcome, changed = self.revision_service.upsert_forward_revision(
            db,
            outcome,
            calculation.values,
            now=now,
        )
        if changed:
            totals.matured += 1
        if matured_outcome.revision > before_revision:
            totals.revised += 1
        totals.warnings += len(calculation.warnings)

        target_stops = (
            context.target_stops_by_prediction.get(prediction.id, [])
            if context is not None
            else self.repository.get_target_stop_outcomes_for_forward(
                db,
                prediction_id=prediction.id,
                entry_model=matured_outcome.entry_model,
                horizon_sessions=matured_outcome.horizon_sessions,
            )
        )
        for target_stop in target_stops:
            matured_target, material = self._mature_target_stop(
                db, target_stop, matured_outcome, calculation, now=now
            )
            if material:
                totals.target_stop_matured += 1
                totals.material_changes.append(
                    MaterialOutcomeChange(
                        prediction_id=prediction.id,
                        forward_outcome_id=matured_outcome.id,
                        forward_revision_id=matured_outcome.id,
                        target_stop_outcome_id=matured_target.id,
                        target_stop_revision_id=matured_target.id,
                        outcome_definition_id=matured_target.outcome_definition_id,
                    )
                )

        return totals.to_result()

    def _calculate_forward(
        self,
        db: Session,
        prediction: WinnerPredictionSnapshot,
        outcome: WinnerForwardOutcome,
        *,
        now: datetime,
        context: OutcomeBatchContext | None = None,
    ) -> ForwardCalculation | None:
        if outcome.entry_session is None or outcome.due_session is None:
            _mark_pending(outcome, "unresolved_entry_or_due_session", now=now)
            return None

        ticker_bars = (
            _bars_in_range(
                context.bars_by_ticker.get(prediction.ticker.upper(), []),
                outcome.entry_session,
                outcome.due_session,
            )
            if context is not None
            else self.repository.load_bars(
                db,
                prediction.ticker,
                start_date=outcome.entry_session,
                end_date=outcome.due_session,
            )
        )
        if not ticker_bars:
            prediction.entry_data_status = EntryDataStatus.MISSING
            _mark_pending(outcome, "missing_entry_bar", now=now)
            return None
        entry_bar = _bar_by_date(ticker_bars, outcome.entry_session)
        due_bar = _bar_by_date(ticker_bars, outcome.due_session)
        if entry_bar is None:
            prediction.entry_data_status = EntryDataStatus.MISSING
            _mark_pending(outcome, "missing_entry_bar", now=now)
            return None
        if not _has_required_sessions(ticker_bars, outcome.entry_session, outcome.due_session):
            _mark_pending(outcome, "missing_horizon_bar", now=now)
            return None
        validation_error = _validate_bars(ticker_bars)
        if validation_error:
            prediction.entry_data_status = EntryDataStatus.INVALID
            _mark_excluded(outcome, validation_error)
            return None

        if due_bar is None:
            _mark_pending(outcome, "missing_due_bar", now=now)
            return None

        entry_price = _entry_price(outcome.entry_model, entry_bar)
        if entry_price is None or entry_price <= 0:
            prediction.entry_data_status = EntryDataStatus.INVALID
            _mark_excluded(outcome, "invalid_entry_price")
            return None

        prediction.entry_data_status = EntryDataStatus.AVAILABLE
        outcome.pending_reason_code = None
        outcome.retry_not_before_at = None
        exit_price = Decimal(str(due_bar.close))
        close_return = _return_pct(exit_price, entry_price)
        mfe, sessions_to_mfe = _max_excursion(ticker_bars, entry_price, field_name="high")
        mae, sessions_to_mae = _min_excursion(ticker_bars, entry_price, field_name="low")

        lineage_bars = list(ticker_bars)
        warnings: list[str] = []
        spy_return = self._comparison_return(
            db,
            BENCHMARK_TICKER,
            outcome,
            entry_price_from_model=outcome.entry_model,
            lineage_bars=lineage_bars,
            warnings=warnings,
            context=context,
        )
        sector_return = self._sector_return(
            db,
            prediction,
            outcome,
            lineage_bars=lineage_bars,
            warnings=warnings,
            context=context,
        )
        lineage_hash, source_cutoff = _source_lineage(lineage_bars)
        values = {
            "status": OutcomeStatus.MATURED,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "close_return_pct": close_return,
            "spy_return_pct": spy_return,
            "excess_spy_return_pct": (
                close_return - spy_return if spy_return is not None else None
            ),
            "sector_return_pct": sector_return,
            "excess_sector_return_pct": (
                close_return - sector_return if sector_return is not None else None
            ),
            "mfe_pct": mfe,
            "mae_pct": mae,
            "sessions_to_mfe": sessions_to_mfe,
            "sessions_to_mae": sessions_to_mae,
            "positive_return": close_return > 0,
            "beat_spy": close_return > spy_return if spy_return is not None else None,
            "beat_sector": close_return > sector_return if sector_return is not None else None,
            "source_bar_lineage_hash": lineage_hash,
            "source_revision_cutoff_at": source_cutoff,
            "matured_at": now,
            "metadata_json": {
                **(outcome.metadata_json or {}),
                "warnings": warnings,
                "calculation_phase": "phase_5",
            },
        }
        return ForwardCalculation(
            values=values,
            ticker_bars=ticker_bars,
            lineage_bars=lineage_bars,
            warnings=tuple(warnings),
        )

    def _comparison_return(
        self,
        db: Session,
        ticker: str,
        outcome: WinnerForwardOutcome,
        *,
        entry_price_from_model: str,
        lineage_bars: list[PriceBar],
        warnings: list[str],
        context: OutcomeBatchContext | None = None,
    ) -> Decimal | None:
        bars = (
            _bars_in_range(
                context.bars_by_ticker.get(ticker.upper(), []),
                outcome.entry_session,
                outcome.due_session,
            )
            if context is not None
            else self.repository.load_bars(
                db,
                ticker,
                start_date=outcome.entry_session,
                end_date=outcome.due_session,
            )
        )
        if not bars or not _has_required_sessions(bars, outcome.entry_session, outcome.due_session):
            warnings.append(f"missing_{ticker.lower()}_benchmark_data")
            return None
        if _validate_bars(bars):
            warnings.append(f"invalid_{ticker.lower()}_benchmark_data")
            return None
        entry_bar = _bar_by_date(bars, outcome.entry_session)
        due_bar = _bar_by_date(bars, outcome.due_session)
        if entry_bar is None or due_bar is None:
            warnings.append(f"missing_{ticker.lower()}_benchmark_data")
            return None
        entry_price = _entry_price(entry_price_from_model, entry_bar)
        if entry_price is None or entry_price <= 0:
            warnings.append(f"invalid_{ticker.lower()}_entry_price")
            return None
        lineage_bars.extend(bars)
        return _return_pct(Decimal(str(due_bar.close)), entry_price)

    def _sector_return(
        self,
        db: Session,
        prediction: WinnerPredictionSnapshot,
        outcome: WinnerForwardOutcome,
        *,
        lineage_bars: list[PriceBar],
        warnings: list[str],
        context: OutcomeBatchContext | None = None,
    ) -> Decimal | None:
        sector = _prediction_sector(prediction)
        proxy = _sector_proxy(sector)
        if not proxy:
            warnings.append("missing_sector_proxy")
            return None
        return self._comparison_return(
            db,
            proxy,
            outcome,
            entry_price_from_model=outcome.entry_model,
            lineage_bars=lineage_bars,
            warnings=warnings,
            context=context,
        )

    def _mature_target_stop(
        self,
        db: Session,
        target_stop: WinnerTargetStopOutcome,
        forward_outcome: WinnerForwardOutcome,
        calculation: ForwardCalculation,
        *,
        now: datetime,
    ) -> tuple[WinnerTargetStopOutcome, bool]:
        evaluation = self.target_stop_service.evaluate(
            bars=calculation.ticker_bars,
            entry_price=Decimal(str(forward_outcome.entry_price)),
            target_pct=Decimal(str(target_stop.target_pct)),
            stop_pct=Decimal(str(target_stop.stop_pct)),
            same_bar_conflict_policy=getattr(
                getattr(target_stop, "outcome_definition", None),
                "same_bar_conflict_policy",
                "CONSERVATIVE_STOP_FIRST",
            ),
        )
        values = {
            "forward_outcome_id": forward_outcome.id,
            "status": OutcomeStatus.MATURED,
            "target_hit": evaluation.target_hit,
            "stop_hit": evaluation.stop_hit,
            "first_event": evaluation.first_event,
            "event_session": evaluation.event_session,
            "same_bar_conflict": evaluation.same_bar_conflict,
            "primary_winner": evaluation.primary_winner,
            "optimistic_winner": evaluation.optimistic_winner,
            "conservative_winner": evaluation.conservative_winner,
            "source_bar_lineage_hash": forward_outcome.source_bar_lineage_hash,
            "evaluated_at": now,
            "metadata_json": {
                **(target_stop.metadata_json or {}),
                "calculation_phase": "phase_5",
            },
        }
        before_revision = target_stop.revision
        matured, changed = self.revision_service.upsert_target_stop_revision(
            db,
            target_stop,
            values,
            now=now,
        )
        return matured, changed or matured.revision > before_revision


class WinnerOutcomeRepository:
    def get_due_pending_forward_outcomes(
        self,
        db: Session,
        *,
        completed_on: date,
        limit: int,
        entry_model: str | None = None,
        horizon_sessions: int | None = None,
        due_session: date | None = None,
        exclude_ids: tuple[int, ...] = (),
        retry_as_of: datetime | None = None,
    ) -> list[WinnerForwardOutcome]:
        retry_as_of = retry_as_of or _utcnow()
        statement = (
            select(WinnerForwardOutcome)
            .join(
                WinnerPredictionSnapshot,
                WinnerPredictionSnapshot.id == WinnerForwardOutcome.prediction_id,
            )
            .where(WinnerForwardOutcome.status == OutcomeStatus.PENDING)
            .where(WinnerForwardOutcome.is_current_revision.is_(True))
            .where(WinnerForwardOutcome.due_session <= completed_on)
            .where(temporal_eligibility_sql(WinnerPredictionSnapshot))
            .where(
                (WinnerForwardOutcome.retry_not_before_at.is_(None))
                | (WinnerForwardOutcome.retry_not_before_at <= retry_as_of)
            )
            # Try never-attempted outcomes before retrying rows already
            # blocked by missing bars. Otherwise the same old missing rows
            # consume most of every bounded batch and starve later horizons.
            .order_by(
                case(
                    (
                        WinnerForwardOutcome.metadata_json["pending_reason"].as_string().is_(None),
                        0,
                    ),
                    else_=1,
                ),
                WinnerForwardOutcome.due_session,
                WinnerForwardOutcome.id,
            )
            .limit(limit)
        )
        if entry_model is not None:
            statement = statement.where(WinnerForwardOutcome.entry_model == entry_model)
        if horizon_sessions is not None:
            statement = statement.where(WinnerForwardOutcome.horizon_sessions == horizon_sessions)
        if due_session is not None:
            statement = statement.where(WinnerForwardOutcome.due_session <= due_session)
        if exclude_ids:
            statement = statement.where(WinnerForwardOutcome.id.not_in(exclude_ids))
        return list(db.scalars(statement))

    def load_batch_context(
        self,
        db: Session,
        outcomes: list[WinnerForwardOutcome],
    ) -> OutcomeBatchContext:
        prediction_ids = sorted({row.prediction_id for row in outcomes})
        predictions = {
            row.id: row
            for row in db.scalars(
                select(WinnerPredictionSnapshot).where(
                    WinnerPredictionSnapshot.id.in_(prediction_ids)
                )
            )
        }
        target_stops: dict[int, list[WinnerTargetStopOutcome]] = {}
        for row in db.scalars(
            select(WinnerTargetStopOutcome)
            .where(WinnerTargetStopOutcome.prediction_id.in_(prediction_ids))
            .where(WinnerTargetStopOutcome.is_current_revision.is_(True))
            .order_by(WinnerTargetStopOutcome.prediction_id, WinnerTargetStopOutcome.id)
        ):
            target_stops.setdefault(row.prediction_id, []).append(row)
        symbols = {BENCHMARK_TICKER}
        sectors = {_prediction_sector(prediction) for prediction in predictions.values()}
        sector_proxies = {sector: _sector_proxy(sector) for sector in sectors}
        for prediction in predictions.values():
            symbols.add(prediction.ticker.upper())
            proxy = sector_proxies.get(_prediction_sector(prediction))
            if proxy:
                symbols.add(proxy)
        starts = [row.entry_session for row in outcomes if row.entry_session is not None]
        ends = [row.due_session for row in outcomes if row.due_session is not None]
        bars_by_ticker: dict[str, list[PriceBar]] = {}
        if starts and ends and symbols:
            raw_bars = list(
                db.scalars(
                    select(PriceBar)
                    .where(PriceBar.ticker.in_(sorted(symbols)))
                    .where(PriceBar.timeframe == "1 day")
                    .where(PriceBar.what_to_show.in_(("ADJUSTED_LAST", "TRADES")))
                    .where(PriceBar.bar_date >= min(starts))
                    .where(PriceBar.bar_date <= max(ends))
                    .order_by(PriceBar.ticker, PriceBar.bar_date, PriceBar.id)
                )
            )
            by_basis: dict[tuple[str, str], list[PriceBar]] = {}
            for bar in raw_bars:
                by_basis.setdefault((bar.ticker.upper(), bar.what_to_show), []).append(bar)
            for symbol in symbols:
                bars_by_ticker[symbol] = by_basis.get((symbol, "ADJUSTED_LAST")) or by_basis.get(
                    (symbol, "TRADES"), []
                )
        return OutcomeBatchContext(
            predictions=predictions,
            bars_by_ticker=bars_by_ticker,
            target_stops_by_prediction=target_stops,
        )

    def get_current_matured_forward_outcomes(
        self,
        db: Session,
        *,
        limit: int,
        forward_outcome_ids: tuple[int, ...] = (),
    ) -> list[WinnerForwardOutcome]:
        statement = (
            select(WinnerForwardOutcome)
            .join(
                WinnerPredictionSnapshot,
                WinnerPredictionSnapshot.id == WinnerForwardOutcome.prediction_id,
            )
            .where(WinnerForwardOutcome.status == OutcomeStatus.MATURED)
            .where(WinnerForwardOutcome.is_current_revision.is_(True))
            .where(temporal_eligibility_sql(WinnerPredictionSnapshot))
            .order_by(WinnerForwardOutcome.id)
            .limit(limit)
        )
        if forward_outcome_ids:
            statement = statement.where(WinnerForwardOutcome.id.in_(forward_outcome_ids))
        return list(db.scalars(statement))

    def get_prediction(
        self,
        db: Session,
        prediction_id: int,
    ) -> WinnerPredictionSnapshot | None:
        return db.get(WinnerPredictionSnapshot, prediction_id)

    def get_target_stop_outcomes_for_forward(
        self,
        db: Session,
        *,
        prediction_id: int,
        entry_model: str,
        horizon_sessions: int,
    ) -> list[WinnerTargetStopOutcome]:
        return list(
            db.scalars(
                select(WinnerTargetStopOutcome)
                .where(WinnerTargetStopOutcome.prediction_id == prediction_id)
                .where(WinnerTargetStopOutcome.entry_model == entry_model)
                .where(WinnerTargetStopOutcome.horizon_sessions == horizon_sessions)
                .where(WinnerTargetStopOutcome.is_current_revision.is_(True))
                .order_by(WinnerTargetStopOutcome.id)
            )
        )

    def load_bars(
        self,
        db: Session,
        ticker: str,
        *,
        start_date: date,
        end_date: date,
    ) -> list[PriceBar]:
        return load_preferred_price_bar_rows(
            db,
            ticker,
            start_date=start_date,
            end_date=end_date,
        )


@dataclass
class _MutableOutcomeCounts:
    processed: int = 0
    matured: int = 0
    pending: int = 0
    excluded: int = 0
    revised: int = 0
    target_stop_matured: int = 0
    warnings: int = 0
    failed: int = 0
    material_changes: list[MaterialOutcomeChange] = field(default_factory=list)
    reason_counts: dict[str, int] = field(default_factory=dict)

    def add_reason(self, reason: str) -> None:
        self.reason_counts[reason] = self.reason_counts.get(reason, 0) + 1

    def to_result(self) -> OutcomeMaturationResult:
        return OutcomeMaturationResult(
            **{
                **self.__dict__,
                "material_changes": tuple(self.material_changes),
            }
        )


def _has_required_sessions(bars: list[PriceBar], start: date | None, end: date | None) -> bool:
    if start is None or end is None:
        return False
    available = {row.bar_date for row in bars}
    cursor = start
    while cursor <= end:
        if cursor not in available:
            return False
        if cursor == end:
            return True
        cursor = next_regular_session(cursor)
    return True


def _validate_bars(bars: list[PriceBar]) -> str | None:
    adjustment_basis = {(row.what_to_show, row.adjustment_type or "") for row in bars}
    if len(adjustment_basis) > 1:
        return "adjustment_mismatch"
    for row in bars:
        values = [row.open, row.high, row.low, row.close]
        if any(value is None for value in values):
            return "missing_ohlc"
        open_price = Decimal(str(row.open))
        high = Decimal(str(row.high))
        low = Decimal(str(row.low))
        close = Decimal(str(row.close))
        if min(open_price, close) < low or max(open_price, close) > high or low > high:
            return "invalid_ohlc"
    return None


def _bar_by_date(bars: list[PriceBar], bar_date: date | None) -> PriceBar | None:
    return next((row for row in bars if row.bar_date == bar_date), None)


def _entry_price(entry_model: str, entry_bar: PriceBar) -> Decimal | None:
    if entry_model == EntryModel.SIGNAL_CLOSE_DIAGNOSTIC:
        return Decimal(str(entry_bar.close)) if entry_bar.close is not None else None
    return Decimal(str(entry_bar.open)) if entry_bar.open is not None else None


def _return_pct(exit_price: Decimal, entry_price: Decimal) -> Decimal:
    return ((exit_price - entry_price) / entry_price * Decimal("100")).quantize(Decimal("0.000001"))


def _max_excursion(
    bars: list[PriceBar],
    entry_price: Decimal,
    *,
    field_name: str,
) -> tuple[Decimal, int]:
    values = [
        ((Decimal(str(getattr(row, field_name))) - entry_price) / entry_price * Decimal("100"), i)
        for i, row in enumerate(sorted(bars, key=lambda item: item.bar_date), start=1)
    ]
    value, session = max(values, key=lambda item: item[0])
    return value.quantize(Decimal("0.000001")), session


def _min_excursion(
    bars: list[PriceBar],
    entry_price: Decimal,
    *,
    field_name: str,
) -> tuple[Decimal, int]:
    values = [
        ((Decimal(str(getattr(row, field_name))) - entry_price) / entry_price * Decimal("100"), i)
        for i, row in enumerate(sorted(bars, key=lambda item: item.bar_date), start=1)
    ]
    value, session = min(values, key=lambda item: item[0])
    return value.quantize(Decimal("0.000001")), session


def _source_lineage(bars: list[PriceBar]) -> tuple[str, datetime | None]:
    payload = [
        {
            "ticker": row.ticker.upper(),
            "bar_date": row.bar_date.isoformat(),
            "what_to_show": row.what_to_show,
            "timeframe": row.timeframe,
            "data_hash": row.data_hash or price_bar_data_hash(row),
            "revision_count": row.revision_count,
        }
        for row in sorted(bars, key=lambda item: (item.ticker, item.bar_date, item.what_to_show))
    ]
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    cutoff = max(
        (
            value
            for row in bars
            for value in (row.revised_at, row.last_seen_at, row.created_at)
            if isinstance(value, datetime)
        ),
        default=None,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest(), cutoff


def _prediction_sector(prediction: WinnerPredictionSnapshot) -> str | None:
    feature_json = prediction.feature_json or {}
    return (
        feature_json.get("canonical_sector")
        or feature_json.get("raw_sector")
        or getattr(prediction, "sector", None)
    )


def _sector_proxy(sector: str | None) -> str | None:
    if not sector:
        return None
    try:
        config = load_sector_rotation_config()
    except Exception:
        return None
    proxy = config.get("sector_etf_proxies", {}).get(sector)
    return str(proxy).upper() if proxy else None


def _mark_pending(
    outcome: WinnerForwardOutcome,
    reason: str,
    *,
    now: datetime,
) -> None:
    outcome.status = OutcomeStatus.PENDING
    outcome.pending_reason_code = reason
    outcome.last_attempted_at = now
    outcome.retry_not_before_at = now + timedelta(minutes=15)
    outcome.metadata_json = {**(outcome.metadata_json or {}), "pending_reason": reason}


def _mark_excluded(outcome: WinnerForwardOutcome, reason: str) -> None:
    outcome.status = OutcomeStatus.EXCLUDED
    outcome.pending_reason_code = None
    outcome.retry_not_before_at = None
    outcome.metadata_json = {**(outcome.metadata_json or {}), "exclusion_reason": reason}


def _bars_in_range(bars: list[PriceBar], start_date: date, end_date: date) -> list[PriceBar]:
    return [row for row in bars if start_date <= row.bar_date <= end_date]


def _latest_completed_for(now: datetime) -> date:
    from app.services.winner_probability.trading_session_service import latest_completed_session

    return latest_completed_session(now)


def _utcnow() -> datetime:
    return datetime.now(UTC)
