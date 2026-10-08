from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from app.database_safety import DatabaseSafetyContext, require_database_safety_context
from app.db import SessionLocal
from app.services.alembic_heads import schema_is_at_head
from app.services.instrument_lifecycle_service import record_instrument_lifecycle
from app.services.lifecycle_safety import verify_authoritative_connection
from app.services.market_data_session_readiness import InstrumentLifecycleState
from app.settings import get_settings


@dataclass(frozen=True)
class LifecycleOperatorCommand:
    ticker: str
    lifecycle_state: InstrumentLifecycleState
    actor: str
    source: str
    source_reference: str
    reason: str
    evidence_note: str
    confirm_ticker: str
    effective_date: date | None = None
    last_trading_date: date | None = None
    successor_ticker: str | None = None
    contract_valid_from: date | None = None
    contract_valid_to: date | None = None


def record_operator_lifecycle(db, command: LifecycleOperatorCommand):
    ticker = command.ticker.strip().upper()
    if command.confirm_ticker.strip().upper() != ticker:
        raise ValueError("INSTRUMENT_LIFECYCLE_CONFIRMATION_MISMATCH")
    if not all(
        value.strip()
        for value in (
            command.actor,
            command.source,
            command.source_reference,
            command.reason,
            command.evidence_note,
        )
    ):
        raise ValueError("INSTRUMENT_LIFECYCLE_OPERATOR_EVIDENCE_INCOMPLETE")
    evidence = {
        "evidence_type": "OPERATOR_LIFECYCLE_ASSERTION",
        "actor": command.actor.strip(),
        "source": command.source.strip(),
        "source_reference": command.source_reference.strip(),
        "evidence_note": command.evidence_note.strip(),
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    return record_instrument_lifecycle(
        db,
        ticker=ticker,
        lifecycle_state=command.lifecycle_state,
        evidence_json=evidence,
        effective_date=command.effective_date,
        last_trading_date=command.last_trading_date,
        reason=command.reason.strip(),
        successor_ticker=command.successor_ticker,
        contract_valid_from=command.contract_valid_from,
        contract_valid_to=command.contract_valid_to,
    )


def _optional_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Append authoritative instrument lifecycle evidence."
    )
    parser.add_argument("--ticker", required=True)
    parser.add_argument(
        "--state",
        required=True,
        choices=[state.value for state in InstrumentLifecycleState],
    )
    parser.add_argument("--actor", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--source-reference", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--evidence-note", required=True)
    parser.add_argument("--confirm-ticker", required=True)
    parser.add_argument("--effective-date")
    parser.add_argument("--last-trading-date")
    parser.add_argument("--successor-ticker")
    parser.add_argument("--contract-valid-from")
    parser.add_argument("--contract-valid-to")
    parser.add_argument("--commit", action="store_true", help="Required to persist the revision.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.commit:
        raise SystemExit("Refusing to write without explicit --commit")
    if require_database_safety_context() is not DatabaseSafetyContext.AUTHORITATIVE_LOCAL:
        raise SystemExit("Operator lifecycle writes require AUTHORITATIVE_LOCAL")
    command = LifecycleOperatorCommand(
        ticker=args.ticker,
        lifecycle_state=InstrumentLifecycleState(args.state),
        actor=args.actor,
        source=args.source,
        source_reference=args.source_reference,
        reason=args.reason,
        evidence_note=args.evidence_note,
        confirm_ticker=args.confirm_ticker,
        effective_date=_optional_date(args.effective_date),
        last_trading_date=_optional_date(args.last_trading_date),
        successor_ticker=args.successor_ticker,
        contract_valid_from=_optional_date(args.contract_valid_from),
        contract_valid_to=_optional_date(args.contract_valid_to),
    )
    settings = get_settings()
    with SessionLocal() as db:
        provenance = verify_authoritative_connection(db.connection(), settings)
        if not provenance.get("verified"):
            raise SystemExit("Authoritative PostgreSQL provenance verification failed")
        if not schema_is_at_head(db.connection(), Path(__file__).resolve().parents[1]):
            raise SystemExit("Database schema is not at repository Alembic head")
        row = record_operator_lifecycle(db, command)
        db.commit()
        print(
            json.dumps(
                {
                    "id": row.id,
                    "ticker": row.ticker,
                    "lifecycle_state": row.lifecycle_state,
                    "revision": row.revision,
                    "effective_date": row.effective_date,
                    "last_trading_date": row.last_trading_date,
                    "successor_ticker": row.successor_ticker,
                },
                default=str,
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
