from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriCatalystEventRevision, CeriManualReview


class CeriManualReviewService:
    def create_catalyst_override(
        self,
        db: Session,
        *,
        current_revision: CeriCatalystEventRevision,
        new_values: dict[str, Any],
        reviewer: str,
        reason: str,
    ) -> tuple[CeriManualReview, CeriCatalystEventRevision]:
        review = CeriManualReview(
            target_type="ceri_catalyst_event_revision",
            target_id=current_revision.id,
            prior_value_json=_revision_values(current_revision),
            new_value_json=new_values,
            reviewer=reviewer,
            reason=reason,
        )
        current_revision.is_current = False
        new_revision = CeriCatalystEventRevision(
            catalyst_event_id=current_revision.catalyst_event_id,
            source_record_id=current_revision.source_record_id,
            prior_revision_id=current_revision.id,
            revision_number=current_revision.revision_number + 1,
            is_current=True,
            announced_at=new_values.get("announced_at", current_revision.announced_at),
            expected_date=new_values.get("expected_date", current_revision.expected_date),
            effective_session=new_values.get(
                "effective_session",
                current_revision.effective_session,
            ),
            status=new_values.get("status", current_revision.status),
            direction=new_values.get("direction", current_revision.direction),
            materiality=new_values.get("materiality", current_revision.materiality),
            date_confidence=new_values.get("date_confidence", current_revision.date_confidence),
            source_confidence=new_values.get(
                "source_confidence",
                current_revision.source_confidence,
            ),
            operational_values_json={
                **(current_revision.operational_values_json or {}),
                **new_values,
                "manual_review_reason": reason,
            },
            conflict_flags_json=current_revision.conflict_flags_json,
            review_state="REVIEWED",
        )
        db.add(review)
        db.add(new_revision)
        db.flush()
        return review, new_revision


def _revision_values(revision: CeriCatalystEventRevision) -> dict[str, Any]:
    return {
        "status": revision.status,
        "direction": revision.direction,
        "materiality": revision.materiality,
        "expected_date": revision.expected_date.isoformat() if revision.expected_date else None,
        "effective_session": revision.effective_session.isoformat()
        if revision.effective_session
        else None,
    }
