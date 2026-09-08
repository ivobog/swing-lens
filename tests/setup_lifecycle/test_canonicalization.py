from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

from app.models.tables import SetupSignalSnapshot
from app.services.setup_lifecycle.canonicalization import (
    SetupLifecycleCanonicalizer,
    select_canonical_snapshot,
)
from app.services.setup_lifecycle.config import load_setup_lifecycle_config
from app.services.setup_lifecycle.repository import SetupLifecycleRepository


def test_select_canonical_snapshot_uses_phase_4_precedence() -> None:
    stale_high_coverage = _snapshot(
        1,
        coverage=Decimal("1.0"),
        has_bar=False,
        calculated_at=datetime(2026, 8, 1, 22, tzinfo=UTC),
    )
    fresh_lower_coverage = _snapshot(
        2,
        coverage=Decimal("0.9"),
        has_bar=True,
        calculated_at=datetime(2026, 8, 1, 21, tzinfo=UTC),
    )

    assert select_canonical_snapshot([stale_high_coverage, fresh_lower_coverage]).id == 2


def test_select_canonical_snapshot_uses_snapshot_id_as_final_tiebreak() -> None:
    first = _snapshot(1)
    second = _snapshot(2)

    assert select_canonical_snapshot([first, second]).id == 2


def test_canonicalizer_promotes_exactly_one_snapshot_and_audits_changes() -> None:
    previous = _snapshot(1, is_canonical=True, coverage=Decimal("0.75"))
    selected = _snapshot(2, coverage=Decimal("1.0"))
    repository = FakeCanonicalRepository([previous, selected])
    canonicalizer = SetupLifecycleCanonicalizer(
        repository=repository,
        config=load_setup_lifecycle_config(),
    )

    result = canonicalizer.canonicalize_snapshots(
        db=object(),
        snapshots=[previous, selected],
        evaluation_run_id=11,
    )

    assert result.selected_snapshot_ids == (2,)
    assert result.changed_snapshot_ids == (2,)
    assert result.audit_event_ids == (1001,)
    assert selected.is_canonical is True
    assert previous.is_canonical is True
    assert previous.superseded_by_snapshot_id is None
    assert repository.events[0].event_type == "CANONICAL_REVISION"
    assert repository.events[0].source_event_key
    assert repository.events[0].evidence_json["canonical_score"][2] == 1.0
    assert isinstance(repository.events[0].evidence_json["canonical_score"][4], str)
    assert selected.canonical_decision_json["score"][2] == 1.0
    assert isinstance(selected.canonical_decision_json["score"][4], str)


def test_later_run_advances_current_selection_without_mutating_prior_evidence() -> None:
    prior_run_snapshot = _snapshot(36934, run_id=148, is_canonical=True)
    later_run_snapshot = _snapshot(
        36939,
        run_id=149,
        calculated_at=datetime(2026, 9, 8, 15, 32, 53, tzinfo=UTC),
    )
    repository = FakeCanonicalRepository([prior_run_snapshot, later_run_snapshot])
    canonicalizer = SetupLifecycleCanonicalizer(
        repository=repository,
        config=load_setup_lifecycle_config(),
    )
    before = _historical_evidence_hash(prior_run_snapshot)

    canonicalizer.canonicalize_snapshots(
        db=object(),
        snapshots=[prior_run_snapshot, later_run_snapshot],
        evaluation_run_id=257,
    )

    assert _historical_evidence_hash(prior_run_snapshot) == before
    assert prior_run_snapshot.is_canonical is True
    assert prior_run_snapshot.superseded_by_snapshot_id is None
    assert repository.current_snapshot().id == 36939


def test_current_and_historical_selection_have_distinct_meanings() -> None:
    run_a = _snapshot(10, run_id=100, is_canonical=True)
    run_b = _snapshot(
        11,
        run_id=101,
        calculated_at=datetime(2026, 8, 1, 22, tzinfo=UTC),
    )
    repository = FakeCanonicalRepository([run_a, run_b])
    canonicalizer = SetupLifecycleCanonicalizer(repository=repository)

    canonicalizer.canonicalize_snapshots(object(), [run_a, run_b])

    assert next(row for row in repository.peers if row.run_id == 100) is run_a
    assert repository.current_snapshot() is run_b


