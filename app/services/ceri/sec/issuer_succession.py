"""Reviewed SEC issuer symbol changes, effective on regular-way trading dates."""

from datetime import date

from sqlalchemy import select, text

from app.models.ceri_tables import CeriCompany

# Parent's 8-K, accession 0001193125-26-434274. MSGR is a separate issuer.
SYMBOL_CHANGES = {"0001636519": ("MSGS", "MSGK", date(2026, 10, 27))}


def reviewed_symbols(cik, symbols):
    change = SYMBOL_CHANGES.get(cik)
    return bool(change and sorted(symbols) == sorted(change[:2]))


def effective_symbol(cik, as_of):
    change = SYMBOL_CHANGES.get(cik)
    if change and as_of:
        old, new, effective = change
        return new if as_of >= effective else old
    return None


def ownership_conflict(db, ticker, cik, *, writing=False):
    # Serialize competing claims to the same SEC issuer across ticker writers.
    if writing and getattr(db, "bind", None) is not None and db.bind.dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": f"sec-company-cik:{cik}"},
        )
    owners = db.scalars(select(CeriCompany).where(CeriCompany.cik == cik)).all()
    symbols = [row.ticker for row in owners]
    if ticker not in symbols:
        symbols.append(ticker)
    if len(symbols) > 1 and not reviewed_symbols(cik, symbols):
        return f"SEC CIK {cik} requires identity review for {', '.join(sorted(symbols))}"
    return None
