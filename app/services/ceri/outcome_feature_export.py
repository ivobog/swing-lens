from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.models.ceri_tables import CeriScoreSnapshot


@dataclass(frozen=True)
class OutcomeFeatureExportResult:
    rows: list[dict[str, Any]]


class CeriOutcomeFeatureExportService:
    def export_snapshots(
        self,
        *,
        snapshots: list[CeriScoreSnapshot],
        cutoff_at: datetime,
    ) -> OutcomeFeatureExportResult:
        rows = []
        for snapshot in snapshots:
            if snapshot.cutoff_at > cutoff_at:
                continue
            rows.append(
                {
                    "ticker": snapshot.ticker,
                    "company_id": snapshot.company_id,
                    "as_of_session": snapshot.as_of_session,
                    "cutoff_at": snapshot.cutoff_at,
                    "opportunity_score": snapshot.opportunity_score,
                    "event_risk_score": snapshot.event_risk_score,
                    "confidence": snapshot.data_confidence,
                    "posture": snapshot.posture,
                    "alignment": snapshot.alignment_flags_json,
                    "component_json": snapshot.component_json,
                    "source_ids": (snapshot.component_json or {}).get("source_ids") or [],
                    "config_hash": snapshot.config_hash,
                    "calculation_version": snapshot.calculation_version,
                    "evidence_hash": snapshot.evidence_hash,
                }
            )
        return OutcomeFeatureExportResult(rows=rows)
