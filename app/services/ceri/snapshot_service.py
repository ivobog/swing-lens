from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriScoreSnapshot
from app.services.ceri.confidence_service import ConfidenceResult
from app.services.ceri.config import CeriConfig, load_ceri_config
from app.services.ceri.dtos import ScoreComponent
from app.services.ceri.event_risk_service import EventRiskResult
from app.services.ceri.evidence_state_service import CeriEvidenceLedgerService
from app.services.ceri.opportunity_score_service import OpportunityResult


@dataclass(frozen=True)
class SnapshotReproductionResult:
    stored_hash: str
    reproduced_hash: str
    matches: bool
    differences: tuple[str, ...]


class CeriSnapshotService:
    def __init__(self, config: CeriConfig | None = None) -> None:
        self.config = config or load_ceri_config()

    def build_snapshot(
        self,
        *,
        company_id: int,
        ticker: str,
        as_of_session: date,
        cutoff_at: datetime,
        opportunity: OpportunityResult,
        event_risk: EventRiskResult,
        confidence: ConfidenceResult,
        source_ids: list[int],
        run_id: int | None = None,
        source_run_id_text: str | None = None,
        alignment_inputs: dict[str, bool] | None = None,
        alignment_context: dict[str, Any] | None = None,
        evidence_lineage: dict[str, Any] | None = None,
    ) -> CeriScoreSnapshot:
        posture = derive_posture(
            opportunity_score=opportunity.score,
            event_risk_score=event_risk.score,
            confidence_label=confidence.label.value,
        )
        alignment_flags = derive_alignment_flags(
            alignment_inputs or {},
            event_risk.earnings_proximity.level,
        )
        components = [asdict(component) for component in opportunity.components]
        selected_opportunity_ids = [
            evidence_id
            for component in opportunity.components
            if component.available
            for evidence_id in component.evidence_ids
        ]
        evidence_lineage = CeriEvidenceLedgerService().enrich(
            evidence_lineage or {},
            source_ids=source_ids,
            opportunity_selected_ids=selected_opportunity_ids,
            risk_selected_ids=list(event_risk.selected_event_ids),
        )
        opportunity_ledger = {
            "rated": opportunity.rated,
            "score": opportunity.score,
            "coverage_pct": opportunity.coverage_pct,
            "available_weight": opportunity.available_weight,
            "minimum_required_coverage_pct": opportunity.minimum_required_coverage_pct,
            "reweighted": opportunity.reweighted,
            "unrated_reason": opportunity.unrated_reason,
            "components": components,
            "penalties": list(opportunity.penalties),
        }
        confidence_ledger = {
            "score": confidence.score,
            "label": confidence.label.value,
            "coverage_pct": confidence.coverage_pct,
            "subscores": [asdict(entry) for entry in confidence.ledger],
            "gates": list(confidence.gates),
            "caps": list(confidence.caps),
        }
        event_risk_ledger = {
            "score": event_risk.score,
            "dominant_component": event_risk.dominant_component,
            "components": [asdict(entry) for entry in event_risk.ledger],
            "selected_event_ids": list(event_risk.selected_event_ids),
            "rejected_event_ids": list(event_risk.rejected_event_ids),
            "rejected_events": list(event_risk.rejected_events),
            "penalties": list(event_risk.penalties),
            "accepted_evidence": bool(
                event_risk.earnings_proximity.days_until_earnings is not None
                or event_risk.selected_event_ids
            ),
        }
        earnings_risk = event_risk.earnings_proximity.risk_score
        reasons = [*opportunity.reasons, *event_risk.reasons, *confidence.reasons]
        warnings = [*opportunity.warnings, *event_risk.warnings, *confidence.warnings]
        payload = {
            "company_id": company_id,
            "ticker": ticker,
            "as_of_session": as_of_session.isoformat(),
            "cutoff_at": cutoff_at,
            "opportunity_score": opportunity.score,
            "event_risk_score": event_risk.score,
            "data_confidence": confidence.label.value,
            "coverage_pct": confidence.coverage_pct,
            "posture": posture,
            "components": components,
            "opportunity_ledger": opportunity_ledger,
            "confidence_ledger": confidence_ledger,
            "event_risk_ledger": event_risk_ledger,
            "source_ids": sorted(source_ids),
            "config_hash": self.config.config_hash,
            "calculation_version": self.config.engine.calculation_version,
            "alignment_context": alignment_context or {},
            "evidence_lineage": evidence_lineage,
        }
        snapshot = CeriScoreSnapshot(
            run_id=run_id,
            source_run_id_text=source_run_id_text,
            company_id=company_id,
            ticker=ticker.upper(),
            as_of_session=as_of_session,
            cutoff_at=cutoff_at,
            opportunity_score=opportunity.score,
            opportunity_coverage_pct=opportunity.coverage_pct,
            opportunity_unrated_reason=opportunity.unrated_reason,
            event_risk_score=event_risk.score,
            data_confidence=confidence.label.value,
            coverage_pct=confidence.coverage_pct,
            posture=posture,
            earnings_proximity_risk=earnings_risk,
            alignment_flags_json=alignment_flags,
            alignment_context_json=alignment_context or {},
            evidence_lineage_json=evidence_lineage,
            top_positive_contributors_json=_top_contributors(opportunity.components, positive=True),
            top_negative_contributors_json=_top_contributors(
                opportunity.components,
                positive=False,
            ),
            component_json={
                "components": components,
                "source_ids": sorted(source_ids),
                "earnings_proximity": asdict(event_risk.earnings_proximity),
                "alignment_context": alignment_context or {},
                "evidence_lineage": evidence_lineage,
            },
            opportunity_ledger_json=opportunity_ledger,
            confidence_ledger_json=confidence_ledger,
            event_risk_ledger_json=event_risk_ledger,
            reasons_json=reasons or None,
            warnings_json=warnings or None,
            config_version=self.config.engine.config_version,
            config_hash=self.config.config_hash,
            calculation_version=self.config.engine.calculation_version,
            evidence_hash=score_evidence_hash(payload),
            hash_schema_version="ceri-canonical-json-v2",
        )
        return snapshot

    def persist_snapshot(self, db: Session, snapshot: CeriScoreSnapshot) -> CeriScoreSnapshot:
        db.add(snapshot)
        db.flush()
        return snapshot

    def reproduce_snapshot(self, snapshot: CeriScoreSnapshot) -> SnapshotReproductionResult:
        component_json = snapshot.component_json or {}
        if snapshot.calculation_version == "ceri-1.0.0":
            payload = _legacy_reproduction_payload(snapshot, component_json)
            reproduced = _legacy_evidence_hash(payload)
            differences = () if reproduced == snapshot.evidence_hash else ("evidence_hash",)
            return SnapshotReproductionResult(
                stored_hash=snapshot.evidence_hash,
                reproduced_hash=reproduced,
                matches=reproduced == snapshot.evidence_hash,
                differences=differences,
            )
        payload = {
            "company_id": snapshot.company_id,
            "ticker": snapshot.ticker,
            "as_of_session": snapshot.as_of_session.isoformat(),
            "cutoff_at": snapshot.cutoff_at,
            "opportunity_score": snapshot.opportunity_score,
            "event_risk_score": snapshot.event_risk_score,
            "data_confidence": snapshot.data_confidence,
            "coverage_pct": snapshot.coverage_pct,
            "posture": snapshot.posture,
            "components": component_json.get("components") or [],
            "opportunity_ledger": snapshot.opportunity_ledger_json or {},
            "confidence_ledger": snapshot.confidence_ledger_json or {},
            "event_risk_ledger": snapshot.event_risk_ledger_json or {},
            "source_ids": component_json.get("source_ids") or [],
            "config_hash": snapshot.config_hash,
            "calculation_version": snapshot.calculation_version,
            "alignment_context": snapshot.alignment_context_json or {},
            "evidence_lineage": snapshot.evidence_lineage_json or {},
        }
        reproduced = score_evidence_hash(payload)
        differences = () if reproduced == snapshot.evidence_hash else ("evidence_hash",)
        return SnapshotReproductionResult(
            stored_hash=snapshot.evidence_hash,
            reproduced_hash=reproduced,
            matches=reproduced == snapshot.evidence_hash,
            differences=differences,
        )


