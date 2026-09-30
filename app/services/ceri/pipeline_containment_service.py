from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriProcessingRun, CeriScoreSnapshot
from app.models.tables import (
    BackgroundJob,
    CoreCalculationCurrentProjection,
    CoreCalculationEvidence,
    MarketCalculationContext,
    PipelineRun,
)
from app.services.ceri.evidence_eligibility import (
    EXCLUDED,
    EvidenceDispositionRequest,
    append_evidence_dispositions,
    eligible_snapshot_predicate,
)

UNSUCCESSFUL_PIPELINE_STATES = frozenset({"FAILED", "CANCELLED", "BLOCKED", "PARTIAL"})
ACTIVE_PROCESSING_STATES = frozenset({"PENDING", "QUEUED", "RUNNING"})


def reconcile_unsuccessful_pipeline_ceri(
    db: Session,
    pipeline: PipelineRun,
    *,
    reason_code: str = "PARENT_PIPELINE_UNSUCCESSFUL",
) -> dict[str, int]:
    """Idempotently contain CERI state owned by an unsuccessful pipeline."""

    if pipeline.status not in UNSUCCESSFUL_PIPELINE_STATES:
        return {"excluded": 0, "projections_repaired": 0, "processing_terminalized": 0}
    snapshots = list(
        db.scalars(
            select(CeriScoreSnapshot)
            .outerjoin(
                MarketCalculationContext,
                MarketCalculationContext.id == CeriScoreSnapshot.calculation_context_id,
            )
            .where(
                or_(
                    MarketCalculationContext.pipeline_run_id == int(pipeline.id),
                    CeriScoreSnapshot.run_id == int(pipeline.upload_run_id),
                )
            )
            .order_by(CeriScoreSnapshot.id)
        )
    )
    excluded = append_evidence_dispositions(
        db,
        [
            EvidenceDispositionRequest(
                ceri_snapshot_id=int(snapshot.id),
                disposition=EXCLUDED,
                reason_code=reason_code,
                incident_reference=f"pipeline:{pipeline.id}",
                actor_source="pipeline_terminal_reconciliation",
                notes=f"Owning pipeline entered terminal state {pipeline.status}.",
                metadata_json={
                    "pipeline_run_id": int(pipeline.id),
                    "pipeline_status": str(pipeline.status),
                },
            )
            for snapshot in snapshots
            if snapshot.id is not None
        ],
    )
    repaired = _rebuild_ceri_projections(db, snapshots)
    terminalized = _terminalize_processing(db, pipeline)
    return {
        "excluded": excluded,
        "projections_repaired": repaired,
        "processing_terminalized": terminalized,
    }


def _rebuild_ceri_projections(
    db: Session, failed_snapshots: list[CeriScoreSnapshot]
) -> int:
    failed_evidence_ids = {
        int(row.evidence_id) for row in failed_snapshots if row.evidence_id is not None
    }
    if not failed_evidence_ids:
        return 0
    projections = list(
        db.scalars(
            select(CoreCalculationCurrentProjection).where(
                CoreCalculationCurrentProjection.artifact_kind == "CERI",
                CoreCalculationCurrentProjection.evidence_id.in_(failed_evidence_ids),
            )
        )
    )
    repaired = 0
    for projection in projections:
        candidate = db.scalar(
            select(CoreCalculationEvidence)
            .join(
                CeriScoreSnapshot,
                CeriScoreSnapshot.evidence_id == CoreCalculationEvidence.id,
            )
            .where(
                CoreCalculationEvidence.artifact_kind == "CERI",
                CoreCalculationEvidence.run_id == projection.run_id,
                CoreCalculationEvidence.ticker == projection.ticker,
                CoreCalculationEvidence.id.not_in(failed_evidence_ids),
                eligible_snapshot_predicate(CeriScoreSnapshot),
            )
            .order_by(
                CoreCalculationEvidence.calculated_at.desc(),
                CoreCalculationEvidence.id.desc(),
            )
            .limit(1)
        )
        if candidate is None:
            db.delete(projection)
        else:
            projection.evidence_id = int(candidate.id)
            projection.updated_at = datetime.now(UTC)
        repaired += 1
    db.flush()
    return repaired


def _terminalize_processing(db: Session, pipeline: PipelineRun) -> int:
    workflow_prefix = f"ceri:pipeline:{pipeline.id}:"
    job_ids = set(
        db.scalars(
            select(BackgroundJob.id).where(
                or_(
                    BackgroundJob.workflow_key.startswith(workflow_prefix),
                    BackgroundJob.payload_json["pipeline_run_id"].astext
                    == str(pipeline.id),
                )
            )
        )
    )
    rows = list(
        db.scalars(
            select(CeriProcessingRun).where(
                CeriProcessingRun.status.in_(ACTIVE_PROCESSING_STATES),
                or_(
                    CeriProcessingRun.background_job_id.in_(job_ids or {-1}),
                    CeriProcessingRun.deterministic_request_key.startswith(workflow_prefix),
                    CeriProcessingRun.scope_json["run_id"].astext
                    == str(pipeline.upload_run_id),
                ),
            )
        )
    )
    now = datetime.now(UTC)
    target = "CANCELLED" if pipeline.status == "CANCELLED" else "FAILED"
    for row in rows:
        row.status = target
        row.completed_at = now
        row.heartbeat_at = now
        row.execution_token = None
        row.errors_json = {
            **(row.errors_json or {}),
            "parent_pipeline_terminal_state": str(pipeline.status),
            "parent_pipeline_run_id": int(pipeline.id),
        }
    db.flush()
    return len(rows)
