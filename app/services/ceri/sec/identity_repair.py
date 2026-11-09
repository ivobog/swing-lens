from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriCompany
from app.services.ceri.filing_authority import reviewed_filing_authority
from app.services.ceri.sec.provider import SecCeriProvider
from app.services.domain_mutation import MutationDomain, MutationSemanticMode
from app.services.source_mutation_authority import source_mutation_writer


@dataclass(frozen=True)
class SecIdentityRepairResult:
    ticker: str
    status: str
    cik: str | None = None
    reason: str | None = None

    @property
    def resolved(self) -> bool:
        return self.status in {"ALREADY_RESOLVED", "RESOLVED", "NOT_APPLICABLE"}


@source_mutation_writer(
    MutationDomain.CERI_SOURCE, "provider_source", mode=MutationSemanticMode.MAINTENANCE
)
def resolve_and_persist_sec_identity(
    db: Session,
    *,
    provider: SecCeriProvider,
    ticker: str,
) -> SecIdentityRepairResult:
    """Resolve exact SEC identity or retain an evidence-backed coverage exemption."""

    symbol = str(ticker).strip().upper()
    rows = list(
        db.scalars(select(CeriCompany).where(CeriCompany.ticker == symbol).with_for_update()).all()
    )
    reviewed = reviewed_filing_authority(symbol)
    if reviewed is not None and not reviewed.sec_required:
        if not rows:
            company = CeriCompany(
                ticker=symbol, exchange="US", company_name=reviewed.company_name
            )
            db.add(company)
            rows.append(company)
        for row in rows:
            row.sec_applicability = "NOT_APPLICABLE"
            row.sec_applicability_reason = reviewed.applicability_reason
            row.sec_applicability_updated_at = datetime.now(UTC)
        db.flush()
        return SecIdentityRepairResult(
            symbol, "NOT_APPLICABLE", reason=reviewed.applicability_reason
        )
    if rows and all(row.sec_applicability == "NOT_APPLICABLE" for row in rows):
        return SecIdentityRepairResult(
            symbol,
            "NOT_APPLICABLE",
            reason="; ".join(
                sorted({row.sec_applicability_reason for row in rows
                        if row.sec_applicability_reason})
            ),
        )
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
