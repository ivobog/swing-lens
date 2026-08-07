from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.models.ceri_tables import (
    CeriCatalystEventRevision,
    CeriChangeEvent,
    CeriGuidanceEvent,
    CeriScoreSnapshot,
)
from app.services.ceri.change_detection_service import CeriChangeDetectionService

UTC = ZoneInfo("UTC")


def test_score_change_detection_is_idempotent_for_same_snapshot_pair() -> None:
    prior = _snapshot(1, opportunity=4.0, risk=1.0)
    current = _snapshot(2, opportunity=6.0, risk=4.0)
    db = FakeDb(scalar_queue=[None])
    service = CeriChangeDetectionService()

    first = service.detect_score_changes(db, current=current, prior=prior, scope="run:7")
    db.scalar_queue = list(db.added)
    second = service.detect_score_changes(db, current=current, prior=prior, scope="run:7")

    assert first.changes == 3
    assert second.duplicates == 3
    assert len([row for row in db.added if isinstance(row, CeriChangeEvent)]) == 3


def test_catalyst_revision_change_emits_stable_binary_event() -> None:
    revision = CeriCatalystEventRevision(
        id=11,
        catalyst_event_id=9,
        revision_number=1,
        status="SCHEDULED",
        direction="UNKNOWN",
        effective_session=date(2026, 8, 1),
        date_confidence="DATE_RANGE",
    )
    db = FakeDb(scalar_queue=[None])

    result = CeriChangeDetectionService().detect_catalyst_revision(
        db,
        revision=revision,
        company_id=42,
    )

    changes = [row for row in db.added if isinstance(row, CeriChangeEvent)]
    assert result.changes == 1
    assert changes[0].change_type == "NEW_BINARY_EVENT"
    assert changes[0].dedup_key


def test_guidance_and_conflict_transitions_are_persisted() -> None:
    guidance = CeriGuidanceEvent(
        id=12,
        company_id=42,
        action="RAISED",
        effective_session=date(2026, 8, 1),
        confidence="High",
    )
    db = FakeDb(scalar_queue=[None, None])
    service = CeriChangeDetectionService()

    guidance_result = service.detect_guidance_change(
        db,
        guidance=guidance,
        company_id=42,
    )
    prior = _snapshot(3, opportunity=4.0, risk=1.0)
    current = _snapshot(4, opportunity=4.0, risk=1.0)
    current.warnings_json = ["estimate_data_stale", "provider_conflict_open"]
    conflict_result = service.detect_score_changes(db, current=current, prior=prior)

    types = {row.change_type for row in db.added if isinstance(row, CeriChangeEvent)}
    assert guidance_result.changes == 1
    assert conflict_result.changes == 2
    assert types == {"GUIDANCE_RAISED", "DATA_STALE", "CONFLICT_OPENED"}


def _snapshot(snapshot_id: int, *, opportunity: float, risk: float) -> CeriScoreSnapshot:
    return CeriScoreSnapshot(
        id=snapshot_id,
        company_id=42,
        ticker="MSFT",
        as_of_session=date(2026, 8, snapshot_id),
        cutoff_at=datetime(2026, 8, snapshot_id, 21, tzinfo=UTC),
        opportunity_score=opportunity,
        event_risk_score=risk,
        data_confidence="Normal",
        coverage_pct=100.0,
        posture="Improving",
        component_json={"components": [{"name": "revision_magnitude", "value": opportunity}]},
        config_version="2026-07-31",
        config_hash="hash",
        calculation_version="ceri-1.0.0",
        evidence_hash=f"evidence-{snapshot_id}",
    )


class FakeDb:
    def __init__(self, scalar_queue=None) -> None:
        self.scalar_queue = list(scalar_queue or [])
        self.added = []
        self.next_id = 1

    def scalar(self, _statement):
        if self.scalar_queue:
            return self.scalar_queue.pop(0)
        return None

    def add(self, row) -> None:
        self.added.append(row)

    def flush(self) -> None:
        for row in self.added:
            if getattr(row, "id", None) is None:
                row.id = self.next_id
                self.next_id += 1
