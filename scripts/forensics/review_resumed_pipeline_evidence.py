"""Explicit operator review of automatic CERI containment during a verified resume.

Call in the same transaction as ``resume_pipeline``, before committing its job.
This does not change the default resume or eligibility policy. Only unchanged
snapshots pinned by the retained handoff can receive append-only reinstatement.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriEvidenceDisposition, CeriScoreSnapshot
from app.models.tables import PipelineRun, TransitionDecisionHandoffManifest
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.ceri.evidence_eligibility import (
    ELIGIBLE,
    EXCLUDED,
    EvidenceDispositionRequest,
    append_evidence_dispositions,
    eligible_snapshot_select,
)
from app.services.decision_handoff_contract import handoff_artifact_identity


def review_resumed_pipeline_evidence(
    db: Session,
    pipeline_run_id: int,
    *,
    expected_handoff_fingerprint: str,
    incident_reference: str,
) -> dict:
    """Reject changed evidence/manual exclusions; review automatic exclusions only.

    No commits occur here. Any rejection must roll back the caller's entire resume
    transaction, including the queued job. Snapshot locks serialize concurrent
    disposition inserts through their foreign keys; later reviews still prevail.
    The canonical owner predicate remains authoritative after reinstatement.
    """

    if not incident_reference.strip() or not expected_handoff_fingerprint:
        raise ValueError("RESUME_REVIEW_AUTHORITY_REQUIRED")
    pipeline = db.scalar(
        select(PipelineRun).where(PipelineRun.id == pipeline_run_id).with_for_update()
    )
    if (
        pipeline is None
        or pipeline.status != "QUEUED"
        or pipeline.current_step != "EVALUATING_SETUP_LIFECYCLES"
    ):
        raise ValueError("RESUME_REVIEW_QUEUED_LIFECYCLE_RESUME_REQUIRED")
    retained = db.scalar(
        select(TransitionDecisionHandoffManifest)
        .where(TransitionDecisionHandoffManifest.pipeline_run_id == pipeline_run_id)
        .with_for_update(read=True)
    )
    if (
        retained is None
        or retained.upload_run_id != pipeline.upload_run_id
        or retained.manifest_fingerprint != expected_handoff_fingerprint
        or Canonical.fingerprint(retained.manifest_json) != expected_handoff_fingerprint
        or retained.manifest_json.get("run")
        != {"pipeline_run_id": pipeline_run_id, "upload_run_id": pipeline.upload_run_id}
    ):
        raise ValueError("RESUME_REVIEW_FROZEN_HANDOFF_MISMATCH")
    expected = retained.manifest_json.get("ceri_score_snapshots")
    snapshots = list(
        db.scalars(
            select(CeriScoreSnapshot)
            .where(CeriScoreSnapshot.run_id == pipeline.upload_run_id)
            .order_by(CeriScoreSnapshot.ticker, CeriScoreSnapshot.id)
            .with_for_update()
        )
    )
    if not snapshots or expected != [handoff_artifact_identity(row) for row in snapshots]:
        raise ValueError("RESUME_REVIEW_CERI_EVIDENCE_CHANGED")
    ids = {row.id for row in snapshots}
    history = list(
        db.scalars(
            select(CeriEvidenceDisposition)
            .where(CeriEvidenceDisposition.ceri_snapshot_id.in_(ids))
            .order_by(
                CeriEvidenceDisposition.ceri_snapshot_id,
                CeriEvidenceDisposition.created_at,
                CeriEvidenceDisposition.id,
            )
        )
    )
    latest = {row.ceri_snapshot_id: row for row in history}
    explicit = {
        row.ceri_snapshot_id: row
        for row in history
        if not _automatic_parent_exclusion(row, pipeline_run_id)
    }
    requests = []
    reviewed = []
    for snapshot in snapshots:
        prior_review = explicit.get(snapshot.id)
        if prior_review is not None and prior_review.disposition == EXCLUDED:
            raise ValueError(f"RESUME_REVIEW_NONAUTOMATIC_EXCLUSION: snapshot={snapshot.id}")
        exclusion = latest.get(snapshot.id)
        if exclusion is None or exclusion.disposition == ELIGIBLE:
            continue
        if not _automatic_parent_exclusion(exclusion, pipeline_run_id):
            raise ValueError(f"RESUME_REVIEW_NONAUTOMATIC_EXCLUSION: snapshot={snapshot.id}")
        reviewed.append({"snapshot_id": snapshot.id, "exclusion_event_id": exclusion.id})
        requests.append(
            EvidenceDispositionRequest(
                ceri_snapshot_id=snapshot.id,
                disposition=ELIGIBLE,
                reason_code="PARENT_PIPELINE_RESUMED_VERIFIED_HANDOFF",
                incident_reference=incident_reference,
                actor_source="verified_pipeline_operator_resume",
                notes="Operator-approved resume; original CERI identity matches frozen handoff.",
                metadata_json={
                    "pipeline_run_id": pipeline_run_id,
                    "reviewed_exclusion_event_id": exclusion.id,
                    "handoff_manifest_id": retained.id,
                    "handoff_manifest_fingerprint": expected_handoff_fingerprint,
                },
            )
        )
    appended = append_evidence_dispositions(db, requests)
    eligible = set(
        db.scalars(
            eligible_snapshot_select(CeriScoreSnapshot.id).where(CeriScoreSnapshot.id.in_(ids))
        )
    )
    if eligible != ids:
        raise ValueError("RESUME_REVIEW_CANONICAL_ELIGIBILITY_REJECTED")
    return {
        "pipeline_run_id": pipeline_run_id,
        "handoff_manifest_id": retained.id,
        "handoff_manifest_fingerprint": expected_handoff_fingerprint,
        "incident_reference": incident_reference,
        "verified_snapshot_count": len(snapshots),
        "appended_reinstatements": appended,
        "reviewed_exclusions": reviewed,
        "snapshot_rows_modified": False,
    }


def _automatic_parent_exclusion(event: CeriEvidenceDisposition, pipeline_run_id: int) -> bool:
    return (
        event.disposition == EXCLUDED
        and event.reason_code == "PARENT_PIPELINE_UNSUCCESSFUL"
        and event.actor_source == "pipeline_terminal_reconciliation"
        and event.incident_reference == f"pipeline:{pipeline_run_id}"
        and (event.metadata_json or {}).get("pipeline_run_id") == pipeline_run_id
        and (event.metadata_json or {}).get("pipeline_status")
        in {"FAILED", "BLOCKED", "CANCELLED", "PARTIAL"}
    )
