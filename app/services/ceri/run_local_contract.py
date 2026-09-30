from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.ceri_tables import (
    CeriCompany,
    CeriDerivedFeature,
    CeriIngestionRun,
    CeriIngestionRunSourceRecord,
    CeriRevisionFeature,
)

NO_ELIGIBLE_ESTIMATE_INPUT = "NO_ELIGIBLE_ESTIMATE_INPUT"


def provider_no_data_is_explicit(db: Session, *, run_id: int, ticker: str) -> bool:
    """True only when a completed estimate ingestion explicitly returned no rows."""

    normalized_ticker = ticker.strip().upper()
    candidates = list(
        db.scalars(
            select(CeriIngestionRun).where(
                CeriIngestionRun.dataset == "estimates",
                CeriIngestionRun.status == "COMPLETED",
            )
        )
    )
    matching = [
        row
        for row in candidates
        if int((row.scope_json or {}).get("run_id") or -1) == int(run_id)
        and str((row.scope_json or {}).get("ticker") or "").strip().upper()
        == normalized_ticker
    ]
    if not matching or any(int(row.fetched_count or 0) != 0 for row in matching):
        return False
    membership_count = int(
        db.scalar(
            select(func.count(CeriIngestionRunSourceRecord.id)).where(
                CeriIngestionRunSourceRecord.ingestion_run_id.in_(
                    [int(row.id) for row in matching]
                )
            )
        )
        or 0
    )
    return membership_count == 0


def has_revision_feature_output(
    db: Session, *, ticker: str, calculation_context_id: int
) -> bool:
    company_id = db.scalar(
        select(CeriCompany.id)
        .where(func.upper(CeriCompany.ticker) == ticker.strip().upper())
        .order_by(CeriCompany.id)
        .limit(1)
    )
    if company_id is None:
        return False
    # Current releases persist revision rows in both the native table and, for
    # some calculation paths, the generic derived-feature table.
    revision = db.scalar(
        select(CeriRevisionFeature.id)
        .where(
            CeriRevisionFeature.company_id == int(company_id),
            CeriRevisionFeature.calculation_context_id == int(calculation_context_id),
        )
        .limit(1)
    )
    if revision is not None:
        return True
    return (
        db.scalar(
            select(CeriDerivedFeature.id)
            .where(
                CeriDerivedFeature.company_id == int(company_id),
                CeriDerivedFeature.calculation_context_id == int(calculation_context_id),
                CeriDerivedFeature.feature_family == "revisions",
            )
            .limit(1)
        )
        is not None
    )
