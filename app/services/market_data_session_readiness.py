from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import (
    IBContract,
    IBFetchItem,
    InstrumentLifecycleRecord,
    MarketDataSessionDisposition,
)
from app.services.market_clock_service import MarketCalculationCutoff
from app.services.price_bar_repository import load_price_bar_rows_for_context


class InstrumentLifecycleState(StrEnum):
    ACTIVE = "ACTIVE"
    HALTED = "HALTED"
    INACTIVE = "INACTIVE"
    MERGED = "MERGED"
    DELISTED = "DELISTED"
    UNKNOWN = "UNKNOWN"


class MarketDataDisposition(StrEnum):
    READY = "READY"
    TRANSIENT_FAILURE = "TRANSIENT_FAILURE"
    REQUIRED_DATA_UNAVAILABLE = "REQUIRED_DATA_UNAVAILABLE"
    TERMINAL_INACTIVE = "TERMINAL_INACTIVE"
    UNKNOWN = "UNKNOWN"


TERMINAL_LIFECYCLE_STATES = {
    InstrumentLifecycleState.INACTIVE,
    InstrumentLifecycleState.MERGED,
    InstrumentLifecycleState.DELISTED,
}
CONTRACT_IDENTITY_FAILURES = {
    "CONTRACT_NOT_FOUND",
    "IB_CONTRACT_RESOLUTION_FAILED",
    "ACQUISITION_PLAN_CONTRACT_SUPERSEDED",
}


@dataclass(frozen=True)
class SessionReadinessResult:
    rows: tuple[MarketDataSessionDisposition, ...]

    @property
    def technical_tickers(self) -> tuple[str, ...]:
        return tuple(row.ticker for row in self.rows if row.technical_eligible)

    @property
    def downstream_tickers(self) -> tuple[str, ...]:
        return tuple(row.ticker for row in self.rows if row.downstream_eligible)

    @property
    def accepted_inactive_tickers(self) -> tuple[str, ...]:
        return tuple(
            row.ticker
            for row in self.rows
            if row.disposition == MarketDataDisposition.TERMINAL_INACTIVE.value
        )

    @property
    def blockers(self) -> tuple[MarketDataSessionDisposition, ...]:
        return tuple(
            row
            for row in self.rows
            if row.disposition
            not in {
                MarketDataDisposition.READY.value,
                MarketDataDisposition.TERMINAL_INACTIVE.value,
            }
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "technical_tickers": list(self.technical_tickers),
            "downstream_tickers": list(self.downstream_tickers),
            "accepted_inactive_tickers": list(self.accepted_inactive_tickers),
            "blockers": [row.ticker for row in self.blockers],
            "tickers": {
                row.ticker: {
                    "disposition_id": row.id,
                    "revision": row.revision,
                    "expected_session": row.expected_session.isoformat(),
                    "latest_bar_session": (
                        row.latest_bar_session.isoformat() if row.latest_bar_session else None
                    ),
                    "disposition": row.disposition,
                    "lifecycle_state": row.lifecycle_state,
                    "technical_eligible": row.technical_eligible,
                    "downstream_eligible": row.downstream_eligible,
                    "reason_code": row.reason_code,
                }
                for row in self.rows
            },
        }


