from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import IBContract, InstrumentLifecycleRecord
from app.services.canonical_evidence import CanonicalEvidenceSerializer
from app.services.domain_mutation import MutationDomain, MutationSemanticMode
from app.services.market_data_session_readiness import (
    TERMINAL_LIFECYCLE_STATES,
    InstrumentLifecycleState,
)
from app.services.source_mutation_authority import source_mutation_writer


@source_mutation_writer(
    MutationDomain.IBMI_SOURCE, "request_scope", mode=MutationSemanticMode.MAINTENANCE
)
def record_instrument_lifecycle(
    db: Session,
    *,
    ticker: str,
    lifecycle_state: InstrumentLifecycleState | str,
    evidence_json: dict[str, Any],
    effective_date: date | None = None,
    last_trading_date: date | None = None,
    reason: str | None = None,
    successor_ticker: str | None = None,
    contract_valid_from: date | None = None,
    contract_valid_to: date | None = None,
) -> InstrumentLifecycleRecord:
    """Append a sourced lifecycle revision and revoke terminal cached identity.

    ``successor_ticker`` is evidence only. It is never used to rewrite a fetch
    plan or substitute one security for another.
    """

    symbol = ticker.strip().upper()
    state = InstrumentLifecycleState(str(lifecycle_state).upper())
    if not symbol:
        raise ValueError("INSTRUMENT_LIFECYCLE_TICKER_REQUIRED")
    if not evidence_json:
        raise ValueError("INSTRUMENT_LIFECYCLE_EVIDENCE_REQUIRED")
    if successor_ticker and successor_ticker.strip().upper() == symbol:
        raise ValueError("INSTRUMENT_LIFECYCLE_SUCCESSOR_MUST_DIFFER")
    if state in TERMINAL_LIFECYCLE_STATES and not any(
        (effective_date, last_trading_date, contract_valid_to)
    ):
        raise ValueError("TERMINAL_INSTRUMENT_EFFECTIVE_BOUNDARY_REQUIRED")
    if (
        effective_date is not None
        and last_trading_date is not None
        and last_trading_date > effective_date
    ):
        raise ValueError("INSTRUMENT_LIFECYCLE_DATE_ORDER_INVALID")

    current = db.scalar(
        select(InstrumentLifecycleRecord)
        .where(
            InstrumentLifecycleRecord.ticker == symbol,
            InstrumentLifecycleRecord.is_current_revision.is_(True),
        )
        .with_for_update()
    )
    payload = CanonicalEvidenceSerializer.canonicalize(
        {
            "lifecycle_state": state.value,
            "effective_date": effective_date,
            "last_trading_date": last_trading_date,
            "reason": reason,
            "successor_ticker": successor_ticker.strip().upper() if successor_ticker else None,
            "contract_valid_from": contract_valid_from,
            "contract_valid_to": contract_valid_to,
            "evidence_json": evidence_json,
        }
    )
    if current is not None and _record_payload(current) == payload:
        return current
    if current is not None:
        current.is_current_revision = False
    row = InstrumentLifecycleRecord(
        ticker=symbol,
        lifecycle_state=state.value,
        effective_date=effective_date,
        last_trading_date=last_trading_date,
        reason=reason,
        successor_ticker=successor_ticker.strip().upper() if successor_ticker else None,
        contract_valid_from=contract_valid_from,
        contract_valid_to=contract_valid_to,
        evidence_json=CanonicalEvidenceSerializer.canonicalize(evidence_json),
        revision=(current.revision + 1 if current is not None else 1),
        is_current_revision=True,
        supersedes_id=(current.id if current is not None else None),
    )
    db.add(row)

    if state in TERMINAL_LIFECYCLE_STATES:
        contract = db.scalar(
            select(IBContract).where(IBContract.ticker == symbol).with_for_update()
        )
        if contract is not None:
            _clear_cached_identity(contract)
            contract.resolution_status = "FAILED"
            contract.error_message = (
                f"Explicit lifecycle state {state.value}; contract is not authoritative."
            )
    db.flush()
    return row


def _record_payload(row: InstrumentLifecycleRecord) -> dict[str, Any]:
    return CanonicalEvidenceSerializer.canonicalize(
        {
            "lifecycle_state": row.lifecycle_state,
            "effective_date": row.effective_date,
            "last_trading_date": row.last_trading_date,
            "reason": row.reason,
            "successor_ticker": row.successor_ticker,
            "contract_valid_from": row.contract_valid_from,
            "contract_valid_to": row.contract_valid_to,
            "evidence_json": row.evidence_json,
        }
    )


def _clear_cached_identity(row: IBContract) -> None:
    row.ib_conid = None
    row.symbol = None
    row.exchange = None
    row.primary_exchange = None
    row.currency = None
    row.sec_type = None
    row.local_symbol = None
    row.trading_class = None
