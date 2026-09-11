from __future__ import annotations

import hashlib
from typing import Any

from app.models.tables import (
    SetupSignalSnapshot,
    SetupSignalSnapshotCurrentSelection,
)
from app.services.winner_probability.temporal_manifest_canonicalization import (
    canonical_manifest_bytes,
)

# This is the sole legacy administrative field left on the evidence row. New
# code never writes it; it is excluded because old databases already contain
# later-run supersession values. Every other persisted snapshot field is part
# of the immutable evidence boundary, including decision-time canonicalization.
LEGACY_MUTABLE_ADMIN_FIELDS = frozenset({"superseded_by_snapshot_id"})
IMMUTABLE_EVIDENCE_FIELDS = tuple(
    column.name
    for column in SetupSignalSnapshot.__table__.columns
    if column.name not in LEGACY_MUTABLE_ADMIN_FIELDS
)
CURRENT_ADMIN_STATE_FIELDS = tuple(
    column.name for column in SetupSignalSnapshotCurrentSelection.__table__.columns
)


def immutable_evidence_payload(snapshot: SetupSignalSnapshot) -> dict[str, Any]:
    return {field: getattr(snapshot, field) for field in IMMUTABLE_EVIDENCE_FIELDS}


def immutable_evidence_hash(snapshot: SetupSignalSnapshot) -> str:
    return _hash(immutable_evidence_payload(snapshot))


def current_admin_state_payload(
    selection: SetupSignalSnapshotCurrentSelection,
) -> dict[str, Any]:
    return {field: getattr(selection, field) for field in CURRENT_ADMIN_STATE_FIELDS}


def current_admin_state_hash(selection: SetupSignalSnapshotCurrentSelection) -> str:
    return _hash(current_admin_state_payload(selection))


def _hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_manifest_bytes(payload)).hexdigest()
