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
    PipelineRun,
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
                    "reason_message": getattr(row, "reason_message", None),
                    "history_gap_count": (getattr(row, "evidence_json", None) or {}).get(
                        "history_gap_count", 0
                    ),
                    "missing_sessions": (getattr(row, "evidence_json", None) or {}).get(
                        "missing_required_sessions", []
                    ),
                    "provider_results": (getattr(row, "evidence_json", None) or {}).get(
                        "fetch_results", []
                    ),
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
    from app.services.market_data_availability_policy import pipeline_availability_policy

    pipeline = db.get(PipelineRun, pipeline_run_id)
    availability_policy = pipeline_availability_policy(pipeline)
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

    from app.services.ib_bar_admission_service import load_gap_admissions

    gap_receipts: dict[str, list] = {}
    for receipt in load_gap_admissions(db, pipeline_run_id=pipeline_run_id):
        gap_receipts.setdefault(receipt.ticker.upper(), []).append(receipt)
    visible_history: dict[tuple[str, str], set[date]] = {}
    if gap_receipts:
        for bar in load_price_bar_rows_for_context(
            db, tuple(gap_receipts), what_to_show=("TRADES", "ADJUSTED_LAST"),
            timeframes=("1 day",), max_session=market_cutoff.latest_completed_session,
            as_of=market_cutoff.cutoff_at, calculation_context_id=market_cutoff.context_id,
        ):
            visible_history.setdefault((bar.ticker.upper(), bar.what_to_show), set()).add(
                bar.bar_date
            )

    persisted: list[MarketDataSessionDisposition] = []
    for ticker in symbols:
        lifecycle = lifecycle_rows.get(ticker)
        empty = []
        if availability_policy.allow_history_gaps:
            empty = [item for item in fetch_items.get(ticker, ())
                     if (item.decision_metadata_json or {}).get("provider_result")
                     == "PROVIDER_NO_DATA"
                     and _pinned_empty_response(
                         pipeline, item, market_cutoff.latest_completed_session
                     )
                     and _owned_fetch_run(db, pipeline, item.fetch_run_id)]
        values = classify_session_readiness(
            ticker=ticker,
            expected_session=market_cutoff.latest_completed_session,
            latest_bar_session=latest_by_ticker.get(ticker),
            lifecycle=lifecycle,
            fetch_items=tuple(fetch_items.get(ticker, ())),
            contract_resolution_status=contract_statuses.get(ticker),
            enforce_response_integrity=availability_policy.allow_history_gaps,
            verified_empty_item_ids=tuple(item.id for item in empty),
        )
        if availability_policy.allow_history_gaps and not values["evidence_json"].get(
            "integrity_failure"
        ):
            if empty:
                values["evidence_json"].update(
                    validated_empty_response=True,
                    empty_response_item_ids=[item.id for item in empty],
                )
        receipts = gap_receipts.get(ticker, ())
        missing_sessions = sorted({
            day.isoformat() for row in receipts for day in row.missing_sessions
            if day not in visible_history.get((ticker, row.what_to_show), set())
        })
        if missing_sessions:
            values["evidence_json"].update(
                history_gap_count=len(missing_sessions), history_complete=False,
                missing_required_sessions=missing_sessions,
                admission_receipt_hashes=[row.receipt_hash for row in receipts],
                validated_coverage_gap=not values["evidence_json"].get("integrity_failure", False),
            )
            if values["disposition"] == MarketDataDisposition.READY:
                values["reason_code"] = "EXPECTED_SESSION_AVAILABLE_HISTORY_GAPS"
                values["reason_message"] = (
                    "The expected daily session is admitted; historical session gaps remain."
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
    receipt_rows = [row for row in rows if (row.evidence_json or {}).get("validated_coverage_gap")]
    if receipt_rows:
        from app.services.ib_bar_admission_service import load_gap_admissions

        receipts = load_gap_admissions(db, pipeline_run_id=pipeline_run_id)
        hashes = {receipt.receipt_hash for receipt in receipts}
        for row in receipt_rows:
            claimed = (row.evidence_json or {}).get("admission_receipt_hashes") or ()
            if not claimed or not set(claimed) <= hashes:
                raise ValueError("MARKET_DATA_ADMISSION_RECEIPT_REQUIRED")
            if any(receipt.ticker != row.ticker for receipt in receipts
                   if receipt.receipt_hash in claimed):
                raise ValueError("MARKET_DATA_ADMISSION_RECEIPT_OWNER_MISMATCH")
    empty_rows = [row for row in rows if (row.evidence_json or {}).get("validated_empty_response")]
    if empty_rows:
        pipeline = db.get(PipelineRun, pipeline_run_id)
        for row in empty_rows:
            ids = (row.evidence_json or {}).get("empty_response_item_ids") or ()
            if not ids:
                raise ValueError("MARKET_DATA_EMPTY_RESPONSE_PROOF_REQUIRED")
            for item_id in ids:
                item = db.get(IBFetchItem, item_id)
                if (item is None or item.ticker != row.ticker
                        or not _owned_fetch_run(db, pipeline, item.fetch_run_id)
                        or not _pinned_empty_response(pipeline, item, row.expected_session)):
                    raise ValueError("MARKET_DATA_EMPTY_RESPONSE_PROOF_REQUIRED")
    return SessionReadinessResult(rows=rows) if rows else None


def classify_session_readiness(
    *,
    ticker: str,
    expected_session: date,
    latest_bar_session: date | None,
    lifecycle: InstrumentLifecycleRecord | None,
    fetch_items: tuple[IBFetchItem, ...] = (),
    contract_resolution_status: str | None = None,
    enforce_response_integrity: bool = False,
    verified_empty_item_ids: tuple[int, ...] = (),
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
            "provider_result": (item.decision_metadata_json or {}).get("provider_result"),
            "retryable": (item.decision_metadata_json or {}).get("retryable"),
            "data_role": (item.decision_metadata_json or {}).get("data_role"),
            "dependency_roles": (item.decision_metadata_json or {}).get("dependency_roles"),
            "failure_classification": (item.decision_metadata_json or {}).get(
                "failure_classification"
            ),
            "boundary_status": (item.decision_metadata_json or {}).get("boundary_status"),
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
        "fetch_results": [
            {"feed": item.what_to_show, "status": item.status,
             **{key: (item.decision_metadata_json or {}).get(key) for key in (
                 "provider_result", "provider_error_category", "failure_classification",
                 "actual_start_date", "actual_end_date", "admitted_start_date",
                 "admitted_end_date", "received_bar_count", "admitted_bar_count",
             )}}
            for item in fetch_items
        ],
    }
    integrity_failures = [failure for failure in failure_evidence if (
        failure["boundary_status"] == "FAIL"
        or failure["failure_classification"] is not None
        or failure["provider_error_category"] not in {None, "HISTORICAL_DATA_UNAVAILABLE"}
        or failure["provider_result"] is None
        or (failure["provider_result"] == "PROVIDER_NO_DATA"
            and failure["fetch_item_id"] not in verified_empty_item_ids)
        or "BENCHMARK" in (failure["dependency_roles"] or ())
        or "BENCHMARK" in str(failure["data_role"] or "")
    )]
    if enforce_response_integrity and (
        integrity_failures or contract_resolution_status in {"FAILED", "AMBIGUOUS"}
    ):
        evidence["integrity_failure"] = True
        return _values(
            MarketDataDisposition.UNKNOWN, lifecycle_state, False, False,
            "MARKET_DATA_RESPONSE_INTEGRITY_BLOCKED",
            "Historical response or contract integrity failed; cached bars cannot bypass it.",
            evidence,
        )
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
    if "PROVIDER_REJECTED" in categories:
        return _values(
            MarketDataDisposition.REQUIRED_DATA_UNAVAILABLE,
            lifecycle_state,
            False,
            False,
            "MARKET_DATA_PROVIDER_REJECTED",
            "IB rejected the historical request; resolve data availability before retrying.",
            evidence,
        )
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


def _owned_fetch_run(db, pipeline, fetch_run_id) -> bool:
    from app.models.tables import IBFetchRun, RefreshCycleRecord, WorkScopeRecord

    run = db.get(IBFetchRun, fetch_run_id)
    if run is None or run.run_id != pipeline.upload_run_id:
        return False
    scope = db.get(WorkScopeRecord, run.scope_id) if run.scope_id else None
    refresh = db.get(RefreshCycleRecord, run.refresh_cycle_id) if run.refresh_cycle_id else None
    return bool(scope and refresh and scope.scope_kind == "ib-price-acquisition"
                and scope.parent_scope_id == pipeline.scope_id
                and scope.acquisition_plan_id == run.acquisition_plan_id
                and refresh.scope_id == run.scope_id)


def _pinned_empty_response(pipeline, item, expected_session) -> bool:
    from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
    from app.services.ib_fetch_plan_service import fetch_plan_from_dict
    from app.services.ib_historical_request_scope import (
        DAILY_BAR_ADMISSION_V4,
        build_historical_request_scope,
    )

    meta = item.decision_metadata_json or {}
    if (item.status != "FAILED" or item.attempt_count < 1
            or item.started_at is None or item.completed_at is None
            or item.completed_at < item.started_at or item.fetched != 0
            or meta.get("provider_result") != "PROVIDER_NO_DATA"
            or meta.get("boundary_status") != "PASS"
            or meta.get("received_bar_count") != 0
            or meta.get("response_admission_policy") != DAILY_BAR_ADMISSION_V4):
        return False
    try:
        plan = fetch_plan_from_dict(pipeline.result_json["ib_fetch_plan"])
        candidates = [row for row in plan.items
                      if row.ticker == item.ticker and row.what_to_show == item.what_to_show]
        if len(candidates) != 1 or plan.run_id != pipeline.upload_run_id:
            return False
        pinned = candidates[0]
        duration = pinned.duration or pinned.post_resolution_duration
        if (pinned.response_admission_policy != DAILY_BAR_ADMISSION_V4
                or item.duration != duration or item.bar_size != pinned.bar_size):
            return False
        identity = meta.get("runtime_contract_identity") or {}
        if (identity.get("symbol") != item.ticker or not identity.get("conId")
                or identity.get("secType") != "STK" or identity.get("currency") != "USD"):
            return False
        if pinned.contract_identity is not None and (
            identity != pinned.contract_identity
            or Canonical.fingerprint(identity) != pinned.contract_fingerprint
        ):
            return False
        scope = build_historical_request_scope(
            required_start_date=pinned.missing_start_date,
            required_end_date=pinned.missing_end_date, duration=duration,
            bar_size=pinned.bar_size, what_to_show=pinned.what_to_show,
            reviewed_end_date=pinned.request_end_date,
            response_admission_policy=pinned.response_admission_policy,
        )
        return (scope.reviewed_end_date == expected_session
                and meta.get("reviewed_end_date") == expected_session.isoformat()
                and meta.get("reviewed_start_date") == scope.reviewed_start_date.isoformat()
                and meta.get("request_end_datetime") == scope.end_datetime
                and meta.get("request_end_mode") == scope.end_mode.value)
    except (KeyError, TypeError, ValueError):
        return False


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
