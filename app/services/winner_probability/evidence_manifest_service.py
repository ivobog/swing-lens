from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session

from app.models.tables import (
    WinnerEstimateEvidenceMember,
    WinnerEvidenceManifest,
    WinnerEvidenceManifestMember,
    WinnerProbabilityEstimate,
)
from app.services.winner_probability.evidence_service import GenerationEvidenceMember


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
        evidence: tuple[GenerationEvidenceMember, ...],
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
        get_bind = getattr(db, "get_bind", None)
        bind = get_bind() if callable(get_bind) else None
        if bind is not None and bind.dialect.name == "postgresql":
            manifest_id = db.scalar(
                postgresql_insert(WinnerEvidenceManifest)
                .values(
                    manifest_hash=manifest_hash,
                    hash_algorithm=hash_algorithm,
                    content_encoding="json",
                    member_count=len(evidence),
                    payload_json=payload,
                )
                .on_conflict_do_nothing(
                    constraint="uq_winner_evidence_manifests_hash"
                )
                .returning(WinnerEvidenceManifest.id)
            )
            manifest = (
                db.get(WinnerEvidenceManifest, manifest_id)
                if manifest_id is not None
                else db.scalar(
                    select(WinnerEvidenceManifest).where(
                        WinnerEvidenceManifest.manifest_hash == manifest_hash
                    )
                )
            )
            if manifest is None:
                raise RuntimeError("evidence manifest insert was lost")
            return EvidenceManifestResult(
                manifest=manifest,
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

    def persist_manifest_members(
        self,
        db: Session,
        *,
        manifest: WinnerEvidenceManifest,
        evidence: tuple[GenerationEvidenceMember, ...],
    ) -> int:
        """Persist immutable content-addressed members with one bulk statement."""
        values = [
            _manifest_member_values(manifest.id, ordinal, row)
            for ordinal, row in enumerate(evidence)
        ]
        if not values:
            return 0
        get_bind = getattr(db, "get_bind", None)
        bind = get_bind() if callable(get_bind) else None
        if bind is not None and bind.dialect.name == "postgresql":
            statement = postgresql_insert(WinnerEvidenceManifestMember).values(values)
            statement = statement.on_conflict_do_nothing(
                constraint="uq_winner_manifest_members_hash"
            ).returning(WinnerEvidenceManifestMember.id)
            result = db.execute(statement)
            db.flush()
            scalars = getattr(result, "scalars", None)
            if callable(scalars):
                return len(scalars().all())
            return max(0, int(result.rowcount or 0))
        rows = [WinnerEvidenceManifestMember(**value) for value in values]
        add_all = getattr(db, "add_all", None)
        if callable(add_all):
            add_all(rows)
        else:
            for row in rows:
                db.add(row)
        db.flush()
        return len(rows)

    def persist_members(
        self,
        db: Session,
        *,
        estimate: WinnerProbabilityEstimate,
        evidence: tuple[GenerationEvidenceMember, ...],
        included_as_of: datetime,
        inclusion_cutoff_at: datetime,
    ) -> None:
        values = [
            {
                "estimate_id": estimate.id,
                "prediction_id": row.prediction.id,
                "outcome_id": row.forward_outcome.id,
                "outcome_revision": row.forward_outcome.revision,
                "eligibility_decision_id": row.eligibility_decision_id,
                "outcome_replay_id": row.outcome_replay_id,
                "evidence_origin": row.evidence_origin,
                "episode_id": row.prediction.episode_id,
                "inclusion_weight": Decimal(str(row.inclusion_weight)),
                "included_as_of": included_as_of,
                "inclusion_cutoff_at": inclusion_cutoff_at,
                "metadata_json": {
                    "target_stop_outcome_id": row.target_stop_outcome.id,
                    "target_stop_revision": row.target_stop_outcome.revision,
                    "eligibility_decision_id": row.eligibility_decision_id,
                    "outcome_replay_id": row.outcome_replay_id,
                    "evidence_origin": row.evidence_origin,
                },
            }
            for row in evidence
        ]
        if not values:
            return
        get_bind = getattr(db, "get_bind", None)
        bind = get_bind() if callable(get_bind) else None
        if bind is not None and bind.dialect.name == "postgresql":
            statement = postgresql_insert(WinnerEstimateEvidenceMember).values(values)
            statement = statement.on_conflict_do_nothing(
                constraint=(
                    "uq_winner_estimate_evidence_members_estimate_outcome_revision"
                )
            )
            db.execute(statement)
        else:
            for value in values:
                db.add(WinnerEstimateEvidenceMember(**value))
        db.flush()


def _manifest_payload(evidence: tuple[GenerationEvidenceMember, ...]) -> dict[str, Any]:
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
                "inclusion_weight": _canonical_decimal(row.inclusion_weight),
                "primary_winner": row.target_stop_outcome.primary_winner,
            }
            for row in evidence
        ]
    }


def _hash_payload(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _canonical_decimal(value: Decimal | str | int | float) -> str:
    normalized = Decimal(str(value)).normalize()
    return format(normalized, "f")


def _manifest_member_values(
    manifest_id: int,
    ordinal: int,
    row: GenerationEvidenceMember,
) -> dict[str, Any]:
    identity = {
        "prediction_id": row.prediction.id,
        "forward_outcome_id": row.forward_outcome.id,
        "forward_revision": row.forward_outcome.revision,
        "target_stop_outcome_id": row.target_stop_outcome.id,
        "target_stop_revision": row.target_stop_outcome.revision,
        "eligibility_decision_id": row.eligibility_decision_id,
        "outcome_replay_id": row.outcome_replay_id,
        "evidence_origin": row.evidence_origin,
        "episode_id": row.prediction.episode_id,
        "inclusion_weight": _canonical_decimal(row.inclusion_weight),
        "primary_winner": bool(row.target_stop_outcome.primary_winner),
    }
    return {
        "manifest_id": manifest_id,
        "member_ordinal": ordinal,
        **identity,
        "member_hash": _hash_payload(identity),
    }
