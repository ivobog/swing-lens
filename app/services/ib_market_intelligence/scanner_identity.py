from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def scanner_conids_by_ticker(
    rows: Iterable[tuple[str, int | None]],
) -> dict[str, set[int]]:
    result: dict[str, set[int]] = {}
    for raw_ticker, conid in rows:
        ticker = str(raw_ticker or "").strip().upper()
        if ticker and conid is not None:
            result.setdefault(ticker, set()).add(int(conid))
    return result


def canonical_scanner_identity(
    *,
    ticker: str,
    ib_conid: int | None,
    contract_metadata: Mapping[str, Any] | None = None,
    known_conids_by_ticker: Mapping[str, set[int]] | None = None,
) -> str:
    normalized_ticker = str(ticker or "").strip().upper()
    if not normalized_ticker:
        raise ValueError("scanner candidate identity requires a ticker")
    if ib_conid is not None:
        return f"CONID:{int(ib_conid)}"
    known = (known_conids_by_ticker or {}).get(normalized_ticker, set())
    if len(known) == 1:
        return f"CONID:{next(iter(known))}"
    metadata = contract_metadata or {}
    sec_type = str(metadata.get("sec_type") or "").strip().upper()
    currency = str(metadata.get("currency") or "").strip().upper()
    primary_exchange = str(
        metadata.get("primary_exchange") or metadata.get("exchange") or ""
    ).strip().upper()
    return ":".join(
        ("SYMBOL", normalized_ticker, sec_type or "?", currency or "?", primary_exchange or "?")
    )
