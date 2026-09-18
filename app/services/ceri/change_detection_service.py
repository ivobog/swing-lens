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
from app.services.canonical_evidence import CanonicalEvidenceSerializer
from app.services.ceri.change_semantics import (
    ComparisonState,
    change_dimensions,
    classify_snapshot_comparison,
)
from app.services.ceri.config import CeriConfig
from app.services.ceri.enums import CeriChangeType
from app.services.configuration_delivery import anchored_decision_calculator
from app.services.core_mutation_authority import core_writer_transaction


@dataclass(frozen=True)
class ChangeDetectionResult:
    changes: int
    duplicates: int
    warnings: int = 0
    comparison_state: str = ComparisonState.COMPARABLE.value
    change_ids: tuple[int, ...] = ()

    def as_dict(self) -> dict[str, int | str | list[int]]:
        return {
            "changes": self.changes,
            "duplicates": self.duplicates,
            "warnings": self.warnings,
            "comparison_state": self.comparison_state,
            "change_ids": list(self.change_ids),
        }


class CeriChangeDetectionService:
    def __init__(self, config: CeriConfig | None = None) -> None:
        from app.services.decision_effective_configuration import (
            resolve_ceri_decision_configuration,
        )

        self.effective_configuration = resolve_ceri_decision_configuration(config, family="changes")
        self.config = self.effective_configuration.ceri_decision_config()

    @anchored_decision_calculator
    @core_writer_transaction
    def detect_score_changes(
        self,
        db: Session,
        *,
        current: CeriScoreSnapshot,
        prior: CeriScoreSnapshot | None,
        scope: str = "run_capture",
        comparison_state: ComparisonState | str | None = None,
    ) -> ChangeDetectionResult:
        state = ComparisonState(comparison_state or classify_snapshot_comparison(prior, current))
        if isinstance(db, Session):
            from app.services.ceri.change_authority import validate_score_comparison

            validate_score_comparison(db, current=current, prior=prior, comparison_state=state)
        current.comparison_state = state.value
        current.comparison_snapshot_id = prior.id if prior is not None else None
        if state is not ComparisonState.COMPARABLE:
            return ChangeDetectionResult(
                changes=0,
                duplicates=0,
                comparison_state=state.value,
            )
        changes = duplicates = 0
        change_ids: list[int] = []
        for change_type, delta in self._score_changes(current, prior).items():
            event, created = self._persist_change(
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
                comparison_state=state,
            )
            changes += int(created)
            duplicates += int(not created)
            if event.id is not None:
                change_ids.append(int(event.id))
        return ChangeDetectionResult(
            changes=changes,
            duplicates=duplicates,
            comparison_state=state.value,
            change_ids=tuple(change_ids),
        )

    @anchored_decision_calculator
    @core_writer_transaction
    def detect_catalyst_revision(
        self,
        db: Session,
        *,
        revision: CeriCatalystEventRevision,
        prior_revision: CeriCatalystEventRevision | None = None,
        company_id: int,
        scope: str = "daily_change_feed",
        market_cutoff=None,
    ) -> ChangeDetectionResult:
        if not _catalyst_change_eligible(revision, prior_revision):
            return ChangeDetectionResult(changes=0, duplicates=0)
        change_type = _catalyst_change_type(revision, prior_revision)
        delta = _catalyst_delta(revision, prior_revision)
        event, created = self._persist_change(
            db,
            company_id=company_id,
            change_type=change_type,
            severity="NOTABLE",
            effective_session=revision.effective_session,
            scope=scope,
            catalyst_revision_id=revision.id,
            prior_catalyst_revision_id=prior_revision.id if prior_revision else None,
            market_cutoff=market_cutoff,
            delta=delta,
            config_hash="event_revision",
            calculation_version="ceri-1.0.0",
            comparison_state=ComparisonState.COMPARABLE,
        )
        return ChangeDetectionResult(
            changes=int(created),
            duplicates=int(not created),
            change_ids=(int(event.id),) if event.id is not None else (),
        )

    @anchored_decision_calculator
    @core_writer_transaction
    def detect_guidance_change(
        self,
        db: Session,
        *,
        guidance: Any,
        company_id: int,
        prior_action: str | None = None,
        prior_guidance_event_id: int | None = None,
        scope: str = "daily_change_feed",
        market_cutoff=None,
    ) -> ChangeDetectionResult:
        if getattr(guidance, "accepted_for_scoring", None) is not True:
            return ChangeDetectionResult(changes=0, duplicates=0)
        action_map = {
            "RAISED": CeriChangeType.GUIDANCE_RAISED,
            "LOWERED": CeriChangeType.GUIDANCE_LOWERED,
            "WITHDRAWN": CeriChangeType.GUIDANCE_WITHDRAWN,
        }
        change_type = action_map.get(str(guidance.action))
        if change_type is None or str(guidance.action) == prior_action:
            return ChangeDetectionResult(changes=0, duplicates=0)
        event, created = self._persist_change(
            db,
            company_id=company_id,
            change_type=change_type,
            severity="NOTABLE",
            effective_session=guidance.effective_session,
            scope=scope,
            guidance_event_id=getattr(guidance, "id", None),
            prior_guidance_event_id=prior_guidance_event_id,
            market_cutoff=market_cutoff,
            delta=_guidance_delta(guidance, prior_action),
            config_hash="guidance_event",
            calculation_version="ceri-1.0.0",
            comparison_state=ComparisonState.COMPARABLE,
        )
        return ChangeDetectionResult(
            changes=int(created),
            duplicates=int(not created),
            change_ids=(int(event.id),) if event.id is not None else (),
        )

    def _score_changes(
        self,
        current: CeriScoreSnapshot,
        prior: CeriScoreSnapshot | None,
    ) -> dict[CeriChangeType, dict[str, Any]]:
        if prior is None:
            return {}
        changes: dict[CeriChangeType, dict[str, Any]] = {}
        score_delta = float(self.config.change_thresholds["score_delta"])
        revision_delta = float(self.config.change_thresholds["revision_pct_points"])
        if prior.opportunity_score is None and current.opportunity_score is not None:
            changes[CeriChangeType.BECAME_RATED] = {
                "from": None,
                "to": current.opportunity_score,
                "baseline_only": False,
            }
        elif prior.opportunity_score is not None and current.opportunity_score is None:
            changes[CeriChangeType.BECAME_UNRATED] = {
                "from": prior.opportunity_score,
                "to": None,
                "baseline_only": False,
            }
        elif current.opportunity_score is not None and prior.opportunity_score is not None:
            opportunity_delta = current.opportunity_score - prior.opportunity_score
            upgrade_boundary = float(self.config.change_thresholds["opportunity_upgrade_threshold"])
            if prior.opportunity_score < upgrade_boundary <= current.opportunity_score:
                changes[CeriChangeType.OPPORTUNITY_UPGRADED] = _opportunity_delta(
                    prior, current, opportunity_delta, upgrade_boundary
                )
            elif prior.opportunity_score >= upgrade_boundary > current.opportunity_score:
                changes[CeriChangeType.OPPORTUNITY_DOWNGRADED] = _opportunity_delta(
                    prior, current, opportunity_delta, upgrade_boundary
                )
            elif prior.posture != current.posture:
                changes[CeriChangeType.POSTURE_CHANGED] = _opportunity_delta(
                    prior, current, opportunity_delta, upgrade_boundary
                )
            elif abs(opportunity_delta) >= score_delta:
                changes[CeriChangeType.OPPORTUNITY_CHANGED] = _opportunity_delta(
                    prior, current, opportunity_delta, upgrade_boundary
                )
        if (
            current.event_risk_score is not None
            and prior.event_risk_score is not None
            and _has_accepted_risk_evidence(current)
        ):
            risk_delta = current.event_risk_score - prior.event_risk_score
            if risk_delta >= float(self.config.change_thresholds["risk_escalation_delta"]):
                changes[CeriChangeType.RISK_ESCALATED] = {
                    "delta": risk_delta,
                    "prior_comparable": True,
                    "accepted_evidence": True,
                }
            elif risk_delta <= -float(self.config.change_thresholds["risk_escalation_delta"]):
                changes[CeriChangeType.RISK_DEESCALATED] = {
                    "delta": risk_delta,
                    "prior_comparable": True,
                    "accepted_evidence": True,
                }
        revision_current = _component_value(current, "revision_magnitude")
        revision_prior = _component_value(prior, "revision_magnitude")
        if revision_current is not None and revision_prior is not None:
            delta = revision_current - revision_prior
            if delta >= revision_delta:
                changes[CeriChangeType.REVISION_UP] = {"delta": delta}
            elif delta <= -revision_delta:
                changes[CeriChangeType.REVISION_DOWN] = {"delta": delta}
        acceleration_current = _component_value(current, "revision_acceleration")
        acceleration_prior = _component_value(prior, "revision_acceleration")
        if acceleration_current is not None and acceleration_prior is not None:
            acceleration_delta = acceleration_current - acceleration_prior
            threshold = float(self.config.change_thresholds.get("acceleration_delta", 0.01))
            if acceleration_delta >= threshold:
                changes[CeriChangeType.REVISION_ACCELERATED] = {"delta": acceleration_delta}
            elif acceleration_delta <= -threshold:
                changes[CeriChangeType.REVISION_DECELERATED] = {"delta": acceleration_delta}
        if _has_stale_warning(current) and not _has_stale_warning(prior):
            changes[CeriChangeType.DATA_STALE] = {
                "warnings": current.warnings_json,
                "freshness": (current.confidence_ledger_json or {}).get("freshness") or {},
            }
        if not _has_stale_warning(current) and _has_stale_warning(prior):
            changes[CeriChangeType.DATA_REFRESHED] = {
                "prior_warnings": prior.warnings_json,
                "freshness": (current.confidence_ledger_json or {}).get("freshness") or {},
                "prior_freshness": (prior.confidence_ledger_json or {}).get("freshness") or {},
            }
        current_conflicts = _has_conflict_warning(current)
        prior_conflicts = _has_conflict_warning(prior)
        if current_conflicts and not prior_conflicts:
            changes[CeriChangeType.CONFLICT_OPENED] = {"warnings": current.warnings_json}
        elif prior_conflicts and not current_conflicts:
            changes[CeriChangeType.CONFLICT_RESOLVED] = {"prior_warnings": prior.warnings_json}
        return changes

    @anchored_decision_calculator
    @core_writer_transaction
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
        guidance_event_id: int | None = None,
        comparison_state: ComparisonState | str = ComparisonState.COMPARABLE,
        prior_catalyst_revision_id: int | None = None,
        prior_guidance_event_id: int | None = None,
        market_cutoff=None,
    ) -> tuple[CeriChangeEvent, bool]:
        proof = None
        if isinstance(db, Session):
            from app.services.ceri.change_authority import validate_score_change
            from app.services.decision_mutation_authority import lock_decision_scope

            lock_decision_scope(
                db,
                (
                    "ceri-change",
                    company_id,
                    from_snapshot_id,
                    to_snapshot_id,
                    catalyst_revision_id,
                    guidance_event_id,
                    change_type.value,
                ),
            )
            if catalyst_revision_id is not None or guidance_event_id is not None:
                from app.services.ceri.change_authority import validate_normalized_change

                proof = validate_normalized_change(
                    db,
                    self,
                    company_id=company_id,
                    change_type=change_type,
                    effective_session=effective_session,
                    delta=delta,
                    config_hash=config_hash,
                    calculation_version=calculation_version,
                    catalyst_revision_id=catalyst_revision_id,
                    guidance_event_id=guidance_event_id,
                    prior_catalyst_revision_id=prior_catalyst_revision_id,
                    prior_guidance_event_id=prior_guidance_event_id,
                    market_cutoff=market_cutoff,
                    comparison_state=ComparisonState(comparison_state),
                )
                if from_snapshot_id is not None or to_snapshot_id is not None:
                    raise ValueError("MUTATION_CERI_CHANGE_MIXED_SOURCE_KINDS")
            else:
                proof = validate_score_change(
                    db,
                    self,
                    company_id=company_id,
                    change_type=change_type,
                    effective_session=effective_session,
                    delta=delta,
                    config_hash=config_hash,
                    calculation_version=calculation_version,
                    from_snapshot_id=from_snapshot_id,
                    to_snapshot_id=to_snapshot_id,
                    comparison_state=ComparisonState(comparison_state),
                )
        importance, signal_class = change_dimensions(change_type, delta)
        dedup_key = change_dedup_key(
            company_id=company_id,
            change_type=change_type.value,
            effective_session=effective_session,
            scope=scope,
            from_snapshot_id=from_snapshot_id,
            to_snapshot_id=to_snapshot_id,
            catalyst_revision_id=catalyst_revision_id,
            guidance_event_id=guidance_event_id,
            config_hash=config_hash,
            calculation_version=calculation_version,
            effective_configuration_hash=self.effective_configuration.snapshot.semantic_hash,
        )
        existing = _maybe_scalar(
            db,
            select(CeriChangeEvent).where(CeriChangeEvent.dedup_key == dedup_key),
        )
        configuration_payload = self.effective_configuration.snapshot.as_dict()
        event = CeriChangeEvent(
            company_id=company_id,
            from_snapshot_id=from_snapshot_id,
            to_snapshot_id=to_snapshot_id,
            catalyst_revision_id=catalyst_revision_id,
            guidance_event_id=guidance_event_id,
            change_type=change_type.value,
            severity=importance.value,
            importance=importance.value,
            signal_class=signal_class.value,
            comparison_state=ComparisonState(comparison_state).value,
            delta_json={
                **delta,
                "effective_configuration_at_creation": configuration_payload,
            },
            dedup_key=dedup_key,
        )
        if proof is not None:
            event.delta_json["native_change_proof"] = CanonicalEvidenceSerializer.canonicalize(
                proof
            )
        if existing is not None:
            if isinstance(db, Session):
                from app.services.ceri.change_authority import change_body

                if change_body(existing) != change_body(event):
                    raise ValueError("MUTATION_CERI_CHANGE_ALTERED_RETRY")
            return existing, False
        db.add(event)
        db.flush()
        return event, True


