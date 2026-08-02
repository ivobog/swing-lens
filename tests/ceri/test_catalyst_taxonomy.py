from __future__ import annotations

from datetime import date

import pytest

from app.models.ceri_tables import CeriCatalystEventRevision, CeriSourceRecord
from app.services.ceri.catalyst_taxonomy import CeriCatalystTaxonomy
from app.services.ceri.enums import CatalystCategory, CatalystStatus
from app.services.ceri.manual_review_service import CeriManualReviewService


def test_catalyst_taxonomy_normalizes_category_and_subject_key() -> None:
    source = CeriSourceRecord(
        id=21,
        provider="manual",
        dataset="catalysts",
        provider_record_id="cat-1",
        raw_json={
            "ticker": "MSFT",
            "subtype": "award",
            "subject": "Multi year Azure contract",
            "source_date": "2026-08-03",
        },
        content_hash="hash",
        idempotency_key="key",
    )

    record = CeriCatalystTaxonomy().normalize(source, company_id=42)

    assert record.category is CatalystCategory.CONTRACT
    assert record.subtype == "award"
    assert record.subject_key == "multi-year-azure-contract"
    assert record.effective_session == date(2026, 8, 3)


def test_invalid_catalyst_status_transition_is_rejected() -> None:
    taxonomy = CeriCatalystTaxonomy()

    with pytest.raises(ValueError, match="invalid catalyst transition"):
        taxonomy.validate_transition(CatalystStatus.CANCELLED, CatalystStatus.ANNOUNCED)


def test_manual_override_keeps_prior_new_reviewer_reason_and_revision_lineage() -> None:
    db = FakeDb()
    current = CeriCatalystEventRevision(
        id=5,
        catalyst_event_id=3,
        source_record_id=21,
        revision_number=1,
        is_current=True,
        status="SCHEDULED",
        direction="UNKNOWN",
        materiality=1.0,
    )

    review, revision = CeriManualReviewService().create_catalyst_override(
        db,
        current_revision=current,
        new_values={"status": "CANCELLED", "direction": "NEGATIVE"},
        reviewer="analyst@example.com",
        reason="Provider correction confirmed.",
    )

    assert current.is_current is False
    assert review.prior_value_json["status"] == "SCHEDULED"
    assert review.new_value_json["status"] == "CANCELLED"
    assert review.reviewer == "analyst@example.com"
    assert review.reason == "Provider correction confirmed."
    assert revision.prior_revision_id == 5
    assert revision.revision_number == 2
    assert revision.status == "CANCELLED"


class FakeDb:
    def __init__(self) -> None:
        self.added = []
        self.next_id = 1

    def add(self, row) -> None:
        self.added.append(row)

    def flush(self) -> None:
        for row in self.added:
            if getattr(row, "id", None) is None:
                row.id = self.next_id
                self.next_id += 1
