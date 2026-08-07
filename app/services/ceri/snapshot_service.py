from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriScoreSnapshot
from app.services.ceri.confidence_service import ConfidenceResult
from app.services.ceri.config import CeriConfig, load_ceri_config
from app.services.ceri.dtos import ScoreComponent
from app.services.ceri.event_risk_service import EventRiskResult
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
        earnings_risk = event_risk.earnings_proximity.risk_score
        reasons = [*opportunity.reasons, *event_risk.reasons, *confidence.reasons]
        warnings = [*opportunity.warnings, *event_risk.warnings, *confidence.warnings]
        payload = {
            "company_id": company_id,
            "ticker": ticker,
            "as_of_session": as_of_session.isoformat(),
            "cutoff_at": cutoff_at.isoformat(),
            "opportunity_score": opportunity.score,
            "event_risk_score": event_risk.score,
            "data_confidence": confidence.label.value,
            "coverage_pct": confidence.coverage_pct,
            "posture": posture,
            "components": components,
            "source_ids": sorted(source_ids),
            "config_hash": self.config.config_hash,
            "calculation_version": self.config.engine.calculation_version,
            "alignment_context": alignment_context or {},
            "evidence_lineage": evidence_lineage or {},
        }
        snapshot = CeriScoreSnapshot(
            run_id=run_id,
            source_run_id_text=source_run_id_text,
            company_id=company_id,
            ticker=ticker.upper(),
            as_of_session=as_of_session,
            cutoff_at=cutoff_at,
            opportunity_score=opportunity.score,
            event_risk_score=event_risk.score,
            data_confidence=confidence.label.value,
            coverage_pct=confidence.coverage_pct,
            posture=posture,
            earnings_proximity_risk=earnings_risk,
            alignment_flags_json=alignment_flags,
            alignment_context_json=alignment_context or {},
            evidence_lineage_json=evidence_lineage or {},
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
                "evidence_lineage": evidence_lineage or {},
            },
            reasons_json=reasons or None,
            warnings_json=warnings or None,
            config_version=self.config.engine.config_version,
            config_hash=self.config.config_hash,
            calculation_version=self.config.engine.calculation_version,
            evidence_hash=score_evidence_hash(payload),
        )
        return snapshot

    def persist_snapshot(self, db: Session, snapshot: CeriScoreSnapshot) -> CeriScoreSnapshot:
        db.add(snapshot)
        db.flush()
        return snapshot

    def reproduce_snapshot(self, snapshot: CeriScoreSnapshot) -> SnapshotReproductionResult:
        component_json = snapshot.component_json or {}
        payload = {
            "company_id": snapshot.company_id,
            "ticker": snapshot.ticker,
            "as_of_session": snapshot.as_of_session.isoformat(),
            "cutoff_at": snapshot.cutoff_at.isoformat(),
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
