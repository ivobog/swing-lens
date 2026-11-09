"""Reviewed same-security symbol changes, distinct from terminal lifecycle events."""

import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical

REGISTRY_PATH = Path(__file__).resolve().parents[2] / "config/instrument_symbol_changes.json"


@dataclass(frozen=True)
class InstrumentSymbolChange:
    previous_symbol: str
    current_symbol: str
    effective_date: date
    ib_conid: int
    primary_exchange: str
    currency: str
    sec_type: str
    source_url: str

    def evidence(self) -> dict[str, object]:
        payload = Canonical.canonicalize(asdict(self))
        return {**payload, "fingerprint": Canonical.fingerprint(payload)}

    def matches(self, identity: dict[str, object]) -> bool:
        return (
            identity.get("conId") == self.ib_conid
            and identity.get("symbol") == self.current_symbol
            and identity.get("primaryExchange") == self.primary_exchange
            and identity.get("currency") == self.currency
            and identity.get("secType") == self.sec_type
        )


def load_symbol_changes(path: Path | None = None) -> tuple[InstrumentSymbolChange, ...]:
    payload = json.loads((path or REGISTRY_PATH).read_text(encoding="utf-8"))
    if payload.get("version") != 1 or not isinstance(payload.get("changes"), list):
        raise ValueError("INSTRUMENT_SYMBOL_CHANGE_REGISTRY_INVALID")
    changes = []
    previous_symbols = set()
    for entry in payload["changes"]:
        change = InstrumentSymbolChange(
            **{**entry, "effective_date": date.fromisoformat(entry["effective_date"])}
        )
        if (
            not change.previous_symbol
            or change.previous_symbol != change.previous_symbol.strip().upper()
            or not change.current_symbol
            or change.current_symbol != change.current_symbol.strip().upper()
            or change.previous_symbol == change.current_symbol
            or type(change.ib_conid) is not int
            or change.ib_conid <= 0
            or change.sec_type != "STK"
            or change.currency != "USD"
            or not change.primary_exchange
            or not change.source_url.startswith("https://")
            or change.previous_symbol in previous_symbols
        ):
            raise ValueError("INSTRUMENT_SYMBOL_CHANGE_REGISTRY_INVALID")
        previous_symbols.add(change.previous_symbol)
        changes.append(change)
    return tuple(changes)


def active_symbol_change(
    ticker: str, *, as_of: date | None = None
) -> InstrumentSymbolChange | None:
    provider_date = as_of or datetime.now(UTC).date()
    return next(
        (
            change for change in load_symbol_changes()
            if change.previous_symbol == ticker.strip().upper()
            and change.effective_date <= provider_date
        ),
        None,
    )


def symbol_change_evidence(
    ticker: str, identity: dict[str, object]
) -> dict[str, object] | None:
    change = active_symbol_change(ticker)
    return change.evidence() if change is not None and change.matches(identity) else None


def contract_identity_matches_ticker(
    ticker: str,
    identity: dict[str, object],
    evidence: dict[str, object] | None = None,
    *,
    reviewed_session: date | None = None,
) -> bool:
    """A different provider symbol needs exact sourced, same-conId authority."""
    if not identity.get("conId"):
        return False
    if str(identity.get("symbol", "")).upper() == ticker.upper():
        return evidence is None
    change = active_symbol_change(ticker)
    return bool(
        change is not None
        and evidence == change.evidence()
        and change.matches(identity)
        and reviewed_session is not None
        and reviewed_session < change.effective_date
    )
