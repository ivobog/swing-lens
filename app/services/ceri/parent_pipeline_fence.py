from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import BackgroundJob, PipelineRun
from app.services.pipeline_prerequisites import CeriParentPipelineTerminalError

TERMINAL_PIPELINE_STATUSES = frozenset({"COMPLETED", "PARTIAL", "FAILED", "BLOCKED", "CANCELLED"})


def require_parent_pipeline_active(
    db: Session,
    job: BackgroundJob,
    *,
    lock_for_checkpoint: bool = False,
) -> PipelineRun | None:
    """Fence pipeline-owned CERI work against a terminal authoritative parent.

    A checkpoint caller requests ``FOR UPDATE`` and retains that row lock until
    its transaction commits. Pipeline failure roll-up takes the same lock, which
    establishes a total order: a child checkpoint is either committed before the
    terminal transition, or observes the terminal transition and rolls back.
    """

    payload = dict(job.payload_json or {})
    workflow_key = str(payload.get("workflow_key") or job.workflow_key or "")
    pipeline_id = payload.get("pipeline_run_id")
    if not workflow_key.startswith("ceri:pipeline:") or pipeline_id in (None, ""):
        return None
    statement = select(PipelineRun).where(PipelineRun.id == int(pipeline_id))
    if lock_for_checkpoint:
        statement = statement.with_for_update()
    pipeline = db.scalar(statement)
    if pipeline is None:
        raise CeriParentPipelineTerminalError(
            "Authoritative parent pipeline does not exist.",
            diagnostics={"pipeline_run_id": int(pipeline_id), "job_id": job.id},
        )
    if pipeline.status in TERMINAL_PIPELINE_STATUSES:
        raise CeriParentPipelineTerminalError(
            f"Authoritative parent pipeline is terminal: {pipeline.status}.",
            diagnostics={
                "pipeline_run_id": pipeline.id,
                "pipeline_status": pipeline.status,
                "job_id": job.id,
                "job_type": job.job_type,
            },
        )
    return pipeline