def change_dedup_key(**parts: Any) -> str:
    # Scope describes which orchestration path found the change; it is not
    # part of the business identity. Capture and standalone rebuild stages can
    # observe the same transition and must converge on one durable event.
    canonical_parts = {key: value for key, value in parts.items() if key != "scope"}
    encoded = "|".join(f"{key}={canonical_parts[key]}" for key in sorted(canonical_parts))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _component_value(snapshot: CeriScoreSnapshot, name: str) -> float | None:
    for component in (snapshot.component_json or {}).get("components") or []:
        if component.get("name") == name and component.get("value") is not None:
            return float(component["value"])
    return None


def _has_conflict_warning(snapshot: CeriScoreSnapshot | None) -> bool:
    if snapshot is None:
        return False
    values = [str(value).lower() for value in (snapshot.warnings_json or [])]
    return any("conflict" in value for value in values)


def _has_stale_warning(snapshot: CeriScoreSnapshot | None) -> bool:
    if snapshot is None:
        return False
    values = [str(value).lower() for value in (snapshot.warnings_json or [])]
    return any("stale" in value for value in values)


def _has_accepted_risk_evidence(snapshot: CeriScoreSnapshot) -> bool:
    ledger = snapshot.event_risk_ledger_json or {}
    if ledger.get("accepted_evidence") is True:
        return True
    return bool(ledger.get("accepted_evidence_ids") or ledger.get("selected_event_ids"))


