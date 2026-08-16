from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.models.ceri_tables import (
    CeriCatalystEventRevision,
    CeriChangeEvent,
    CeriGuidanceEvent,
    CeriScoreSnapshot,
)
from app.services.ceri.change_detection_service import (
    CeriChangeDetectionService,
    change_dedup_key,
)

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
    assert set(first.change_ids) == set(second.change_ids)
    assert len([row for row in db.added if isinstance(row, CeriChangeEvent)]) == 3


def test_first_null_snapshot_establishes_baseline_without_opportunity_or_risk_change() -> None:
    current = _snapshot(1, opportunity=None, risk=5.0)

    result = CeriChangeDetectionService().detect_score_changes(
        FakeDb(), current=current, prior=None
    )

    assert result.changes == 0


def test_null_to_null_has_no_opportunity_change() -> None:
    prior = _snapshot(1, opportunity=None, risk=0.0)
    current = _snapshot(2, opportunity=None, risk=0.0)
    db = FakeDb()

    result = CeriChangeDetectionService().detect_score_changes(db, current=current, prior=prior)

    assert result.changes == 0
    assert not db.added


def test_first_numeric_snapshot_is_baseline_not_generic_upgrade() -> None:
    current = _snapshot(1, opportunity=7.0, risk=3.0)
    db = FakeDb()

    result = CeriChangeDetectionService().detect_score_changes(db, current=current, prior=None)

    assert result.changes == 0
    assert not db.added


def test_null_numeric_transitions_use_explicit_rating_change_types() -> None:
    service = CeriChangeDetectionService()
    became_rated_db = FakeDb()
    became_unrated_db = FakeDb()

    rated = service.detect_score_changes(
        became_rated_db,
        current=_snapshot(2, opportunity=6.0, risk=0.0),
        prior=_snapshot(1, opportunity=None, risk=0.0),
    )
    unrated = service.detect_score_changes(
        became_unrated_db,
        current=_snapshot(3, opportunity=None, risk=0.0),
        prior=_snapshot(2, opportunity=6.0, risk=0.0),
    )

    assert rated.changes == 1
    assert became_rated_db.added[0].change_type == "BECAME_RATED"
    assert unrated.changes == 1
    assert became_unrated_db.added[0].change_type == "BECAME_UNRATED"


def test_change_business_identity_does_not_depend_on_orchestration_scope() -> None:
    parts = {
        "company_id": 42,
        "change_type": "OPPORTUNITY_UPGRADED",
        "effective_session": date(2026, 8, 12),
        "from_snapshot_id": None,
        "to_snapshot_id": 10,
        "catalyst_revision_id": None,
        "config_hash": "hash",
        "calculation_version": "ceri-1.0.0",
    }

    assert change_dedup_key(**parts, scope="run:7") == change_dedup_key(
        **parts,
        scope="standalone",
    )


def test_catalyst_revision_change_emits_stable_binary_event() -> None:
    revision = CeriCatalystEventRevision(
        id=11,
        catalyst_event_id=9,
        revision_number=1,
        status="SCHEDULED",
        direction="UNKNOWN",
        effective_session=date(2026, 8, 1),
        date_confidence="DATE_RANGE",
        issuer_relevance=True,
        binary_eligible=True,
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
        accepted_for_scoring=True,
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


def _snapshot(
    snapshot_id: int, *, opportunity: float | None, risk: float | None
) -> CeriScoreSnapshot:
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
        opportunity_coverage_pct=100.0 if opportunity is not None else 0.0,
        event_risk_ledger_json={
            "accepted_evidence": True,
            "components": [
                {
                    "component": "earnings_proximity_risk",
                    "score": risk or 0.0,
                    "reason": "accepted_fixture_evidence",
                }
            ],
        },
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
