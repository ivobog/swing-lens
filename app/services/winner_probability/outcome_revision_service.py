from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.tables import WinnerForwardOutcome, WinnerTargetStopOutcome


class OutcomeRevisionService:
    def upsert_forward_revision(
        self,
        db: Session,
        current: WinnerForwardOutcome,
        values: dict[str, Any],
        *,
        now: datetime,
    ) -> tuple[WinnerForwardOutcome, bool]:
        lineage_hash = values.get("source_bar_lineage_hash")
        if current.source_bar_lineage_hash == lineage_hash and current.matured_at is not None:
            return current, False
        if current.source_bar_lineage_hash is None or current.matured_at is None:
            _assign(current, values)
            db.flush()
            return current, True

        current.is_current_revision = False
        current.superseded_at = now
        revision = WinnerForwardOutcome(
            prediction_id=current.prediction_id,
            entry_model=current.entry_model,
            horizon_sessions=current.horizon_sessions,
            entry_session=current.entry_session,
            due_session=current.due_session,
            revision=current.revision + 1,
            is_current_revision=True,
            metadata_json={**(current.metadata_json or {}), "revised_from_id": current.id},
        )
        _assign(revision, values)
        db.add(revision)
        db.flush()
        return revision, True

    def upsert_target_stop_revision(
        self,
        db: Session,
        current: WinnerTargetStopOutcome,
        values: dict[str, Any],
        *,
        now: datetime,
    ) -> tuple[WinnerTargetStopOutcome, bool]:
        lineage_hash = values.get("source_bar_lineage_hash")
        if current.source_bar_lineage_hash == lineage_hash and current.evaluated_at is not None:
            return current, False
        if current.source_bar_lineage_hash is None or current.evaluated_at is None:
            _assign(current, values)
            db.flush()
            return current, True

        current.is_current_revision = False
        current.superseded_at = now
        revision = WinnerTargetStopOutcome(
            prediction_id=current.prediction_id,
            outcome_definition_id=current.outcome_definition_id,
            entry_model=current.entry_model,
            horizon_sessions=current.horizon_sessions,
            target_pct=current.target_pct,
            stop_pct=current.stop_pct,
            revision=current.revision + 1,
            is_current_revision=True,
            metadata_json={**(current.metadata_json or {}), "revised_from_id": current.id},
        )
        _assign(revision, values)
        db.add(revision)
        db.flush()
        return revision, True


def _assign(row: object, values: dict[str, Any]) -> None:
    for key, value in values.items():
        setattr(row, key, value)
