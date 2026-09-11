from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.canonical_evidence import CanonicalEvidenceSerializer
from app.services.price_bar_evidence import price_bar_immutable_evidence_hash

DECISION_MANIFEST_CONTRACT = "transition-decision-manifest-v1"


@dataclass(frozen=True)
class TransitionDecisionManifest:
    payload: dict[str, Any]
    fingerprint: str

    def as_dict(self) -> dict[str, Any]:
        return CanonicalEvidenceSerializer.canonicalize(self.payload)


def build_transition_decision_manifest(
    *,
    built,
    context,
    market_cutoff,
    technical_reconstruction_fingerprint: str,
    current_pointer_snapshot_id: int | None,
    current_pointer_revision: int | None,
    exact_pointer_snapshot_id: int | None,
    exact_pointer_revision: int | None,
    candidate_type: str,
    candidate_reason: str,
    candidate_confidence: str,
    predicted_pointer_advance: bool,
    predicted_current_state_advance: bool,
) -> TransitionDecisionManifest:
    dto = built.dto
    source_ids = dict(dto.source_ids)
    # The preview object is intentionally not persisted, while the production
    # score receives a database ID. Its complete semantic fingerprint below is
    # the identity that matters to the decision.
    source_ids.pop("technical_score_id", None)
    payload = {
        "contract": DECISION_MANIFEST_CONTRACT,
        "market_context": {
            "id": market_cutoff.context_id,
            "cutoff_at": market_cutoff.cutoff_at,
            "latest_completed_session": market_cutoff.latest_completed_session,
            "calendar_version": market_cutoff.calendar_version,
            "bar_readiness_version": market_cutoff.bar_readiness_version,
        },
        "candidate": {
            "ticker": dto.ticker,
            "timeframe": dto.timeframe,
            "data_as_of_date": dto.data_as_of_date,
            "selection_key": (f"{dto.ticker}/{dto.timeframe}/{dto.data_as_of_date.isoformat()}"),
            "type": candidate_type,
            "reason": candidate_reason,
            "confidence": candidate_confidence,
            "predicted_pointer_advance": predicted_pointer_advance,
            "predicted_current_state_advance": predicted_current_state_advance,
        },
        "expected_pointer": {
            "latest_snapshot_id": current_pointer_snapshot_id,
            "latest_revision": current_pointer_revision,
            "exact_snapshot_id": exact_pointer_snapshot_id,
            "exact_revision": exact_pointer_revision,
        },
        "technical": {
            "fingerprint": technical_reconstruction_fingerprint,
            "input_as_of_session": getattr(context.technical_score, "input_as_of_session", None),
            "calculation_cutoff_at": getattr(
                context.technical_score, "calculation_cutoff_at", None
            ),
            "calculation_context_id": getattr(
                context.technical_score, "calculation_context_id", None
            ),
            "engine_version": getattr(context.technical_score, "technical_engine_version", None),
        },
        "eligible_price_bars": sorted(
            (
                {
                    "id": row.id,
                    "session": row.bar_date,
                    "timeframe": row.timeframe,
                    "what_to_show": row.what_to_show,
                    "revision_count": row.revision_count,
                    "data_hash": row.data_hash,
                    "immutable_evidence_hash": price_bar_immutable_evidence_hash(row),
                }
                for row in context.price_bars
            ),
            key=lambda row: (
                str(row["session"]),
                str(row["what_to_show"]),
                int(row["id"] or 0),
            ),
        ),
        "source_ids": source_ids,
        "lifecycle_semantics": {
            "engine_version": dto.engine_version,
            "config_version": dto.config_version,
            "config_hash": dto.config_hash,
            "schema_version": dto.schema_version,
            "promoted_fields": dto.promoted_fields,
            "signals": dto.signals,
            "feature_flags": dto.feature_flags,
            "warning_flags": dto.warning_flags,
            "missing_data": dto.missing_data,
            "required_feature_coverage": built.required_feature_coverage,
            "freshness_status": built.freshness_status,
            "data_quality_label": dto.data_quality_label,
        },
    }
    canonical = CanonicalEvidenceSerializer.canonicalize(payload)
    return TransitionDecisionManifest(
        payload=canonical,
        fingerprint=CanonicalEvidenceSerializer.fingerprint(canonical),
    )


def candidate_type_for(result) -> str:
    if result.would_initialize_new_key and result.predicted_current_state_advance:
        return "NEW_SESSION_CANONICAL_INITIALIZATION"
    if result.would_initialize_new_key:
        return "NEW_KEY"
    if result.predicted_pointer_advance:
        return "SAME_SESSION_REPLACEMENT"
    return "NOT_BETTER"
