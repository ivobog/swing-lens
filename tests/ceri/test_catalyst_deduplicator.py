from __future__ import annotations

from datetime import date

from app.services.ceri.catalyst_deduplicator import CeriCatalystDeduplicator
from app.services.ceri.dtos import NormalizedCatalystRecord
from app.services.ceri.enums import (
    CatalystCategory,
    CatalystDirection,
    CatalystStatus,
    CeriConfidenceLabel,
    DateConfidence,
)


def test_equivalent_contract_award_records_become_one_cluster_with_two_sources() -> None:
    records = [
        _record(1, "Mega cloud contract", date(2026, 8, 12)),
        _record(2, "Mega cloud contract", date(2026, 8, 12)),
    ]

    clusters = CeriCatalystDeduplicator().cluster(records)

    assert len(clusters) == 1
    assert clusters[0].canonical.source_record_id == 1
    assert {row.source_record_id for row in clusters[0].sources} == {1, 2}


def test_conflicting_event_dates_remain_visible_and_lower_confidence() -> None:
    clusters = CeriCatalystDeduplicator().cluster(
        [
            _record(1, "Mega cloud contract", date(2026, 8, 12)),
            _record(2, "Mega cloud contract", date(2026, 8, 13)),
        ]
    )

    assert len(clusters) == 1
    assert {row.expected_date for row in clusters[0].sources} == {
        date(2026, 8, 12),
        date(2026, 8, 13),
    }
    assert "conflicting_event_dates" in clusters[0].conflict_flags


def _record(
    source_record_id: int,
    subject: str,
    expected_date: date,
) -> NormalizedCatalystRecord:
    return NormalizedCatalystRecord(
        source_record_id=source_record_id,
        company_id=42,
        category=CatalystCategory.CONTRACT,
        subtype="award",
        subject_key="mega-cloud-contract",
        status=CatalystStatus.ANNOUNCED,
        direction=CatalystDirection.POSITIVE,
        materiality=2.0,
        confidence=CeriConfidenceLabel.NORMAL,
        date_confidence=DateConfidence.EXACT_DATE,
        expected_date=expected_date,
        effective_session=expected_date,
        canonical_text=subject,
    )
