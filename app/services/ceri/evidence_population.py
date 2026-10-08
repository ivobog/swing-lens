from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriScoreSnapshot
from app.services.ceri.evidence_eligibility import (
    EXCLUDED,
    effective_disposition_subquery,
    eligible_snapshot_select,
)


def evidence_population_summary(
    db: Session,
    *,
    run_id: int | None = None,
    ticker: str | None = None,
) -> dict[str, Any]:
    """Describe captured CERI evidence without promoting ineligible rows to current."""

    predicates = []
    if run_id is not None:
        predicates.append(CeriScoreSnapshot.run_id == run_id)
    if ticker is not None:
        predicates.append(CeriScoreSnapshot.ticker == ticker.strip().upper())

    captured_count = int(
        db.scalar(select(func.count(CeriScoreSnapshot.id)).where(*predicates)) or 0
    )
    eligible_ids = eligible_snapshot_select(
        CeriScoreSnapshot.id,
        name="evidence_population_eligibility",
    ).where(*predicates)
    eligible_count = int(
        db.scalar(select(func.count()).select_from(eligible_ids.subquery())) or 0
    )

    effective = effective_disposition_subquery("evidence_population_dispositions")
    reason_rows = db.execute(
        select(effective.c.reason_code, func.count(CeriScoreSnapshot.id))
        .select_from(CeriScoreSnapshot)
        .join(effective, effective.c.ceri_snapshot_id == CeriScoreSnapshot.id)
        .where(*predicates, effective.c.disposition == EXCLUDED)
        .group_by(effective.c.reason_code)
        .order_by(func.count(CeriScoreSnapshot.id).desc(), effective.c.reason_code.asc())
    ).all()
    reason_counts = [
        {"reason": str(reason), "count": int(count)} for reason, count in reason_rows
    ]
    explicitly_excluded = sum(row["count"] for row in reason_counts)
    excluded_count = max(captured_count - eligible_count, 0)
    owner_ineligible_count = max(excluded_count - explicitly_excluded, 0)
    if owner_ineligible_count:
        reason_counts.append(
            {"reason": "OWNER_PIPELINE_UNSUCCESSFUL", "count": owner_ineligible_count}
        )

    # Snapshot dispositions currently define ELIGIBLE/EXCLUDED only. Keep quarantine
    # explicit in the contract so a future disposition can be shown without ambiguity.
    quarantined_count = 0
    if captured_count == 0:
        state = "ABSENT"
    elif quarantined_count == captured_count:
        state = "CAPTURED_QUARANTINED"
    elif eligible_count == captured_count:
        state = "CAPTURED_ELIGIBLE"
    elif eligible_count == 0:
        state = "CAPTURED_EXCLUDED"
    else:
        state = "CAPTURED_MIXED"

    return {
        "state": state,
        "captured_count": captured_count,
        "eligible_count": eligible_count,
        "excluded_count": excluded_count,
        "quarantined_count": quarantined_count,
        "exclusion_reasons": reason_counts,
    }
