from __future__ import annotations

from datetime import UTC, date, datetime

from app.models.tables import SetupSignalSnapshot, SetupSignalSnapshotCurrentSelection
from app.services.setup_lifecycle.forensic_hash import (
    CURRENT_ADMIN_STATE_FIELDS,
    IMMUTABLE_EVIDENCE_FIELDS,
    current_admin_state_hash,
    immutable_evidence_hash,
)


def test_immutable_evidence_hash_excludes_only_legacy_supersession_pointer() -> None:
    snapshot = _snapshot()
    before = immutable_evidence_hash(snapshot)

    snapshot.superseded_by_snapshot_id = 99

    assert immutable_evidence_hash(snapshot) == before
    assert "is_canonical" in IMMUTABLE_EVIDENCE_FIELDS
    assert "canonical_decision_json" in IMMUTABLE_EVIDENCE_FIELDS
    assert "source_lineage_json" in IMMUTABLE_EVIDENCE_FIELDS
    assert "superseded_by_snapshot_id" not in IMMUTABLE_EVIDENCE_FIELDS


def test_current_admin_hash_changes_when_pointer_advances() -> None:
    selection = SetupSignalSnapshotCurrentSelection(
        id=1,
        ticker="MSFT",
        timeframe="1d",
        data_as_of_date=date(2026, 9, 4),
        selected_snapshot_id=10,
        revision=1,
        selection_reason="test",
        selection_decision_json={},
        created_at=datetime(2026, 9, 8, 10, tzinfo=UTC),
        updated_at=datetime(2026, 9, 8, 10, tzinfo=UTC),
    )
    before = current_admin_state_hash(selection)

    selection.selected_snapshot_id = 11
    selection.revision = 2

    assert current_admin_state_hash(selection) != before
    assert "selected_snapshot_id" in CURRENT_ADMIN_STATE_FIELDS
    assert "revision" in CURRENT_ADMIN_STATE_FIELDS


def _snapshot() -> SetupSignalSnapshot:
    return SetupSignalSnapshot(
        id=10,
        run_id=100,
        ticker="MSFT",
        timeframe="1d",
        data_as_of_date=date(2026, 9, 4),
        calculated_at=datetime(2026, 9, 8, 10, tzinfo=UTC),
        captured_at=datetime(2026, 9, 8, 10, tzinfo=UTC),
        origin_type="LIVE_RUN",
        engine_version="slse-test",
        config_version="test-v1",
        config_hash="config-hash",
        source_data_hash="source-hash",
        schema_version="snapshot-v1",
        is_canonical=True,
        canonical_reason="test",
        canonicalized_at=datetime(2026, 9, 8, 10, tzinfo=UTC),
        data_quality_label="HIGH",
        signals_json={},
        feature_flags_json={},
        warning_flags_json=[],
        missing_data_json={},
        source_lineage_json={},
        diagnostic_high_cross_json={},
        canonical_decision_json={},
        debug_json={},
    )