def test_retry_is_idempotent_for_pointer_and_audit_event() -> None:
    selected = _snapshot(2)
    repository = FakeCanonicalRepository([selected])
    canonicalizer = SetupLifecycleCanonicalizer(repository=repository)

    first = canonicalizer.canonicalize_snapshots(object(), [selected])
    second = canonicalizer.canonicalize_snapshots(object(), [selected])

    assert first.changed_snapshot_ids == (2,)
    assert second.changed_snapshot_ids == ()
    assert len(repository.selection_events) == 1


def test_canonicalizer_does_not_emit_audit_when_choice_is_unchanged() -> None:
    selected = _snapshot(2, is_canonical=True)
    repository = FakeCanonicalRepository([selected])
    canonicalizer = SetupLifecycleCanonicalizer(
        repository=repository,
        config=load_setup_lifecycle_config(),
    )

    result = canonicalizer.canonicalize_snapshots(db=object(), snapshots=[selected])

    assert result.changed_snapshot_ids == ()
    assert result.unchanged_snapshot_ids == (2,)
    assert repository.events == []


class FakeCanonicalRepository:
    stable_key = staticmethod(SetupLifecycleRepository.stable_key)

    def __init__(self, peers) -> None:
        self.peers = peers
        self.events = []
        self.selection_events = []
        self.selected = next((peer for peer in peers if peer.is_canonical), None)

    def add_lifecycle_event(self, _db, event):
        event.id = 1000 + len(self.events) + 1
        self.events.append(event)
        return event

    def advance_canonical_selection(
        self,
        _db,
        snapshot,
        *,
        reason,
        decision,
        evaluation_run_id,
    ):
        previous = self.selected
        changed = previous is None or previous.id != snapshot.id
        audit_event = None
        if changed:
            audit_event = SimpleNamespace(decision_json=dict(decision))
            self.selection_events.append(audit_event)
            self.selected = snapshot
        return SimpleNamespace(
            selection=SimpleNamespace(selection_decision_json=dict(decision)),
            previous_snapshot=previous,
            changed=changed,
            audit_event=audit_event,
        )

    def record_snapshot_canonical_decision(self, _db, snapshot, *, reason, decision):
        snapshot.is_canonical = True
        snapshot.canonical_reason = reason
        snapshot.canonical_decision_json = decision

    def current_snapshot(self):
        return self.selected

def _snapshot(
    snapshot_id: int,
    *,
    run_id: int = 7,
    coverage: Decimal = Decimal("1.0"),
    has_bar: bool = True,
    is_canonical: bool = False,
    calculated_at: datetime = datetime(2026, 8, 1, 21, tzinfo=UTC),
) -> SetupSignalSnapshot:
    snapshot = SetupSignalSnapshot(
        id=snapshot_id,
        run_id=run_id,
        ticker="MSFT",
        timeframe="1d",
        data_as_of_date=date(2026, 8, 1),
        calculated_at=calculated_at,
        origin_type="LIVE_RUN",
        engine_version="slse-1.0.0",
        config_version="v1",
        config_hash="hash",
        source_data_hash=f"source-{snapshot_id}",
        schema_version="snapshot-v1",
        data_quality_label="NORMAL",
        required_feature_coverage=coverage,
        market_regime_snapshot_id=601,
        sector_rotation_snapshot_id=701,
        primary_setup_family="BREAKOUT",
        primary_phase="PIVOT_READY",
        confidence_score=80,
        confidence_label="NORMAL",
        actionability_candidate="WATCH_ONLY",
        is_canonical=is_canonical,
        warning_flags_json=[],
        source_lineage_json={
            "latest_bar": {"bar_date": "2026-08-01"} if has_bar else None,
        },
    )
    return snapshot


def _historical_evidence_hash(snapshot: SetupSignalSnapshot) -> str:
    payload = {
        "id": snapshot.id,
        "run_id": snapshot.run_id,
        "ticker": snapshot.ticker,
        "data_as_of_date": snapshot.data_as_of_date.isoformat(),
        "source_data_hash": snapshot.source_data_hash,
        "is_canonical": snapshot.is_canonical,
        "superseded_by_snapshot_id": snapshot.superseded_by_snapshot_id,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
