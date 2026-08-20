from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriCompany
from app.services.ceri.sec.provider import SecCeriProvider


@dataclass(frozen=True)
class SecIdentityRepairResult:
    ticker: str
    status: str
    cik: str | None = None
    reason: str | None = None

    @property
    def resolved(self) -> bool:
        return self.status in {"ALREADY_RESOLVED", "RESOLVED"}


def resolve_and_persist_sec_identity(
    db: Session,
    *,
    provider: SecCeriProvider,
    ticker: str,
) -> SecIdentityRepairResult:
    """Resolve an exact SEC ticker identity without fuzzy/ambiguous guessing."""

    symbol = str(ticker).strip().upper()
    rows = list(db.scalars(select(CeriCompany).where(CeriCompany.ticker == symbol)).all())
    known = sorted({str(row.cik).zfill(10) for row in rows if row.cik})
    if len(known) > 1:
        return SecIdentityRepairResult(
            symbol,
            "AMBIGUOUS",
            reason=f"Conflicting persisted CIK values: {', '.join(known)}",
        )
    if known:
        return SecIdentityRepairResult(symbol, "ALREADY_RESOLVED", cik=known[0])

    candidate_resolver = getattr(provider, "resolve_cik_candidates", None)
    candidates = (
        tuple(candidate_resolver(symbol))
        if callable(candidate_resolver)
        else tuple(filter(None, (provider.resolve_cik(symbol),)))
    )
    if len(candidates) > 1:
        return SecIdentityRepairResult(
            symbol,
            "AMBIGUOUS",
            reason=f"SEC metadata has conflicting exact CIK values: {', '.join(candidates)}",
        )
    resolved = candidates[0] if candidates else None
    if resolved is None:
        return SecIdentityRepairResult(
            symbol,
            "UNRESOLVED",
            reason="SEC company-ticker metadata has no exact ticker match.",
        )

    normalized = str(resolved).zfill(10)
    if rows:
        for row in rows:
            row.cik = normalized
            row.sec_applicability = "REQUIRED"
    else:
        db.add(
            CeriCompany(
                ticker=symbol,
                exchange="US",
                cik=normalized,
                sec_applicability="REQUIRED",
            )
        )
    db.flush()
    return SecIdentityRepairResult(symbol, "RESOLVED", cik=normalized)
