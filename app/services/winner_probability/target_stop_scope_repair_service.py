"""Append-only repair for target/stop maturation scope leaks."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import (
    WinnerForwardOutcome,
    WinnerOutcomeDefinition,
    WinnerTargetStopOutcome,
)
from app.services.winner_probability.temporal_manifest_canonicalization import (
    canonical_manifest_bytes,
    canonicalize_manifest_value,
)

REPAIR_SCHEMA = "swinglens-winner-target-stop-scope-repair-v1"
REPAIR_TYPE = "MATURATION_SCOPE_LEAK_CORRECTION"


class TargetStopScopeRepairError(RuntimeError):
    pass


@dataclass(frozen=True)
class TargetStopScopeRepairResult:
    reviewed_manifest_hash: str
    repaired_count: int
    source_revision_ids: tuple[int, ...]
    created_revision_ids: tuple[int, ...]
    actor: str
    request_key: str
    repaired_at: datetime


def target_stop_scope_repair_hash(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_manifest_bytes(manifest)).hexdigest()


def build_target_stop_scope_repair_manifest(
    db: Session,
    *,
    target_stop_ids: list[int],
    incident: str,
) -> dict[str, Any]:
    ids = sorted({int(value) for value in target_stop_ids})
    if not ids or not incident.strip():
        raise TargetStopScopeRepairError("repair IDs and incident are required")
    rows = list(
        db.scalars(
            select(WinnerTargetStopOutcome)
            .where(WinnerTargetStopOutcome.id.in_(ids))
            .order_by(WinnerTargetStopOutcome.id)
        )
    )
    if [int(row.id) for row in rows] != ids:
        raise TargetStopScopeRepairError("one or more repair rows do not exist")
    records = [_repair_record(db, row, incident=incident) for row in rows]
    return canonicalize_manifest_value(
        {
            "schema": REPAIR_SCHEMA,
            "incident": incident.strip(),
            "repair_count": len(records),
            "records": records,
        }
    )


def apply_target_stop_scope_repair(
    db: Session,
    manifest: dict[str, Any],
    *,
    reviewed_manifest_hash: str,
    approve_write: bool,
    actor: str,
    request_key: str,
    now: datetime | None = None,
) -> TargetStopScopeRepairResult:
    if not approve_write:
        raise PermissionError("explicit approve_write=True is required")
    if not actor.strip() or not request_key.strip():
        raise ValueError("actor and request_key are required")
    if manifest.get("schema") != REPAIR_SCHEMA:
        raise TargetStopScopeRepairError("unsupported repair manifest schema")
    if target_stop_scope_repair_hash(manifest) != reviewed_manifest_hash:
        raise TargetStopScopeRepairError("repair manifest hash differs from reviewed hash")
    repaired_at = now or datetime.now(UTC)
    source_ids: list[int] = []
    created_ids: list[int] = []
    for record in manifest["records"]:
        source_id = int(record["target_stop_id"])
        source = db.scalar(
            select(WinnerTargetStopOutcome)
            .where(WinnerTargetStopOutcome.id == source_id)
            .with_for_update()
        )
        if source is None:
            raise TargetStopScopeRepairError(f"repair source {source_id} disappeared")
        current_record = _repair_record(db, source, incident=manifest["incident"])
        if canonical_manifest_bytes(current_record) != canonical_manifest_bytes(record):
            raise TargetStopScopeRepairError(f"repair source {source_id} changed after review")

        source.is_current_revision = False
        source.superseded_at = repaired_at
        expected = record["expected_corrective_revision"]
        metadata = {
            **expected["metadata_json"],
            "actor": actor.strip(),
            "request_key": request_key.strip(),
            "repair_manifest_hash": reviewed_manifest_hash,
        }
        corrective = WinnerTargetStopOutcome(
            prediction_id=int(record["prediction_id"]),
            outcome_definition_id=int(record["outcome_definition_id"]),
            forward_outcome_id=int(record["correct_forward_outcome_id"]),
            entry_model=expected["entry_model"],
            horizon_sessions=int(expected["horizon_sessions"]),
            status=expected["status"],
            revision=int(expected["revision"]),
            is_current_revision=True,
            target_pct=source.target_pct,
            stop_pct=source.stop_pct,
            target_hit=None,
            stop_hit=None,
            first_event=None,
            event_session=None,
            same_bar_conflict=False,
            primary_winner=None,
            optimistic_winner=None,
            conservative_winner=None,
            source_bar_lineage_hash=None,
            evaluated_at=None,
            superseded_at=None,
            metadata_json=metadata,
        )
        db.add(corrective)
        db.flush()
        source_ids.append(source_id)
        created_ids.append(int(corrective.id))
    return TargetStopScopeRepairResult(
        reviewed_manifest_hash=reviewed_manifest_hash,
        repaired_count=len(created_ids),
        source_revision_ids=tuple(source_ids),
        created_revision_ids=tuple(created_ids),
        actor=actor.strip(),
        request_key=request_key.strip(),
        repaired_at=repaired_at,
    )


def _repair_record(
    db: Session,
    row: WinnerTargetStopOutcome,
    *,
    incident: str,
) -> dict[str, Any]:
    if (
        row.entry_model != "SIGNAL_CLOSE_DIAGNOSTIC"
        or int(row.horizon_sessions) != 5
        or row.status != "MATURED"
        or not row.is_current_revision
        or row.evaluated_at is None
        or row.source_bar_lineage_hash is None
    ):
        raise TargetStopScopeRepairError(f"target/stop {row.id} is not the reviewed bad state")
    definition = db.get(WinnerOutcomeDefinition, int(row.outcome_definition_id))
    if definition is None or (
        definition.entry_model != row.entry_model
        or int(definition.horizon_sessions) != int(row.horizon_sessions)
    ):
        raise TargetStopScopeRepairError(f"target/stop {row.id} definition is incompatible")
    forwards = list(
        db.scalars(
            select(WinnerForwardOutcome)
            .where(WinnerForwardOutcome.prediction_id == row.prediction_id)
            .where(WinnerForwardOutcome.entry_model == row.entry_model)
            .where(WinnerForwardOutcome.horizon_sessions == row.horizon_sessions)
            .where(WinnerForwardOutcome.is_current_revision.is_(True))
            .order_by(WinnerForwardOutcome.id)
        )
    )
    if len(forwards) != 1:
        raise TargetStopScopeRepairError(
            f"target/stop {row.id} has {len(forwards)} diagnostic forward candidates"
        )
    correct_forward = forwards[0]
    if row.forward_outcome_id == correct_forward.id:
        raise TargetStopScopeRepairError(f"target/stop {row.id} already has correct forward link")
    metadata_before = dict(row.metadata_json or {})
    if metadata_before.get("calculation_phase") != "phase_5":
        raise TargetStopScopeRepairError(f"target/stop {row.id} lacks scope-leak phase marker")
    restored_metadata = {
        key: value for key, value in metadata_before.items() if key != "calculation_phase"
    }
    expected_metadata = {
        **restored_metadata,
        "repair_type": REPAIR_TYPE,
        "incident": incident,
        "source_bad_revision": int(row.revision),
    }
    old_state = _row_state(row)
    return {
        "target_stop_id": int(row.id),
        "prediction_id": int(row.prediction_id),
        "outcome_definition_id": int(row.outcome_definition_id),
        "old_revision": int(row.revision),
        "old_status": row.status,
        "incorrect_forward_outcome_id": int(row.forward_outcome_id or 0),
        "correct_forward_outcome_id": int(correct_forward.id),
        "correct_forward_status": correct_forward.status,
        "correct_forward_revision": int(correct_forward.revision),
        "old_state": old_state,
        "old_state_hash": target_stop_scope_repair_hash(old_state),
        "fields_cleared": [
            "target_hit",
            "stop_hit",
            "first_event",
            "event_session",
            "primary_winner",
            "optimistic_winner",
            "conservative_winner",
            "source_bar_lineage_hash",
            "evaluated_at",
        ],
        "expected_corrective_revision": {
            "revision": int(row.revision) + 1,
            "status": "PENDING",
            "entry_model": row.entry_model,
            "horizon_sessions": int(row.horizon_sessions),
            "forward_outcome_id": int(correct_forward.id),
            "same_bar_conflict": False,
            "metadata_json": expected_metadata,
        },
    }


def _row_state(row: WinnerTargetStopOutcome) -> dict[str, Any]:
    return canonicalize_manifest_value(
        {column.name: getattr(row, column.name) for column in row.__table__.columns}
    )
