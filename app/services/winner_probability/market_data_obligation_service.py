from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import (
    IBContract,
    IBFetchRun,
    PriceBar,
    WinnerForwardOutcome,
    WinnerMarketDataObligation,
    WinnerPredictionSnapshot,
)
from app.services.core_mutation_authority import core_writer_transaction
from app.services.us_market_calendar import next_us_trading_day
from app.services.winner_probability.temporal_eligibility import (
    load_current_temporal_decisions,
    prediction_temporally_eligible,
)

SUPPORTED_BASES = ("ADJUSTED_LAST", "TRADES")
EMPTY_PRICE_WATERMARK = hashlib.sha256(b"[]").hexdigest()


@dataclass(frozen=True)
class RecoveryNeed:
    outcome_id: int
    prediction_id: int
    contract_id: int
    ib_conid: int
    ticker: str
    what_to_show: str
    missing_sessions: tuple[date, ...]


@dataclass(frozen=True)
class RecoveryRequest:
    contract_id: int
    ib_conid: int
    ticker: str
    what_to_show: str
    first_missing_session: date
    last_missing_session: date
    missing_sessions: tuple[date, ...]
    outcome_ids: tuple[int, ...]
    prediction_ids: tuple[int, ...]


@dataclass(frozen=True)
class ObligationSyncResult:
    created: int = 0
    updated: int = 0
    satisfied: int = 0
    fetch_required: int = 0
    identity_blocked: int = 0
    unavailable: int = 0
    failed: int = 0
    excluded: int = 0


@dataclass(frozen=True)
class GlobalDailyBarLag:
    latest_completed_session: date
    latest_local_session: date | None
    lag_sessions: int
    degraded: bool


def required_outcome_sessions(entry_session: date, horizon_sessions: int) -> tuple[date, ...]:
    if horizon_sessions < 1:
        raise ValueError("horizon_sessions must be positive")
    result = [entry_session]
    while len(result) < horizon_sessions:
        result.append(next_us_trading_day(result[-1]))
    return tuple(result)


def complete_basis_for_rows(
    rows: Sequence[PriceBar],
    required_sessions: Sequence[date],
) -> tuple[str | None, tuple[PriceBar, ...]]:
    required = tuple(required_sessions)
    for basis in SUPPORTED_BASES:
        by_date = {
            row.bar_date: row
            for row in rows
            if row.what_to_show == basis and row.bar_date in required
        }
        if all(session in by_date for session in required):
            return basis, tuple(by_date[session] for session in required)
    return None, ()


def price_series_watermark(rows: Iterable[PriceBar]) -> str:
    payload = [
        {
            "bar_date": row.bar_date.isoformat(),
            "data_hash": row.data_hash,
            "id": int(row.id),
            "revision_count": int(row.revision_count or 0),
            "what_to_show": row.what_to_show,
        }
        for row in sorted(rows, key=lambda row: (row.what_to_show, row.bar_date, int(row.id)))
    ]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _native_obligations(
    db: Session, obligations: Sequence[WinnerMarketDataObligation]
) -> list[WinnerMarketDataObligation]:
    """Select native work without laundering legacy rows into current authority.

    A native prediction with a missing obligation seal is corruption, not
    legacy compatibility, and must still fail closed in the native writer.
    """
    if not obligations:
        return []
    prediction_ids = sorted({int(row.prediction_id) for row in obligations})
    predictions = {
        int(row.id): row
        for row in db.scalars(
            select(WinnerPredictionSnapshot).where(WinnerPredictionSnapshot.id.in_(prediction_ids))
        )
    }
    native = []
    for obligation in obligations:
        prediction = predictions.get(int(obligation.prediction_id))
        if prediction is None:
            raise ValueError("MUTATION_WINNER_OBLIGATION_PREDICTION_REQUIRED")
        capture_proof = (prediction.lineage_json or {}).get("native_capture_proof")
        scope_proof = (obligation.metadata_json or {}).get("native_obligation_scope")
        if capture_proof is None and scope_proof is None:
            continue  # Historical serving-only evidence is never rewritten.
        if capture_proof is None:
            raise ValueError("MUTATION_WINNER_CERTIFIED_CAPTURE_REQUIRED")
        if scope_proof is None:
            raise ValueError("MUTATION_WINNER_CERTIFIED_OBLIGATION_SCOPE_REQUIRED")
        native.append(obligation)
    return native