def evaluate_and_persist_session_readiness(
    db: Session,
    *,
    pipeline_run_id: int,
    upload_run_id: int,
    tickers: list[str] | tuple[str, ...],
    market_cutoff: MarketCalculationCutoff,
    fetch_run_id: int | None,
) -> SessionReadinessResult:
    """Freeze one explicit disposition per requested ticker and expected session."""

    if market_cutoff.context_id is None:
        raise ValueError("MARKET_DATA_READINESS_CONTEXT_REQUIRED")
    symbols = tuple(sorted({ticker.strip().upper() for ticker in tickers if ticker.strip()}))
    bars = load_price_bar_rows_for_context(
        db,
        symbols,
        what_to_show=("TRADES", "ADJUSTED_LAST"),
        timeframes=("1 day",),
        max_session=market_cutoff.latest_completed_session,
        as_of=market_cutoff.cutoff_at,
        calculation_context_id=market_cutoff.context_id,
        session_count=1,
        one_source_per_session=True,
        source_priority=("TRADES", "ADJUSTED_LAST"),
    )
    latest_by_ticker: dict[str, date] = {}
    for row in bars:
        ticker = row.ticker.upper()
        latest_by_ticker[ticker] = max(row.bar_date, latest_by_ticker.get(ticker, row.bar_date))
    lifecycle_rows: dict[str, InstrumentLifecycleRecord] = {}
    lifecycle_statement = (
        select(InstrumentLifecycleRecord)
        .where(
            InstrumentLifecycleRecord.ticker.in_(symbols),
            InstrumentLifecycleRecord.observed_at <= market_cutoff.cutoff_at,
        )
        .order_by(
            InstrumentLifecycleRecord.ticker,
            InstrumentLifecycleRecord.observed_at.desc(),
            InstrumentLifecycleRecord.revision.desc(),
        )
    )
    for row in db.scalars(lifecycle_statement):
        lifecycle_rows.setdefault(row.ticker.upper(), row)
    contract_statuses = {
        row.ticker.upper(): row.resolution_status
        for row in db.scalars(select(IBContract).where(IBContract.ticker.in_(symbols)))
    }
    fetch_items: dict[str, list[IBFetchItem]] = {ticker: [] for ticker in symbols}
    if fetch_run_id is not None:
        for item in db.scalars(
            select(IBFetchItem).where(
                IBFetchItem.fetch_run_id == fetch_run_id,
                IBFetchItem.ticker.in_(symbols),
            )
        ):
            fetch_items.setdefault(item.ticker.upper(), []).append(item)

    persisted: list[MarketDataSessionDisposition] = []
    for ticker in symbols:
        lifecycle = lifecycle_rows.get(ticker)
        values = classify_session_readiness(
            ticker=ticker,
            expected_session=market_cutoff.latest_completed_session,
            latest_bar_session=latest_by_ticker.get(ticker),
            lifecycle=lifecycle,
            fetch_items=tuple(fetch_items.get(ticker, ())),
            contract_resolution_status=contract_statuses.get(ticker),
        )
        current = db.scalar(
            select(MarketDataSessionDisposition).where(
                MarketDataSessionDisposition.pipeline_run_id == pipeline_run_id,
                MarketDataSessionDisposition.ticker == ticker,
                MarketDataSessionDisposition.is_current_revision.is_(True),
            )
        )
        material = {
            "expected_session": market_cutoff.latest_completed_session,
            "latest_bar_session": latest_by_ticker.get(ticker),
            **values,
        }
        if (
            current is not None
            and current.fetch_run_id == fetch_run_id
            and all(getattr(current, key) == value for key, value in material.items())
        ):
            persisted.append(current)
            continue
        if current is not None:
            current.is_current_revision = False
        row = MarketDataSessionDisposition(
            pipeline_run_id=pipeline_run_id,
            upload_run_id=upload_run_id,
            market_calculation_context_id=market_cutoff.context_id,
            fetch_run_id=fetch_run_id,
            ticker=ticker,
            revision=(current.revision + 1 if current is not None else 1),
            supersedes_id=(current.id if current is not None else None),
            is_current_revision=True,
            **material,
        )
        db.add(row)
        db.flush()
        persisted.append(row)
    return SessionReadinessResult(rows=tuple(persisted))


def load_current_session_readiness(
    db: Session,
    *,
    pipeline_run_id: int,
) -> SessionReadinessResult | None:
    rows = tuple(
        db.scalars(
            select(MarketDataSessionDisposition)
            .where(
                MarketDataSessionDisposition.pipeline_run_id == pipeline_run_id,
                MarketDataSessionDisposition.is_current_revision.is_(True),
            )
            .order_by(MarketDataSessionDisposition.ticker)
        )
    )
    return SessionReadinessResult(rows=rows) if rows else None


