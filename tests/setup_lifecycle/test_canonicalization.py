from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

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
    assert previous.is_canonical is False
    assert previous.superseded_by_snapshot_id == 2
    assert repository.events[0].event_type == "CANONICAL_REVISION"
    assert repository.events[0].source_event_key
    assert repository.events[0].evidence_json["canonical_score"][2] == 1.0
    assert isinstance(repository.events[0].evidence_json["canonical_score"][4], str)
    assert selected.canonical_decision_json["score"][2] == 1.0
    assert isinstance(selected.canonical_decision_json["score"][4], str)


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

    def add_lifecycle_event(self, _db, event):
        event.id = 1000 + len(self.events) + 1
        self.events.append(event)
        return event

    def promote_canonical_snapshot(self, _db, snapshot, *, reason, decision):
        for peer in self.peers:
            if peer.id != snapshot.id and peer.is_canonical:
                peer.is_canonical = False
                peer.superseded_by_snapshot_id = snapshot.id
        snapshot.is_canonical = True
        snapshot.canonical_reason = reason
        snapshot.canonical_decision_json = decision
        return snapshot

def _snapshot(
    snapshot_id: int,
    *,
    coverage: Decimal = Decimal("1.0"),
    has_bar: bool = True,
    is_canonical: bool = False,
    calculated_at: datetime = datetime(2026, 8, 1, 21, tzinfo=UTC),
) -> SetupSignalSnapshot:
    snapshot = SetupSignalSnapshot(
        id=snapshot_id,
        run_id=7,
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
