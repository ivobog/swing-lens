from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import IBContract
from app.services.ib_api import IB, Contract, Stock


@dataclass(frozen=True)
class ContractResolution:
    ticker: str
    status: str
    contract: Contract | None
    cache_row: IBContract
    error_message: str | None = None


IBQualify = Callable[[Contract], list[Contract]]


def cached_contract_to_ib(row: IBContract) -> Contract | None:
    if row.resolution_status != "RESOLVED" or not row.ib_conid:
        return None

    return Contract(
        conId=int(row.ib_conid),
        symbol=row.symbol or row.ticker,
        secType=row.sec_type or "STK",
        exchange=row.exchange or "SMART",
        primaryExchange=row.primary_exchange or "",
        currency=row.currency or "USD",
        localSymbol=row.local_symbol or "",
        tradingClass=row.trading_class or "",
    )


def resolve_us_stock_contract(
    db: Session,
    ticker: str,
    ib: IB,
    force_refresh: bool = False,
) -> ContractResolution:
    normalized_ticker = ticker.strip().upper()
    existing = db.scalar(select(IBContract).where(IBContract.ticker == normalized_ticker))

    if existing and not force_refresh and existing.resolution_status == "RESOLVED":
        return ContractResolution(
            ticker=normalized_ticker,
            status="RESOLVED",
            contract=cached_contract_to_ib(existing),
            cache_row=existing,
        )

    row = existing or IBContract(ticker=normalized_ticker, resolution_status="PENDING")
    if not existing:
        db.add(row)

    try:
        requested = Stock(symbol=normalized_ticker, exchange="SMART", currency="USD")
        qualified = ib.qualifyContracts(requested)
        if not qualified:
            return _mark_failed(db, row, "IB returned no matching US stock contract.")
        if len(qualified) > 1:
            return _mark_ambiguous(db, row, qualified)

        contract = qualified[0]
        row.ib_conid = contract.conId
        row.symbol = contract.symbol
        row.exchange = contract.exchange
        row.primary_exchange = contract.primaryExchange
        row.currency = contract.currency
        row.sec_type = contract.secType
        row.local_symbol = contract.localSymbol
        row.trading_class = contract.tradingClass
        row.resolution_status = "RESOLVED"
        row.error_message = None
        row.last_resolved_at = datetime.now(UTC)
        db.flush()
        return ContractResolution(
            ticker=normalized_ticker,
            status="RESOLVED",
            contract=contract,
            cache_row=row,
        )
    except Exception as exc:
        return _mark_failed(db, row, str(exc))


def _mark_failed(db: Session, row: IBContract, message: str) -> ContractResolution:
    _clear_contract_identity(row)
    row.resolution_status = "FAILED"
    row.error_message = message
    row.last_resolved_at = datetime.now(UTC)
    db.flush()
    return ContractResolution(
        ticker=row.ticker,
        status="FAILED",
        contract=None,
        cache_row=row,
        error_message=message,
    )


def _mark_ambiguous(
    db: Session,
    row: IBContract,
    candidates: list[Contract],
) -> ContractResolution:
    _clear_contract_identity(row)
    message = (
        "IB returned multiple qualified contracts for this ticker; "
        f"manual instrument selection is required. Candidates: {_candidate_summary(candidates)}"
    )
    row.resolution_status = "AMBIGUOUS"
    row.error_message = message
    row.last_resolved_at = datetime.now(UTC)
    db.flush()
    return ContractResolution(
        ticker=row.ticker,
        status="AMBIGUOUS",
        contract=None,
        cache_row=row,
        error_message=message,
    )


def _clear_contract_identity(row: IBContract) -> None:
    row.ib_conid = None
    row.symbol = None
    row.exchange = None
    row.primary_exchange = None
    row.currency = None
    row.sec_type = None
    row.local_symbol = None
    row.trading_class = None


def _candidate_summary(candidates: list[Contract]) -> str:
    parts = []
    for candidate in candidates[:5]:
        parts.append(
            "|".join(
                str(value or "")
                for value in (
                    candidate.conId,
                    candidate.symbol,
                    candidate.secType,
                    candidate.exchange,
                    candidate.primaryExchange,
                    candidate.currency,
                    candidate.localSymbol,
                    candidate.tradingClass,
                )
            )
        )
    if len(candidates) > 5:
        parts.append(f"... +{len(candidates) - 5} more")
    return "; ".join(parts)
