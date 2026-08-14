from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import (
    WinnerEstimateEvidenceMember,
    WinnerEvidenceManifest,
    WinnerProbabilityEstimate,
)
from app.services.winner_probability.evidence_service import EvidenceOutcome


@dataclass(frozen=True)
class EvidenceManifestResult:
    manifest: WinnerEvidenceManifest
    manifest_hash: str
    payload: dict[str, Any]


class EvidenceManifestService:
    def create_or_get_manifest(
        self,
        db: Session,
        *,
        evidence: tuple[EvidenceOutcome, ...],
        hash_algorithm: str = "sha256",
    ) -> EvidenceManifestResult:
        payload = _manifest_payload(evidence)
        manifest_hash = _hash_payload(payload)
        existing = db.scalar(
            select(WinnerEvidenceManifest).where(
                WinnerEvidenceManifest.manifest_hash == manifest_hash
            )
        )
        if existing is not None:
            return EvidenceManifestResult(
                manifest=existing,
                manifest_hash=manifest_hash,
                payload=payload,
            )
        manifest = WinnerEvidenceManifest(
            manifest_hash=manifest_hash,
            hash_algorithm=hash_algorithm,
            content_encoding="json",
            member_count=len(evidence),
            payload_json=payload,
        )
        db.add(manifest)
        db.flush()
        return EvidenceManifestResult(
            manifest=manifest,
            manifest_hash=manifest_hash,
            payload=payload,
        )

    def persist_members(
        self,
        db: Session,
        *,
        estimate: WinnerProbabilityEstimate,
        evidence: tuple[EvidenceOutcome, ...],
        included_as_of: datetime,
        inclusion_cutoff_at: datetime,
    ) -> None:
        for row in evidence:
            exists = db.scalar(
                select(WinnerEstimateEvidenceMember.id)
                .where(WinnerEstimateEvidenceMember.estimate_id == estimate.id)
                .where(WinnerEstimateEvidenceMember.outcome_id == row.forward_outcome.id)
                .where(
                    WinnerEstimateEvidenceMember.outcome_revision == row.forward_outcome.revision
                )
            )
            if exists is not None:
                continue
            db.add(
                WinnerEstimateEvidenceMember(
                    estimate_id=estimate.id,
                    prediction_id=row.prediction.id,
                    outcome_id=row.forward_outcome.id,
                    outcome_revision=row.forward_outcome.revision,
                    eligibility_decision_id=row.eligibility_decision_id,
                    outcome_replay_id=row.outcome_replay_id,
                    evidence_origin=row.evidence_origin,
                    episode_id=row.prediction.episode_id,
                    inclusion_weight=Decimal(str(row.inclusion_weight)),
                    included_as_of=included_as_of,
                    inclusion_cutoff_at=inclusion_cutoff_at,
                    metadata_json={
                        "target_stop_outcome_id": row.target_stop_outcome.id,
                        "target_stop_revision": row.target_stop_outcome.revision,
                        "eligibility_decision_id": row.eligibility_decision_id,
                        "outcome_replay_id": row.outcome_replay_id,
                        "evidence_origin": row.evidence_origin,
                    },
                )
            )
        db.flush()


def _manifest_payload(evidence: tuple[EvidenceOutcome, ...]) -> dict[str, Any]:
    return {
        "members": [
            {
                "prediction_id": row.prediction.id,
                "outcome_id": row.forward_outcome.id,
                "outcome_revision": row.forward_outcome.revision,
                "target_stop_outcome_id": row.target_stop_outcome.id,
                "target_stop_revision": row.target_stop_outcome.revision,
                "eligibility_decision_id": row.eligibility_decision_id,
                "outcome_replay_id": row.outcome_replay_id,
                "evidence_origin": row.evidence_origin,
                "episode_id": row.prediction.episode_id,
                "inclusion_weight": str(row.inclusion_weight),
                "primary_winner": row.target_stop_outcome.primary_winner,
            }
            for row in evidence
        ]
    }


def _hash_payload(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