def _severity(delta: dict[str, Any]) -> str:
    raw = delta.get("delta")
    if raw is None:
        raw = delta.get("to")
    value = abs(float(raw)) if raw is not None else 0.0
    return "IMPORTANT" if value >= 3.0 else "NOTABLE"


def _catalyst_change_type(
    revision: CeriCatalystEventRevision,
    prior_revision: CeriCatalystEventRevision | None,
) -> CeriChangeType:
    if prior_revision is None:
        if revision.status == "SCHEDULED" and revision.date_confidence != "EXACT_TIMESTAMP":
            return CeriChangeType.NEW_BINARY_EVENT
        return CeriChangeType.NEW_CATALYST
    status_map = {
        "COMPLETED": CeriChangeType.EVENT_COMPLETED,
        "DELAYED": CeriChangeType.CATALYST_DELAYED,
        "CANCELLED": CeriChangeType.EVENT_CANCELLED,
        "OUTCOME_KNOWN": CeriChangeType.EVENT_RESOLVED,
        "RESOLVED": CeriChangeType.EVENT_RESOLVED,
    }
    return status_map.get(revision.status, CeriChangeType.CATALYST_UPDATED)


def _catalyst_change_eligible(
    revision: CeriCatalystEventRevision,
    prior_revision: CeriCatalystEventRevision | None,
) -> bool:
    if revision.is_current is False or revision.review_state == "REJECTED":
        return False
    if revision.issuer_relevance is not True and (
        prior_revision is None or prior_revision.issuer_relevance is not True
    ):
        return False
    if prior_revision is None and revision.status in {
        "COMPLETED",
        "CANCELLED",
        "OUTCOME_KNOWN",
        "RESOLVED",
    }:
        return False
    if prior_revision is None:
        return revision.binary_eligible is True or revision.materiality is not None
    return bool(
        revision.binary_eligible is True
        or revision.materiality is not None
        or prior_revision.binary_eligible is True
        or prior_revision.materiality is not None
    )


