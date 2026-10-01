from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, and_, cast, exists, or_, select
from sqlalchemy.orm import Session

from app.models.tables import BackgroundJob, PipelineDependency, PipelineRun

ACTIVE_EXECUTION_AUTHORITY = "ACTIVE"
NON_EXECUTABLE_PIPELINE_STATUSES = frozenset(
    {
        "CANCEL_REQUESTED",
        "COMPLETED",
        "PARTIAL",
        "FAILED",
        "BLOCKED",
        "CANCELLED",
        "FEATURE_CERTIFIED",
    }
)


def pipeline_execution_is_authorized(pipeline: PipelineRun | None) -> bool:
    return bool(
        pipeline is not None
        and pipeline.execution_authority_state == ACTIVE_EXECUTION_AUTHORITY
        and pipeline.status not in NON_EXECUTABLE_PIPELINE_STATUSES
    )


def require_pipeline_execution_authority(pipeline: PipelineRun | None) -> PipelineRun:
    if not pipeline_execution_is_authorized(pipeline):
        pipeline_id = getattr(pipeline, "id", None)
        state = getattr(pipeline, "execution_authority_state", None)
        status = getattr(pipeline, "status", None)
        raise RuntimeError(
            "PIPELINE_EXECUTION_AUTHORITY_REQUIRED: "
            f"pipeline={pipeline_id};state={state};status={status}"
        )
    return pipeline


def revoke_pipeline_execution_authority(
    pipeline: PipelineRun,
    *,
    reason: str,
    state: str = "REVOKED",
) -> None:
    if state not in {"REVOKED", "QUARANTINED", "RETIRED"}:
        raise ValueError("pipeline execution authority can only be revoked")
    from datetime import UTC, datetime

    pipeline.execution_authority_state = state
    pipeline.execution_authority_reason = str(reason)
    pipeline.execution_authority_changed_at = datetime.now(UTC)


def job_execution_authority_predicate() -> Any:
    """Return the fail-closed durable execution predicate for queue work.

    Jobs outside a pipeline keep their existing queue semantics. A job that
    identifies an owning pipeline through its payload or dependency may run
    only while that pipeline has explicit ACTIVE authority and is nonterminal.
    """

    payload_has_pipeline = BackgroundJob.payload_json.has_key(  # type: ignore[attr-defined]
        "pipeline_run_id"
    )
    payload_pipeline_id = cast(
        BackgroundJob.payload_json["pipeline_run_id"].as_string(), BigInteger
    )
    payload_authorized = exists(
        select(PipelineRun.id).where(
            PipelineRun.id == payload_pipeline_id,
            PipelineRun.execution_authority_state == ACTIVE_EXECUTION_AUTHORITY,
            PipelineRun.status.not_in(NON_EXECUTABLE_PIPELINE_STATUSES),
        )
    )
    dependency_authorized = exists(
        select(PipelineDependency.id)
        .join(PipelineRun, PipelineRun.id == PipelineDependency.pipeline_run_id)
        .where(
            PipelineDependency.id == BackgroundJob.pipeline_dependency_id,
            PipelineRun.execution_authority_state == ACTIVE_EXECUTION_AUTHORITY,
            PipelineRun.status.not_in(NON_EXECUTABLE_PIPELINE_STATUSES),
        )
    )
    pipeline_owned = or_(
        payload_has_pipeline,
        BackgroundJob.pipeline_dependency_id.is_not(None),
    )
    return or_(
        ~pipeline_owned,
        and_(
            or_(payload_authorized, dependency_authorized),
            # If both bindings exist, a mismatched payload must never borrow
            # authority from an unrelated dependency.
            or_(~payload_has_pipeline, payload_authorized),
            or_(BackgroundJob.pipeline_dependency_id.is_(None), dependency_authorized),
        ),
    )


def apply_job_execution_authority_scope(query: Any) -> Any:
    return query.where(job_execution_authority_predicate())


def claimable_pipeline_job_ids(
    db: Session,
    *,
    upload_run_id: int,
) -> tuple[int, ...]:
    query = (
        select(BackgroundJob.id)
        .where(
            BackgroundJob.related_run_id == upload_run_id,
            BackgroundJob.status.in_(("QUEUED", "RUNNING", "RECOVERING", "STALLED")),
        )
        .where(job_execution_authority_predicate())
        .order_by(BackgroundJob.id)
    )
    return tuple(int(value) for value in db.scalars(query).all())