def build_recovery_request_plan(needs: Sequence[RecoveryNeed]) -> tuple[RecoveryRequest, ...]:
    grouped: dict[tuple[int, int, str, str], list[RecoveryNeed]] = defaultdict(list)
    for need in needs:
        grouped[(need.contract_id, need.ib_conid, need.ticker, need.what_to_show)].append(need)

    requests: list[RecoveryRequest] = []
    for (contract_id, conid, ticker, basis), group in sorted(grouped.items()):
        sessions = sorted({session for need in group for session in need.missing_sessions})
        for contiguous in _contiguous_session_groups(sessions):
            involved = [
                need for need in group if set(need.missing_sessions).intersection(contiguous)
            ]
            requests.append(
                RecoveryRequest(
                    contract_id=contract_id,
                    ib_conid=conid,
                    ticker=ticker,
                    what_to_show=basis,
                    first_missing_session=contiguous[0],
                    last_missing_session=contiguous[-1],
                    missing_sessions=tuple(contiguous),
                    outcome_ids=tuple(sorted({need.outcome_id for need in involved})),
                    prediction_ids=tuple(sorted({need.prediction_id for need in involved})),
                )
            )
    return tuple(requests)


class MarketDataObligationService:
    """Persist and evaluate Winner bar dependencies without invoking maturation."""

    @core_writer_transaction
    def ensure_for_outcomes(
        self,
        db: Session,
        outcomes: Sequence[WinnerForwardOutcome],
        *,
        excluded_tickers: frozenset[str] = frozenset(),
        now: datetime | None = None,
    ) -> ObligationSyncResult:
        if isinstance(db, Session):
            from app.services.winner_probability.outcome_authority import obligation_authority

            obligation_authority(
                db,
                [
                    row
                    for row in outcomes
                    if row.entry_model == "NEXT_OPEN" and row.horizon_sessions == 5
                ],
                now=now,
                writer="MarketDataObligationService.ensure_for_outcomes",
            )
        now = now or datetime.now(UTC)
        prediction_ids = {int(row.prediction_id) for row in outcomes}
        predictions = {
            int(row.id): row
            for row in db.scalars(
                select(WinnerPredictionSnapshot).where(
                    WinnerPredictionSnapshot.id.in_(sorted(prediction_ids))
                )
            )
        }
        decisions = load_current_temporal_decisions(db, prediction_ids)
        tickers = {prediction.ticker.upper() for prediction in predictions.values()}
        contracts = {
            row.ticker.upper(): row
            for row in db.scalars(select(IBContract).where(IBContract.ticker.in_(sorted(tickers))))
        }
        existing = {
            (int(row.forward_outcome_id), row.what_to_show): row
            for row in db.scalars(
                select(WinnerMarketDataObligation).where(
                    WinnerMarketDataObligation.forward_outcome_id.in_(
                        [int(outcome.id) for outcome in outcomes]
                    )
                )
            )
        }
        created = updated = excluded = 0
        touched: list[WinnerMarketDataObligation] = []
        for outcome in outcomes:
            prediction = predictions.get(int(outcome.prediction_id))
            if (
                prediction is None
                or outcome.entry_model != "NEXT_OPEN"
                or int(outcome.horizon_sessions) != 5
                or outcome.status != "PENDING"
                or not outcome.is_current_revision
                or outcome.entry_session is None
                or not prediction_temporally_eligible(
                    prediction, decisions.get(int(outcome.prediction_id))
                )
                or prediction.ticker.upper() in excluded_tickers
            ):
                excluded += 1
                continue
            sessions = required_outcome_sessions(outcome.entry_session, outcome.horizon_sessions)
            contract = contracts.get(prediction.ticker.upper())
            for basis in SUPPORTED_BASES:
                key = (int(outcome.id), basis)
                obligation = existing.get(key)
                if obligation is None:
                    obligation = WinnerMarketDataObligation(
                        prediction_id=prediction.id,
                        forward_outcome_id=outcome.id,
                        ib_contract_id=getattr(contract, "id", None),
                        ticker_snapshot=prediction.ticker.upper(),
                        ib_conid_snapshot=getattr(contract, "ib_conid", None),
                        symbol_snapshot=getattr(contract, "symbol", None),
                        local_symbol_snapshot=getattr(contract, "local_symbol", None),
                        exchange_snapshot=getattr(contract, "exchange", None),
                        primary_exchange_snapshot=getattr(contract, "primary_exchange", None),
                        currency_snapshot=getattr(contract, "currency", None),
                        sec_type_snapshot=getattr(contract, "sec_type", None),
                        trading_class_snapshot=getattr(contract, "trading_class", None),
                        entry_session=sessions[0],
                        required_through_session=sessions[-1],
                        required_sessions_json=[value.isoformat() for value in sessions],
                        timeframe="1 day",
                        what_to_show=basis,
                        status="FETCH_REQUIRED",
                        first_missing_session=sessions[0],
                        last_missing_session=sessions[-1],
                        price_series_watermark=EMPTY_PRICE_WATERMARK,
                        last_checked_at=now,
                        metadata_json={"obligation_version": "winner-market-data-1.0"},
                    )
                    if isinstance(db, Session):
                        from app.services.canonical_evidence import (
                            CanonicalEvidenceSerializer as Canonical,
                        )
                        from app.services.winner_probability.outcome_authority import (
                            obligation_scope_body,
                        )

                        obligation.metadata_json = {
                            **obligation.metadata_json,
                            "native_obligation_scope": {
                                "contract": "winner-native-obligation-scope-v1",
                                "fingerprint": Canonical.fingerprint(
                                    obligation_scope_body(obligation)
                                ),
                                "capture_proof": prediction.lineage_json["native_capture_proof"],
                            },
                        }
                    db.add(obligation)
                    existing[key] = obligation
                    created += 1
                else:
                    updated += 1
                touched.append(obligation)
        db.flush()
        evaluated = self.evaluate(db, obligations=touched, now=now)
        return ObligationSyncResult(
            created=created,
            updated=updated,
            satisfied=evaluated.satisfied,
            fetch_required=evaluated.fetch_required,
            identity_blocked=evaluated.identity_blocked,
            excluded=excluded,
        )

    @core_writer_transaction
    def evaluate(
        self,
        db: Session,
        *,
        obligations: Sequence[WinnerMarketDataObligation] | None = None,
        tickers: Sequence[str] = (),
        now: datetime | None = None,
    ) -> ObligationSyncResult:
        if isinstance(db, Session) and (now is None or now.tzinfo is None):
            raise ValueError("MUTATION_WINNER_OBLIGATION_EXPLICIT_OPERATION_TIME_REQUIRED")
        now = now or datetime.now(UTC)
        if obligations is None:
            statement = select(WinnerMarketDataObligation)
            if tickers:
                statement = statement.where(
                    WinnerMarketDataObligation.ticker_snapshot.in_(
                        sorted({ticker.upper() for ticker in tickers})
                    )
                )
            obligations = list(db.scalars(statement))
            # A normal market-data fetch is not a migration of historical
            # Winner obligations. Only native obligations may enter this
            # current writer; retained legacy rows remain untouched.
            obligations = _native_obligations(db, obligations)
        if not obligations:
            return ObligationSyncResult()
        if isinstance(db, Session):
            from app.services.winner_probability.outcome_authority import obligation_authority

            outcomes = [db.get(WinnerForwardOutcome, row.forward_outcome_id) for row in obligations]
            if any(row is None for row in outcomes):
                raise ValueError("MUTATION_WINNER_OBLIGATION_OUTCOME_REQUIRED")
            obligation_authority(
                db, outcomes, now=now, writer="MarketDataObligationService.evaluate"
            )
            for obligation, outcome in zip(obligations, outcomes, strict=True):
                from app.services.winner_probability.outcome_authority import (
                    validate_obligation_scope,
                )

                validate_obligation_scope(obligation)
                prediction = db.get(WinnerPredictionSnapshot, outcome.prediction_id)
                required = required_outcome_sessions(
                    outcome.entry_session, outcome.horizon_sessions
                )
                if (
                    obligation.prediction_id != prediction.id
                    or obligation.ticker_snapshot != prediction.ticker.upper()
                    or obligation.entry_session != required[0]
                    or obligation.required_through_session != required[-1]
                    or obligation.required_sessions_json != [day.isoformat() for day in required]
                    or obligation.timeframe != "1 day"
                    or obligation.what_to_show not in SUPPORTED_BASES
                ):
                    raise ValueError("MUTATION_WINNER_OBLIGATION_NATIVE_DEPENDENCY_SCOPE_MISMATCH")
        contract_ids = {row.ib_contract_id for row in obligations if row.ib_contract_id is not None}
        contracts = {
            int(row.id): row
            for row in db.scalars(select(IBContract).where(IBContract.id.in_(contract_ids)))
        }
        ticker_set = {row.ticker_snapshot for row in obligations}
        min_date = min(row.entry_session for row in obligations)
        max_date = max(row.required_through_session for row in obligations)
        bars = list(
            db.scalars(
                select(PriceBar)
                .where(PriceBar.ticker.in_(sorted(ticker_set)))
                .where(PriceBar.timeframe == "1 day")
                .where(PriceBar.what_to_show.in_(SUPPORTED_BASES))
                .where(PriceBar.bar_date >= min_date)
                .where(PriceBar.bar_date <= max_date)
            )
        )
        bars_by_key: dict[tuple[str, str], list[PriceBar]] = defaultdict(list)
        for bar in bars:
            bars_by_key[(bar.ticker.upper(), bar.what_to_show)].append(bar)
        counts = defaultdict(int)
        for obligation in obligations:
            contract = contracts.get(int(obligation.ib_contract_id or 0))
            if not _identity_matches(obligation, contract):
                obligation.status = "IDENTITY_BLOCKED"
                obligation.failure_reason = "CANONICAL_IDENTITY_MISSING_OR_CHANGED"
                obligation.fulfilled_at = None
                obligation.last_checked_at = now
                obligation.updated_at = now
                counts["identity_blocked"] += 1
                continue
            sessions = tuple(
                date.fromisoformat(value) for value in obligation.required_sessions_json
            )
            relevant = [
                row
                for row in bars_by_key[(obligation.ticker_snapshot, obligation.what_to_show)]
                if row.bar_date in sessions
            ]
            if isinstance(db, Session):
                from app.services.market_clock_service import MarketClockService
                from app.services.winner_probability.outcome_authority import price_manifest

                price_manifest(
                    db,
                    relevant,
                    clock=MarketClockService().cutoff_for(
                        now, reason="WINNER_OBLIGATION_OPERATION"
                    ),
                )
            watermark = price_series_watermark(relevant)
            present = {row.bar_date for row in relevant}
            missing = tuple(session for session in sessions if session not in present)
            obligation.price_series_watermark = watermark
            obligation.last_evaluated_watermark = watermark
            obligation.last_checked_at = now
            obligation.updated_at = now
            if missing:
                obligation.status = "FETCH_REQUIRED"
                obligation.first_missing_session = missing[0]
                obligation.last_missing_session = missing[-1]
                obligation.fulfilled_at = None
                obligation.failure_reason = "REQUIRED_SESSION_MISSING"
                counts["fetch_required"] += 1
            else:
                obligation.status = "SATISFIED"
                obligation.first_missing_session = None
                obligation.last_missing_session = None
                obligation.fulfilled_at = obligation.fulfilled_at or now
                obligation.failure_reason = None
                counts["satisfied"] += 1
        db.flush()
        return ObligationSyncResult(
            updated=len(obligations),
            satisfied=counts["satisfied"],
            fetch_required=counts["fetch_required"],
            identity_blocked=counts["identity_blocked"],
        )

    def recovery_needs(self, db: Session) -> tuple[RecoveryNeed, ...]:
        rows = list(
            db.scalars(
                select(WinnerMarketDataObligation)
                .where(WinnerMarketDataObligation.status == "FETCH_REQUIRED")
                .order_by(
                    WinnerMarketDataObligation.ib_contract_id,
                    WinnerMarketDataObligation.what_to_show,
                    WinnerMarketDataObligation.forward_outcome_id,
                )
            )
        )
        if not rows:
            return ()
        min_date = min(row.entry_session for row in rows)
        max_date = max(row.required_through_session for row in rows)
        tickers = {row.ticker_snapshot for row in rows}
        bases = {row.what_to_show for row in rows}
        present_by_key: dict[tuple[str, str], set[date]] = defaultdict(set)
        for ticker, basis, bar_date in db.execute(
            select(PriceBar.ticker, PriceBar.what_to_show, PriceBar.bar_date)
            .where(PriceBar.ticker.in_(sorted(tickers)))
            .where(PriceBar.timeframe == "1 day")
            .where(PriceBar.what_to_show.in_(sorted(bases)))
            .where(PriceBar.bar_date >= min_date)
            .where(PriceBar.bar_date <= max_date)
        ):
            present_by_key[(str(ticker).upper(), str(basis))].add(bar_date)
        result: list[RecoveryNeed] = []
        for row in rows:
            if row.ib_contract_id is None or row.ib_conid_snapshot is None:
                continue
            required = tuple(date.fromisoformat(value) for value in row.required_sessions_json)
            present = present_by_key[(row.ticker_snapshot, row.what_to_show)]
            missing = tuple(session for session in required if session not in present)
            if missing:
                result.append(
                    RecoveryNeed(
                        outcome_id=int(row.forward_outcome_id),
                        prediction_id=int(row.prediction_id),
                        contract_id=int(row.ib_contract_id),
                        ib_conid=int(row.ib_conid_snapshot),
                        ticker=row.ticker_snapshot,
                        what_to_show=row.what_to_show,
                        missing_sessions=missing,
                    )
                )
        return tuple(result)

    @core_writer_transaction
    def record_fetch_results(
        self,
        db: Session,
        *,
        fetch_run: IBFetchRun,
        now: datetime | None = None,
    ) -> ObligationSyncResult:
        """Reevaluate touched series and retain provider-result distinctions."""
        if isinstance(db, Session):
            if now is None or now.tzinfo is None or fetch_run.id is None:
                raise ValueError("MUTATION_WINNER_OBLIGATION_FETCH_TIME_SOURCE_REQUIRED")
            from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical

            with db.no_autoflush:
                retained = (
                    db.execute(
                        select(*IBFetchRun.__table__.columns).where(IBFetchRun.id == fetch_run.id)
                    )
                    .mappings()
                    .one_or_none()
                )
            if retained is None or any(
                Canonical.dumps(value) != Canonical.dumps(getattr(fetch_run, key))
                for key, value in retained.items()
            ):
                raise ValueError("MUTATION_WINNER_OBLIGATION_RETAINED_FETCH_MISMATCH")
            for item in fetch_run.items:
                with db.no_autoflush:
                    stored = (
                        db.execute(
                            select(*item.__table__.columns).where(item.__table__.c.id == item.id)
                        )
                        .mappings()
                        .one_or_none()
                    )
                if (
                    stored is None
                    or stored["fetch_run_id"] != fetch_run.id
                    or any(
                        Canonical.dumps(value) != Canonical.dumps(getattr(item, key))
                        for key, value in stored.items()
                    )
                ):
                    raise ValueError("MUTATION_WINNER_OBLIGATION_FETCH_ITEM_MISMATCH")
        now = now or datetime.now(UTC)
        items = list(fetch_run.items or [])
        tickers = sorted({str(item.ticker).upper() for item in items})
        candidates = list(
            db.scalars(
                select(WinnerMarketDataObligation).where(
                    WinnerMarketDataObligation.ticker_snapshot.in_(tickers)
                )
            )
        )
        result = self.evaluate(db, obligations=_native_obligations(db, candidates), now=now)
        terminal_by_key = {
            (str(item.ticker).upper(), str(item.what_to_show)): item
            for item in items
            if item.status in {"SUCCESS", "FAILED"} and item.action != "SKIP"
        }
        if not terminal_by_key:
            return result
        obligations = list(
            db.scalars(
                select(WinnerMarketDataObligation)
                .where(WinnerMarketDataObligation.ticker_snapshot.in_(tickers))
                .where(WinnerMarketDataObligation.status == "FETCH_REQUIRED")
            )
        )
        obligations = _native_obligations(db, obligations)
        unavailable = failed = 0
        for obligation in obligations:
            item = terminal_by_key.get((obligation.ticker_snapshot, obligation.what_to_show))
            if item is None:
                continue
            if item.status == "FAILED":
                provider_result = (item.decision_metadata_json or {}).get("provider_result")
                if provider_result == "PROVIDER_NO_DATA":
                    obligation.status = "UNAVAILABLE"
                    obligation.failure_reason = "PROVIDER_RETURNED_NO_DATA"
                    unavailable += 1
                else:
                    obligation.status = "FAILED"
                    obligation.failure_reason = (
                        "PROVIDER_REQUEST_REJECTED"
                        if provider_result == "PROVIDER_REJECTED"
                        else "PROVIDER_REQUEST_FAILED"
                    )
                    failed += 1
            else:
                obligation.status = "UNAVAILABLE"
                obligation.failure_reason = "PROVIDER_RESPONSE_MISSING_REQUIRED_SESSIONS"
                unavailable += 1
            obligation.last_checked_at = now
            obligation.updated_at = now
        db.flush()
        return ObligationSyncResult(
            updated=result.updated,
            satisfied=result.satisfied,
            fetch_required=max(0, result.fetch_required - unavailable - failed),
            identity_blocked=result.identity_blocked,
            unavailable=unavailable,
            failed=failed,
        )