def _opportunity_delta(
    prior: CeriScoreSnapshot,
    current: CeriScoreSnapshot,
    delta: float,
    boundary: float,
) -> dict[str, Any]:
    return {
        "from": prior.opportunity_score,
        "to": current.opportunity_score,
        "delta": delta,
        "from_posture": prior.posture,
        "to_posture": current.posture,
        "from_coverage_pct": prior.opportunity_coverage_pct,
        "to_coverage_pct": current.opportunity_coverage_pct,
        "from_confidence": prior.data_confidence,
        "to_confidence": current.data_confidence,
        "upgrade_boundary": boundary,
    }


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _maybe_scalar(db: Session, statement):
    scalar = getattr(db, "scalar", None)
    if callable(scalar):
        return scalar(statement)
    return None


def _catalyst_delta(revision, prior_revision):
    return {
        "canonical_event_id": revision.catalyst_event_id,
        "event_revision_id": revision.id,
        "status": revision.status,
        "prior_status": prior_revision.status if prior_revision is not None else None,
        "direction": revision.direction,
        "materiality": revision.materiality,
        "confidence": revision.source_confidence,
        "announced_at": revision.announced_at.isoformat()
        if revision.announced_at is not None
        else None,
        "effective_session": revision.effective_session.isoformat()
        if revision.effective_session is not None
        else None,
        "expected_date": revision.expected_date.isoformat()
        if revision.expected_date is not None
        else None,
        "issuer_relevance": revision.issuer_relevance,
        "binary_eligible": revision.binary_eligible,
        "eligibility_reason": revision.relevance_reason,
    }


def _guidance_delta(guidance, prior_action):
    return {
        "guidance_event_id": getattr(guidance, "id", None),
        "action": guidance.action,
        "prior_action": prior_action,
        "metric": getattr(guidance, "metric", None),
        "period": getattr(guidance, "period_type", None),
        "low": _json_value(getattr(guidance, "low_value", None)),
        "high": _json_value(getattr(guidance, "high_value", None)),
        "point": _json_value(getattr(guidance, "point_value", None)),
        "confidence": getattr(guidance, "confidence", None),
        "accepted_for_scoring": True,
    }
