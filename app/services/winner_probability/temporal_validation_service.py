from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import WinnerPredictionSnapshot, WinnerTemporalValidityDecision
from app.services.us_market_calendar import us_market_session
from app.services.winner_probability.temporal_integrity import validate_next_open_timing
from app.services.winner_probability.temporal_manifest_canonicalization import (
    canonical_manifest_bytes,
    canonicalize_temporal_metadata,
)


@dataclass(frozen=True)
class TemporalQuarantineItem:
    prediction_id: int
    decision_at: datetime
    entry_session: date
    semantic_input_time_valid: bool | None
    incident_reason: str
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class TemporalQuarantinePlan:
    manifest_hash: str
    item_count: int
    invalid_count: int
    items: tuple[TemporalQuarantineItem, ...]


@dataclass(frozen=True)
class TemporalQuarantineResult:
    manifest_hash: str
    inserted_count: int
    decision_ids: tuple[int, ...]


class TemporalValidationService:
    """Append temporal certification or quarantine events without rewriting history."""

    def record(
        self,
        db: Session,
        *,
        prediction: WinnerPredictionSnapshot,
        decision_at: datetime,
        entry_session,
        semantic_input_time_valid: bool | None,
        evaluated_by: str,
        evaluated_at: datetime | None = None,
        metadata: dict | None = None,
    ) -> WinnerTemporalValidityDecision:
        session = us_market_session(entry_session)
        if session is None:
            raise ValueError("entry_session must be a valid US market session")
        db.execute(
            select(WinnerPredictionSnapshot.id)
            .where(WinnerPredictionSnapshot.id == prediction.id)
            .with_for_update()
        )
        sequence = (
            int(
                db.scalar(
                    select(func.max(WinnerTemporalValidityDecision.validation_sequence)).where(
                        WinnerTemporalValidityDecision.prediction_id == prediction.id
                    )
                )
                or 0
            )
            + 1
        )
        result = validate_next_open_timing(
            decision_at,
            session.open_at,
            source_data_cutoff_at=prediction.source_data_cutoff_at,
            semantic_input_time_valid=semantic_input_time_valid,
        )
        row = WinnerTemporalValidityDecision(
            prediction_id=prediction.id,
            validation_sequence=sequence,
            status=result.status,
            entry_timing_valid=result.entry_timing_valid,
            source_cutoff_valid=result.source_cutoff_valid,
            semantic_input_time_valid=result.semantic_input_time_valid,
            evidence_eligible=result.evidence_eligible,
            reason_codes_json=list(result.reason_codes),
            validation_version=result.validation_version,
            decision_at=decision_at,
            entry_session=entry_session,
            entry_open_at=session.open_at,
            evaluated_at=evaluated_at or datetime.now(UTC),
            evaluated_by=evaluated_by,
            metadata_json=canonicalize_temporal_metadata(metadata or {}),
        )
        db.add(row)
        db.flush()
        return row

    def plan_quarantine(
        self,
        db: Session,
        *,
        items: tuple[TemporalQuarantineItem, ...],
    ) -> TemporalQuarantinePlan:
        ordered = tuple(sorted(items, key=lambda item: item.prediction_id))
        if len({item.prediction_id for item in ordered}) != len(ordered):
            raise ValueError("quarantine manifest contains duplicate prediction ids")
        predictions = {
            int(row.id): row
            for row in db.scalars(
                select(WinnerPredictionSnapshot).where(
                    WinnerPredictionSnapshot.id.in_([item.prediction_id for item in ordered])
                )
            )
        }
        missing = [item.prediction_id for item in ordered if item.prediction_id not in predictions]
        if missing:
            raise ValueError(f"quarantine predictions not found: {missing[:10]}")
        invalid_count = 0
        for item in ordered:
            schedule = us_market_session(item.entry_session)
            if schedule is None:
                raise ValueError(f"invalid entry session for prediction {item.prediction_id}")
            result = validate_next_open_timing(
                item.decision_at,
                schedule.open_at,
                source_data_cutoff_at=predictions[item.prediction_id].source_data_cutoff_at,
                semantic_input_time_valid=item.semantic_input_time_valid,
            )
            invalid_count += int(not result.evidence_eligible)
        return TemporalQuarantinePlan(
            manifest_hash=_quarantine_manifest_hash(ordered),
            item_count=len(ordered),
            invalid_count=invalid_count,
            items=ordered,
        )

    def apply_quarantine(
        self,
        db: Session,
        *,
        plan: TemporalQuarantinePlan,
        expected_manifest_hash: str,
        actor: str,
        request_key: str,
        approve_write: bool,
        evaluated_at: datetime | None = None,
    ) -> TemporalQuarantineResult:
        """Append a reviewed manifest; never edits predictions, outcomes, or evidence."""
        if not approve_write:
            raise PermissionError("explicit approve_write=True is required")
        if not actor.strip() or not request_key.strip():
            raise ValueError("actor and request_key are required")
        if plan.manifest_hash != expected_manifest_hash:
            raise ValueError("quarantine manifest hash differs from the reviewed plan")
        verified = self.plan_quarantine(db, items=plan.items)
        if verified.manifest_hash != plan.manifest_hash:
            raise ValueError("quarantine plan changed before application")

        ids = [item.prediction_id for item in plan.items]
        predictions = {
            int(row.id): row
            for row in db.scalars(
                select(WinnerPredictionSnapshot)
                .where(WinnerPredictionSnapshot.id.in_(ids))
                .order_by(WinnerPredictionSnapshot.id)
                .with_for_update()
            )
        }
        latest_sequences = {
            int(prediction_id): int(sequence)
            for prediction_id, sequence in db.execute(
                select(
                    WinnerTemporalValidityDecision.prediction_id,
                    func.max(WinnerTemporalValidityDecision.validation_sequence),
                )
                .where(WinnerTemporalValidityDecision.prediction_id.in_(ids))
                .group_by(WinnerTemporalValidityDecision.prediction_id)
            )
        }
        timestamp = evaluated_at or datetime.now(UTC)
        rows: list[WinnerTemporalValidityDecision] = []
        for item in plan.items:
            prediction = predictions[item.prediction_id]
            schedule = us_market_session(item.entry_session)
            if schedule is None:
                raise ValueError(f"invalid entry session for prediction {item.prediction_id}")
            result = validate_next_open_timing(
                item.decision_at,
                schedule.open_at,
                source_data_cutoff_at=prediction.source_data_cutoff_at,
                semantic_input_time_valid=item.semantic_input_time_valid,
            )
            reasons = tuple(dict.fromkeys((*result.reason_codes, item.incident_reason)))
            rows.append(
                WinnerTemporalValidityDecision(
                    prediction_id=item.prediction_id,
                    validation_sequence=int(latest_sequences.get(item.prediction_id) or 0) + 1,
                    status=result.status,
                    entry_timing_valid=result.entry_timing_valid,
                    source_cutoff_valid=result.source_cutoff_valid,
                    semantic_input_time_valid=result.semantic_input_time_valid,
                    evidence_eligible=result.evidence_eligible,
                    reason_codes_json=list(reasons),
                    validation_version=result.validation_version,
                    decision_at=item.decision_at,
                    entry_session=item.entry_session,
                    entry_open_at=schedule.open_at,
                    evaluated_at=timestamp,
                    evaluated_by=actor,
                    metadata_json=canonicalize_temporal_metadata(
                        {
                            **dict(item.metadata or {}),
                            "request_key": request_key,
                            "manifest_hash": plan.manifest_hash,
                        }
                    ),
                )
            )
        db.add_all(rows)
        db.flush()
        return TemporalQuarantineResult(
            manifest_hash=plan.manifest_hash,
            inserted_count=len(rows),
            decision_ids=tuple(int(row.id) for row in rows),
        )


def _quarantine_manifest_hash(items: tuple[TemporalQuarantineItem, ...]) -> str:
    payload = [
        {
            "prediction_id": item.prediction_id,
            "decision_at": item.decision_at,
            "entry_session": item.entry_session,
            "semantic_input_time_valid": item.semantic_input_time_valid,
            "incident_reason": item.incident_reason,
            "metadata": item.metadata or {},
        }
        for item in items
    ]
    return hashlib.sha256(canonical_manifest_bytes(payload)).hexdigest()