def derive_posture(
    *,
    opportunity_score: float | None,
    event_risk_score: float | None,
    confidence_label: str,
) -> str:
    if confidence_label == "Insufficient" or opportunity_score is None:
        return "Unrated"
    if event_risk_score is not None and event_risk_score >= 6.0:
        return "Binary Risk"
    if opportunity_score >= 7.0:
        return "Positive"
    if opportunity_score >= 5.0:
        return "Improving"
    if opportunity_score >= 3.0:
        return "Mixed"
    return "Deteriorating"


def derive_alignment_flags(inputs: dict[str, bool], earnings_level: str) -> dict[str, bool]:
    return {
        "fundamentals": bool(inputs.get("fundamentals")),
        "technicals": bool(inputs.get("technicals")),
        "sector": bool(inputs.get("sector")),
        "regime": bool(inputs.get("regime")),
        "lifecycle": bool(inputs.get("lifecycle")),
        "earnings_clearance": earnings_level in {"clear", "unknown"},
    }


def score_evidence_hash(payload: dict[str, Any]) -> str:
    encoded = canonical_json_dumps(payload)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def canonical_json_dumps(value: Any) -> str:
    return json.dumps(
        _canonical_json_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _canonical_json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return normalized.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, Enum):
        return _canonical_json_value(value.value)
    if isinstance(value, dict):
        return {str(key): _canonical_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_json_value(item) for item in value]
    return value


def _legacy_reproduction_payload(
    snapshot: CeriScoreSnapshot,
    component_json: dict[str, Any],
) -> dict[str, Any]:
    cutoff_at = snapshot.cutoff_at
    if cutoff_at.tzinfo is None:
        cutoff_at = cutoff_at.replace(tzinfo=UTC)
    return {
        "company_id": snapshot.company_id,
        "ticker": snapshot.ticker,
        "as_of_session": snapshot.as_of_session.isoformat(),
        "cutoff_at": cutoff_at.astimezone(UTC).isoformat(),
        "opportunity_score": snapshot.opportunity_score,
        "event_risk_score": snapshot.event_risk_score,
        "data_confidence": snapshot.data_confidence,
        "coverage_pct": snapshot.coverage_pct,
        "posture": snapshot.posture,
        "components": component_json.get("components") or [],
        "source_ids": component_json.get("source_ids") or [],
        "config_hash": snapshot.config_hash,
        "calculation_version": snapshot.calculation_version,
        "alignment_context": snapshot.alignment_context_json or {},
        "evidence_lineage": snapshot.evidence_lineage_json or {},
    }


def _legacy_evidence_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _top_contributors(
    components: tuple[ScoreComponent, ...],
    *,
    positive: bool,
) -> list[dict[str, Any]]:
    rows = []
    for component in components:
        contribution = (
            component.contribution if component.contribution is not None else 0.0
        )
        if positive and contribution <= 0:
            continue
        if not positive and contribution >= 0:
            continue
        rows.append(
            {
                "name": component.name,
                "value": component.value,
                "weight": component.weight,
                "contribution": contribution,
            }
        )
    return sorted(rows, key=lambda row: abs(row["contribution"]), reverse=True)[:3]