def classify_session_readiness(
    *,
    ticker: str,
    expected_session: date,
    latest_bar_session: date | None,
    lifecycle: InstrumentLifecycleRecord | None,
    fetch_items: tuple[IBFetchItem, ...] = (),
    contract_resolution_status: str | None = None,
) -> dict[str, Any]:
    lifecycle_state = InstrumentLifecycleState(
        lifecycle.lifecycle_state if lifecycle is not None else InstrumentLifecycleState.UNKNOWN
    )
    terminal_effective = bool(
        lifecycle is not None
        and lifecycle_state in TERMINAL_LIFECYCLE_STATES
        and (
            (lifecycle.effective_date is not None and lifecycle.effective_date <= expected_session)
            or (
                lifecycle.last_trading_date is not None
                and lifecycle.last_trading_date < expected_session
            )
            or (
                lifecycle.contract_valid_to is not None
                and lifecycle.contract_valid_to < expected_session
            )
        )
    )
    failures = [item for item in fetch_items if item.status == "FAILED"]
    failure_evidence = [
        {
            "fetch_item_id": item.id,
            "feed": item.what_to_show,
            "status": item.status,
            "error": item.error_message,
            "provider_error_code": (item.decision_metadata_json or {}).get("provider_error_code"),
            "provider_error_category": (item.decision_metadata_json or {}).get(
                "provider_error_category"
            ),
            "retryable": (item.decision_metadata_json or {}).get("retryable"),
        }
        for item in failures
    ]
    evidence = {
        "ticker": ticker,
        "expected_session": expected_session.isoformat(),
        "latest_bar_session": latest_bar_session.isoformat() if latest_bar_session else None,
        "fetch_failures": failure_evidence,
        "lifecycle_record_id": lifecycle.id if lifecycle is not None else None,
        "lifecycle_revision": lifecycle.revision if lifecycle is not None else None,
        "lifecycle_effective_date": (
            lifecycle.effective_date.isoformat()
            if lifecycle is not None and lifecycle.effective_date
            else None
        ),
        "last_trading_date": (
            lifecycle.last_trading_date.isoformat()
            if lifecycle is not None and lifecycle.last_trading_date
            else None
        ),
        "successor_ticker": lifecycle.successor_ticker if lifecycle is not None else None,
        "contract_resolution_status": contract_resolution_status,
    }
    if terminal_effective:
        return _values(
            MarketDataDisposition.TERMINAL_INACTIVE,
            lifecycle_state,
            False,
            False,
            "TERMINAL_INSTRUMENT_EXCLUDED",
            "Explicit lifecycle evidence proves the instrument inactive for the frozen session.",
            evidence,
        )
    if latest_bar_session == expected_session:
        return _values(
            MarketDataDisposition.READY,
            lifecycle_state,
            True,
            True,
            "EXPECTED_SESSION_AVAILABLE",
            "A PIT-visible daily bar exists for the frozen expected session.",
            evidence,
        )
    if any((item.decision_metadata_json or {}).get("retryable") is True for item in failures):
        return _values(
            MarketDataDisposition.TRANSIENT_FAILURE,
            lifecycle_state,
            False,
            False,
            "TRANSIENT_MARKET_DATA_FAILURE",
            "Required market data is stale after a retryable provider failure.",
            evidence,
        )
    categories = {
        str(
            (item.decision_metadata_json or {}).get("provider_error_category")
            or (item.decision_metadata_json or {}).get("failure_classification")
            or ""
        )
        for item in failures
    }
    if categories.intersection(CONTRACT_IDENTITY_FAILURES) or contract_resolution_status in {
        "FAILED",
        "AMBIGUOUS",
    }:
        return _values(
            MarketDataDisposition.UNKNOWN,
            lifecycle_state,
            False,
            False,
            "INSTRUMENT_STATE_UNRESOLVED",
            "The contract identity failed and no terminal lifecycle evidence permits exclusion.",
            evidence,
        )
    return _values(
        MarketDataDisposition.REQUIRED_DATA_UNAVAILABLE,
        lifecycle_state,
        False,
        False,
        "EXPECTED_SESSION_UNAVAILABLE",
        "No PIT-visible daily bar exists for the frozen expected session.",
        evidence,
    )


def _values(
    disposition: MarketDataDisposition,
    lifecycle_state: InstrumentLifecycleState,
    technical_eligible: bool,
    downstream_eligible: bool,
    reason_code: str,
    reason_message: str,
    evidence_json: dict[str, Any],
) -> dict[str, Any]:
    return {
        "disposition": disposition.value,
        "lifecycle_state": lifecycle_state.value,
        "technical_eligible": technical_eligible,
        "downstream_eligible": downstream_eligible,
        "reason_code": reason_code,
        "reason_message": reason_message,
        "evidence_json": evidence_json,
    }
