from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ceri_tables import (
    CeriCatalystEventRevision,
    CeriChangeEvent,
    CeriScoreSnapshot,
)
from app.services.ceri.config import CeriConfig, load_ceri_config
from app.services.ceri.enums import CeriChangeType


@dataclass(frozen=True)
class ChangeDetectionResult:
    changes: int
    duplicates: int
    warnings: int = 0

    def as_dict(self) -> dict[str, int]:
        return {"changes": self.changes, "duplicates": self.duplicates, "warnings": self.warnings}


class CeriChangeDetectionService:
    def __init__(self, config: CeriConfig | None = None) -> None:
        self.config = config or load_ceri_config()

    def detect_score_changes(
        self,
        db: Session,
        *,
        current: CeriScoreSnapshot,
        prior: CeriScoreSnapshot | None,
        scope: str = "run_capture",
    ) -> ChangeDetectionResult:
        changes = duplicates = 0
        for change_type, delta in self._score_changes(current, prior).items():
            event = self._persist_change(
                db,
                company_id=current.company_id,
                change_type=change_type,
                severity=_severity(delta),
                effective_session=current.as_of_session,
                scope=scope,
                from_snapshot_id=prior.id if prior is not None else None,
                to_snapshot_id=current.id,
                delta=delta,
                config_hash=current.config_hash,
                calculation_version=current.calculation_version,
            )
            changes += int(event is not None)
            duplicates += int(event is None)
        return ChangeDetectionResult(changes=changes, duplicates=duplicates)

    def detect_catalyst_revision(
        self,
        db: Session,
        *,
        revision: CeriCatalystEventRevision,
        prior_revision: CeriCatalystEventRevision | None = None,
        company_id: int,
        scope: str = "daily_change_feed",
    ) -> ChangeDetectionResult:
        change_type = _catalyst_change_type(revision, prior_revision)
        event = self._persist_change(
            db,
            company_id=company_id,
            change_type=change_type,
            severity="RISK" if change_type is CeriChangeType.NEW_BINARY_EVENT else "NOTABLE",
            effective_session=revision.effective_session,
            scope=scope,
            catalyst_revision_id=revision.id,
            delta={
                "status": revision.status,
                "prior_status": prior_revision.status if prior_revision is not None else None,
            },
            config_hash="event_revision",
            calculation_version="ceri-1.0.0",
        )
        return ChangeDetectionResult(changes=int(event is not None), duplicates=int(event is None))

    def _score_changes(
        self,
        current: CeriScoreSnapshot,
        prior: CeriScoreSnapshot | None,
    ) -> dict[CeriChangeType, dict[str, Any]]:
        if prior is None:
            return {
                CeriChangeType.OPPORTUNITY_UPGRADED: {
                    "from": None,
                    "to": current.opportunity_score,
                }
            }
        changes: dict[CeriChangeType, dict[str, Any]] = {}
        score_delta = float(self.config.change_thresholds["score_delta"])
        revision_delta = float(self.config.change_thresholds["revision_pct_points"])
        opportunity_delta = (current.opportunity_score or 0.0) - (prior.opportunity_score or 0.0)
        risk_delta = (current.event_risk_score or 0.0) - (prior.event_risk_score or 0.0)
        if opportunity_delta >= score_delta:
            changes[CeriChangeType.OPPORTUNITY_UPGRADED] = {"delta": opportunity_delta}
        elif opportunity_delta <= -score_delta:
            changes[CeriChangeType.OPPORTUNITY_DOWNGRADED] = {"delta": opportunity_delta}
        if risk_delta >= float(self.config.change_thresholds["risk_escalation_delta"]):
            changes[CeriChangeType.RISK_ESCALATED] = {"delta": risk_delta}
        elif risk_delta <= -float(self.config.change_thresholds["risk_escalation_delta"]):
            changes[CeriChangeType.RISK_DEESCALATED] = {"delta": risk_delta}
        revision_current = _component_value(current, "revision_magnitude")
        revision_prior = _component_value(prior, "revision_magnitude")
        if revision_current is not None and revision_prior is not None:
            delta = revision_current - revision_prior
            if delta >= revision_delta:
                changes[CeriChangeType.REVISION_UP] = {"delta": delta}
            elif delta <= -revision_delta:
                changes[CeriChangeType.REVISION_DOWN] = {"delta": delta}
        if (current.warnings_json or []) and not (prior.warnings_json or []):
            changes[CeriChangeType.DATA_STALE] = {"warnings": current.warnings_json}
        if not (current.warnings_json or []) and (prior.warnings_json or []):
            changes[CeriChangeType.DATA_REFRESHED] = {"prior_warnings": prior.warnings_json}
        return changes

    def _persist_change(
        self,
        db: Session,
        *,
        company_id: int,
        change_type: CeriChangeType,
        severity: str,
        effective_session: date | None,
        scope: str,
        delta: dict[str, Any],
        config_hash: str,
        calculation_version: str,
        from_snapshot_id: int | None = None,
        to_snapshot_id: int | None = None,
        catalyst_revision_id: int | None = None,
    ) -> CeriChangeEvent | None:
        dedup_key = change_dedup_key(
            company_id=company_id,
            change_type=change_type.value,
            effective_session=effective_session,
            scope=scope,
            from_snapshot_id=from_snapshot_id,
            to_snapshot_id=to_snapshot_id,
            catalyst_revision_id=catalyst_revision_id,
            config_hash=config_hash,
            calculation_version=calculation_version,
        )
        existing = _maybe_scalar(
            db,
            select(CeriChangeEvent).where(CeriChangeEvent.dedup_key == dedup_key),
        )
        if existing is not None:
            return None
        event = CeriChangeEvent(
            company_id=company_id,
            from_snapshot_id=from_snapshot_id,
            to_snapshot_id=to_snapshot_id,
            catalyst_revision_id=catalyst_revision_id,
            change_type=change_type.value,
            severity=severity,
            delta_json=delta,
            dedup_key=dedup_key,
        )
        db.add(event)
        db.flush()
        return event


def change_dedup_key(**parts: Any) -> str:
    encoded = "|".join(f"{key}={parts[key]}" for key in sorted(parts))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _component_value(snapshot: CeriScoreSnapshot, name: str) -> float | None:
    for component in (snapshot.component_json or {}).get("components") or []:
        if component.get("name") == name and component.get("value") is not None:
            return float(component["value"])
    return None


def _severity(delta: dict[str, Any]) -> str:
    value = abs(float(delta.get("delta") or delta.get("to") or 0.0))
    return "RISK" if value >= 3.0 else "NOTABLE"


def _catalyst_change_type(
    revision: CeriCatalystEventRevision,
    prior_revision: CeriCatalystEventRevision | None,
) -> CeriChangeType:
    if prior_revision is None:
        if revision.status == "SCHEDULED" and revision.date_confidence != "EXACT_TIMESTAMP":
            return CeriChangeType.NEW_BINARY_EVENT
        return CeriChangeType.NEW_CATALYST
    status_map = {
        "COMPLETED": CeriChangeType.CATALYST_CONFIRMED,
        "DELAYED": CeriChangeType.CATALYST_DELAYED,
        "CANCELLED": CeriChangeType.CATALYST_CANCELLED,
        "OUTCOME_KNOWN": CeriChangeType.CATALYST_RESOLVED,
    }
    return status_map.get(revision.status, CeriChangeType.CATALYST_UPDATED)


def _maybe_scalar(db: Session, statement):
    scalar = getattr(db, "scalar", None)
    if callable(scalar):
        return scalar(statement)
    return None