def global_daily_bar_lag(db: Session, *, latest_completed_session: date) -> GlobalDailyBarLag:
    latest = db.scalar(select(func.max(PriceBar.bar_date)).where(PriceBar.timeframe == "1 day"))
    lag = 0
    cursor = latest
    if cursor is None:
        lag = 1
    else:
        while cursor < latest_completed_session:
            cursor = next_us_trading_day(cursor)
            if cursor <= latest_completed_session:
                lag += 1
    return GlobalDailyBarLag(
        latest_completed_session=latest_completed_session,
        latest_local_session=latest,
        lag_sessions=lag,
        degraded=latest is None or latest < latest_completed_session,
    )


def _identity_matches(
    obligation: WinnerMarketDataObligation,
    contract: IBContract | None,
) -> bool:
    return bool(
        contract is not None
        and contract.resolution_status == "RESOLVED"
        and contract.ib_conid is not None
        and contract.ib_conid == obligation.ib_conid_snapshot
        and (contract.symbol or None) == obligation.symbol_snapshot
        and (contract.local_symbol or None) == obligation.local_symbol_snapshot
        and (contract.exchange or None) == obligation.exchange_snapshot
        and (contract.primary_exchange or None) == obligation.primary_exchange_snapshot
        and (contract.currency or None) == obligation.currency_snapshot
        and (contract.sec_type or None) == obligation.sec_type_snapshot
        and (contract.trading_class or None) == obligation.trading_class_snapshot
    )


def _contiguous_session_groups(sessions: Sequence[date]) -> tuple[tuple[date, ...], ...]:
    if not sessions:
        return ()
    groups: list[list[date]] = [[sessions[0]]]
    for session in sessions[1:]:
        if session == next_us_trading_day(groups[-1][-1]):
            groups[-1].append(session)
        else:
            groups.append([session])
    return tuple(tuple(group) for group in groups)
