from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.models.tables import BackgroundJob, PipelineDependency, PipelineRun, PipelineStep
from app.observability.transaction_metrics import publish_after_commit
from app.services.background_job_service import JobStatus, enqueue_job
from app.services.pipeline_state_machine import (
    TERMINAL_PIPELINE_STATES,
    transition_pipeline,
    transition_pipeline_step,
)

SEC_READINESS_DEPENDENCY = "SEC_READINESS"
CERI_WORKFLOW_DEPENDENCY = "CERI_WORKFLOW"
DEPENDENCY_ACTIVE_STATES = frozenset({"PENDING_ENQUEUE", "QUEUED", "RUNNING"})
DEPENDENCY_TERMINAL_STATES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})
logger = logging.getLogger(__name__)


def prepare_ceri_workflow_dependency(
    db: Session,
    *,
    pipeline: PipelineRun,
    workflow_key: str,
    resume_from_step: str,
) -> PipelineDependency:
    """Persist authority for the asynchronous CERI workflow handoff."""

    from app.services.domain_write_fence import current_domain_write_ownership
    from app.services.scope_refresh_adoption import require_semantic_authority

    require_semantic_authority(pipeline)
    ownership = current_domain_write_ownership()
    root_job_id = (
        ownership.job_id
        if ownership is not None
        else int((pipeline.result_json or {}).get("pipeline_root_job_id") or 0)
        or int((pipeline.result_json or {}).get("background_job_id") or 0)
    )
    root_job = db.get(BackgroundJob, root_job_id) if root_job_id else None
    if root_job is None or root_job.job_type != "FULL_PIPELINE":
        raise ValueError("PIPELINE_DEPENDENCY_ROOT_JOB_REQUIRED")
    identity_payload = {
        "pipeline_run_id": pipeline.id,
        "dependency_type": CERI_WORKFLOW_DEPENDENCY,
        "workflow_key": workflow_key,
        "continuation_step": resume_from_step,
    }
    continuation_identity = hashlib.sha256(
        json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    dependency_key = f"pipeline:{pipeline.id}:ceri-workflow:{continuation_identity}"
    existing = db.scalar(
        select(PipelineDependency).where(PipelineDependency.dependency_key == dependency_key)
    )
    if existing is not None:
        return existing
    dependency = PipelineDependency(
        pipeline_run_id=pipeline.id,
        dependency_type=CERI_WORKFLOW_DEPENDENCY,
        dependency_key=dependency_key,
        state="RUNNING",
        required_subjects_json=[],
        continuation_step=resume_from_step,
        continuation_identity=continuation_identity,
        root_job_id=root_job.id,
        root_worker_instance_id=root_job.worker_instance_id,
        result_json={"workflow_key": workflow_key},
    )
    db.add(dependency)
    db.flush()
    pipeline.result_json = {
        **(pipeline.result_json or {}),
        "pipeline_root_job_id": root_job.id,
        "pipeline_dependency_id": dependency.id,
        "dependency_type": CERI_WORKFLOW_DEPENDENCY,
        "dependency_state": dependency.state,
        "continuation_identity": continuation_identity,
    }
    publish_after_commit(
        db,
        "increment",
        "swinglens_pipeline_dependencies_created_total",
        dependency_type=dependency.dependency_type,
    )
    return dependency


def complete_ceri_workflow_dependency(
    db: Session,
    *,
    pipeline: PipelineRun,
    workflow_key: str,
    trigger_job: BackgroundJob,
    continuation: BackgroundJob,
    certified_count: int,
) -> PipelineDependency | None:
    """Atomically bind the certified barrier and its continuation."""

    dependency = db.scalar(
        select(PipelineDependency)
        .where(
            PipelineDependency.pipeline_run_id == pipeline.id,
            PipelineDependency.dependency_type == CERI_WORKFLOW_DEPENDENCY,
        )
        .order_by(PipelineDependency.id.desc())
        .with_for_update()
        .limit(1)
    )
    if dependency is None:
        return None
    if (dependency.result_json or {}).get("workflow_key") != workflow_key:
        raise ValueError("CERI_PIPELINE_DEPENDENCY_IDENTITY_MISMATCH")
    now = _utcnow()
    dependency.state = "COMPLETED"
    dependency.child_job_id = trigger_job.id
    dependency.continuation_job_id = continuation.id
    dependency.completed_at = now
    dependency.updated_at = now
    dependency.result_json = {
        **(dependency.result_json or {}),
        "certified_count": certified_count,
        "trigger_job_id": trigger_job.id,
    }
    trigger_job.pipeline_dependency_id = dependency.id
    continuation.pipeline_dependency_id = dependency.id
    continuation.root_job_id = dependency.root_job_id
    _publish_dependency_latency(db, dependency, now, outcome="completed")
    return dependency


def fail_ceri_workflow_dependency(
    db: Session,
    *,
    pipeline: PipelineRun,
    workflow_key: str,
    error_message: str,
) -> PipelineDependency | None:
    dependency = db.scalar(
        select(PipelineDependency)
        .where(
            PipelineDependency.pipeline_run_id == pipeline.id,
            PipelineDependency.dependency_type == CERI_WORKFLOW_DEPENDENCY,
            PipelineDependency.state.in_(DEPENDENCY_ACTIVE_STATES),
        )
        .order_by(PipelineDependency.id.desc())
        .with_for_update()
        .limit(1)
    )
    if dependency is None:
        return None
    if (dependency.result_json or {}).get("workflow_key") != workflow_key:
        raise ValueError("CERI_PIPELINE_DEPENDENCY_IDENTITY_MISMATCH")
    now = _utcnow()
    dependency.state = "FAILED"
    dependency.completed_at = now
    dependency.updated_at = now
    dependency.error_message = error_message
    _publish_dependency_latency(db, dependency, now, outcome="failed")
    return dependency


def prepare_sec_readiness_dependency(
    db: Session,
    *,
    pipeline: PipelineRun,
    diagnostics: dict[str, Any],
    resume_from_step: str,
) -> PipelineDependency:
    """Transaction A: persist the wait contract without creating a child job."""

    from app.services.domain_write_fence import current_domain_write_ownership
    from app.services.scope_refresh_adoption import require_semantic_authority

    require_semantic_authority(pipeline)
    ownership = current_domain_write_ownership()
    root_job_id = (
        ownership.job_id
        if ownership is not None
        else int((pipeline.result_json or {}).get("pipeline_root_job_id") or 0)
        or int((pipeline.result_json or {}).get("background_job_id") or 0)
    )
    root_job = db.get(BackgroundJob, root_job_id) if root_job_id else None
    if root_job is None or root_job.job_type != "FULL_PIPELINE":
        raise ValueError("PIPELINE_DEPENDENCY_ROOT_JOB_REQUIRED")

    readiness = dict(diagnostics.get("readiness") or {})
    processor = dict(diagnostics.get("processor") or {})
    signature = str(
        readiness.get("processor_signature")
        or processor.get("active_signature")
        or processor.get("deployed_signature")
        or "unknown"
    )
    subjects = _required_subjects(readiness)
    identity_payload = {
        "pipeline_run_id": pipeline.id,
        "dependency_type": SEC_READINESS_DEPENDENCY,
        "processor_signature": signature,
        "continuation_step": resume_from_step,
        "subjects": subjects,
    }
    continuation_identity = hashlib.sha256(
        json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    dependency_key = f"pipeline:{pipeline.id}:sec-readiness:{continuation_identity}"
    existing = db.scalar(
        select(PipelineDependency).where(PipelineDependency.dependency_key == dependency_key)
    )
    if existing is not None:
        return existing

    dependency = PipelineDependency(
        pipeline_run_id=pipeline.id,
        dependency_type=SEC_READINESS_DEPENDENCY,
        dependency_key=dependency_key,
        state="PENDING_ENQUEUE",
        required_subjects_json=subjects,
        continuation_step=resume_from_step,
        continuation_identity=continuation_identity,
        root_job_id=root_job.id,
        root_worker_instance_id=root_job.worker_instance_id,
        result_json={
            "processor_signature": signature,
            "readiness": readiness,
            "diagnostics": diagnostics,
        },
    )
    db.add(dependency)
    db.flush()
    transition_pipeline(
        db,
        pipeline,
        "WAITING_DEPENDENCY",
        actor="pipeline_orchestrator",
        current_step=resume_from_step,
        message="Waiting for automatic SEC preparation.",
    )
    step = _pipeline_step(db, pipeline.id, resume_from_step)
    if step is not None:
        transition_pipeline_step(
            db,
            step,
            "PENDING",
            message="Waiting for automatic SEC preparation.",
        )
    pipeline.result_json = {
        **(pipeline.result_json or {}),
        "pipeline_root_job_id": root_job.id,
        "pipeline_dependency_id": dependency.id,
        "dependency_type": SEC_READINESS_DEPENDENCY,
        "dependency_state": dependency.state,
        "continuation_identity": continuation_identity,
        "repair_job_id": None,
        "blocked_reason": None,
        "blocked_diagnostics": None,
    }
    db.flush()
    publish_after_commit(
        db,
        "increment",
        "swinglens_pipeline_dependencies_created_total",
        dependency_type=dependency.dependency_type,
    )
    logger.info(
        "pipeline.dependency.prepared",
        extra={
            "pipeline_id": pipeline.id,
            "dependency_id": dependency.id,
            "root_job_id": root_job.id,
            "continuation_identity": continuation_identity,
        },
    )
    return dependency


def enqueue_sec_readiness_dependency(
    db: Session,
    *,
    dependency_id: int,
) -> BackgroundJob:
    """Transaction B: enqueue the child only after Transaction A committed."""

    from app.services.scope_refresh_adoption import (
        bind_semantic_authority,
        require_semantic_authority,
    )

    dependency = db.scalar(
        select(PipelineDependency)
        .where(PipelineDependency.id == dependency_id)
        .with_for_update()
    )
    if dependency is None:
        raise ValueError(f"Pipeline dependency {dependency_id} was not found.")
    pipeline = db.scalar(
        select(PipelineRun)
        .where(PipelineRun.id == dependency.pipeline_run_id)
        .with_for_update()
    )
    if pipeline is None:
        raise ValueError(f"Pipeline run {dependency.pipeline_run_id} was not found.")
    if pipeline.status in TERMINAL_PIPELINE_STATES or pipeline.status == "CANCEL_REQUESTED":
        dependency.state = "CANCELLED"
        dependency.completed_at = _utcnow()
        dependency.updated_at = dependency.completed_at
        db.flush()
        raise ValueError("PIPELINE_DEPENDENCY_PARENT_TERMINAL")
    if dependency.child_job_id is not None:
        child = db.get(BackgroundJob, dependency.child_job_id)
        if child is None:
            raise ValueError("PIPELINE_DEPENDENCY_CHILD_MISSING")
        return child
    retained = dict(dependency.result_json or {})
    signature = str(retained.get("processor_signature") or "unknown")
    request_key = f"sec-readiness-repair:dependency:{dependency.continuation_identity}"
    child = enqueue_job(
        db,
        job_type="SEC_READINESS_REPAIR",
        payload={
            "pipeline_run_id": pipeline.id,
            "pipeline_dependency_id": dependency.id,
            "processor_signature": signature,
            "resume_from_step": dependency.continuation_step,
        },
        related_run_id=pipeline.upload_run_id,
        priority=70,
        max_retries=3,
        request_key=request_key,
        workflow_key=f"pipeline:{pipeline.id}:sec-readiness",
        root_job_id=dependency.root_job_id,
        parent_job_id=dependency.root_job_id,
        trigger_source="PIPELINE_DEPENDENCY",
        pipeline_dependency_id=dependency.id,
    )
    bind_semantic_authority(
        child, require_semantic_authority(pipeline), required_for_parent_completion=True
    )
    dependency.child_job_id = child.id
    dependency.state = "QUEUED"
    dependency.updated_at = _utcnow()
    pipeline.result_json = {
        **(pipeline.result_json or {}),
        "pipeline_dependency_id": dependency.id,
        "dependency_state": dependency.state,
        "repair_job_id": child.id,
        "sec_repair": {
            "pipeline_id": pipeline.id,
            "run_id": pipeline.upload_run_id,
            "repair_job_id": child.id,
            "repair_stage": "QUEUED",
            "current_processor_signature": signature,
            "updated_at": dependency.updated_at.isoformat(),
        },
    }
    db.flush()
    logger.info(
        "pipeline.dependency.child_queued",
        extra={
            "pipeline_id": pipeline.id,
            "dependency_id": dependency.id,
            "child_job_id": child.id,
        },
    )
    return child


def reconcile_pipeline_job(db: Session, job_id: int) -> None:
    """Orchestrator transaction after a job control transaction has committed."""

    if not isinstance(db, Session):
        return
    job = db.get(BackgroundJob, job_id)
    if job is None:
        return
    if job.pipeline_dependency_id is not None:
        _reconcile_dependency_job(db, job)
    if job.job_type == "FULL_PIPELINE":
        _reconcile_pipeline_root_job(db, job)


def mark_dependency_job_running(db: Session, job_id: int) -> None:
    job = db.get(BackgroundJob, job_id)
    if job is None or job.pipeline_dependency_id is None or job.status != JobStatus.RUNNING:
        return
    dependency = db.scalar(
        select(PipelineDependency)
        .where(PipelineDependency.id == job.pipeline_dependency_id)
        .with_for_update()
    )
    if dependency is None or dependency.child_job_id != job.id:
        return
    if dependency.state in {"QUEUED", "RUNNING"}:
        dependency.state = "RUNNING"
        dependency.updated_at = _utcnow()
        db.flush()


def reconcile_pending_dependency_enqueues(db: Session) -> tuple[int, ...]:
    """Safely complete crash gaps between wait-state commit and child enqueue."""

    if not isinstance(db, Session):
        return ()
    bind = db.get_bind()
    if bind.dialect.name == "sqlite" and not inspect(bind).has_table(
        PipelineDependency.__tablename__
    ):
        return ()

    ids = tuple(
        db.scalars(
            select(PipelineDependency.id)
            .where(PipelineDependency.state == "PENDING_ENQUEUE")
            .order_by(PipelineDependency.id)
            .with_for_update(skip_locked=True)
        )
    )
    created: list[int] = []
    for dependency_id in ids:
        child = enqueue_sec_readiness_dependency(db, dependency_id=int(dependency_id))
        created.append(child.id)
    return tuple(created)


def reconcile_safe_pipeline_invariants(db: Session) -> dict[str, tuple[int, ...]]:
    """Repair only deterministic crash gaps; never rewrite ambiguous history."""

    if not isinstance(db, Session):
        return {"children_enqueued": (), "continuations_enqueued": (), "cancellations": ()}
    children = reconcile_pending_dependency_enqueues(db)
    continuations: list[int] = []
    completed_dependencies = list(
        db.scalars(
            select(PipelineDependency)
            .where(
                PipelineDependency.state == "COMPLETED",
                PipelineDependency.child_job_id.is_not(None),
                PipelineDependency.continuation_job_id.is_(None),
            )
            .order_by(PipelineDependency.id)
            .with_for_update(skip_locked=True)
        )
    )
    for dependency in completed_dependencies:
        child = db.get(BackgroundJob, dependency.child_job_id)
        if child is not None and child.status == JobStatus.COMPLETED:
            _reconcile_dependency_job(db, child)
            if dependency.continuation_job_id is not None:
                continuations.append(dependency.continuation_job_id)

    from app.services.pipeline_service import _active_pipeline_control_job_ids

    cancelled: list[int] = []
    pipelines = list(
        db.scalars(
            select(PipelineRun)
            .where(PipelineRun.status == "CANCEL_REQUESTED")
            .order_by(PipelineRun.id)
            .with_for_update(skip_locked=True)
        )
    )
    for pipeline in pipelines:
        if _active_pipeline_control_job_ids(db, pipeline.id):
            continue
        transition_pipeline(
            db,
            pipeline,
            "CANCELLED",
            actor="pipeline_orchestrator",
            message="Pipeline cancellation completed by deterministic reconciliation.",
        )
        _cancel_incomplete_steps(db, pipeline.id)
        for dependency in db.scalars(
            select(PipelineDependency).where(
                PipelineDependency.pipeline_run_id == pipeline.id,
                PipelineDependency.state.in_(DEPENDENCY_ACTIVE_STATES),
            )
        ):
            dependency.state = "CANCELLED"
            dependency.completed_at = _utcnow()
            dependency.updated_at = dependency.completed_at
        cancelled.append(pipeline.id)
    return {
        "children_enqueued": tuple(children),
        "continuations_enqueued": tuple(continuations),
        "cancellations": tuple(cancelled),
    }


def _reconcile_dependency_job(db: Session, job: BackgroundJob) -> None:
    dependency = db.scalar(
        select(PipelineDependency)
        .where(PipelineDependency.id == job.pipeline_dependency_id)
        .with_for_update()
    )
    if dependency is None:
        return
    pipeline = db.scalar(
        select(PipelineRun)
        .where(PipelineRun.id == dependency.pipeline_run_id)
        .with_for_update()
    )
    if pipeline is None:
        return
    now = _utcnow()
    if job.id == dependency.child_job_id:
        if job.status == JobStatus.RUNNING:
            dependency.state = "RUNNING"
            dependency.updated_at = now
            return
        if job.status == JobStatus.QUEUED:
            dependency.state = "QUEUED"
            dependency.updated_at = now
            return
        if job.status == JobStatus.COMPLETED:
            dependency.state = "COMPLETED"
            dependency.completed_at = now
            dependency.updated_at = now
            dependency.result_json = {
                **(dependency.result_json or {}),
                "child_result": job.result_json or {},
            }
            _publish_dependency_latency(db, dependency, now, outcome="completed")
            if pipeline.status in TERMINAL_PIPELINE_STATES:
                return
            if pipeline.status == "CANCEL_REQUESTED":
                transition_pipeline(
                    db,
                    pipeline,
                    "CANCELLED",
                    actor="dependency",
                    message="Pipeline cancellation completed after SEC preparation stopped.",
                )
                _cancel_incomplete_steps(db, pipeline.id)
                return
            _enqueue_dependency_continuation(db, pipeline, dependency, job)
            return
        if job.status in {JobStatus.FAILED, JobStatus.BLOCKED}:
            dependency.state = "FAILED"
            dependency.completed_at = now
            dependency.updated_at = now
            dependency.error_message = job.error_message
            _publish_dependency_latency(db, dependency, now, outcome="failed")
            if pipeline.status not in TERMINAL_PIPELINE_STATES:
                transition_pipeline(
                    db,
                    pipeline,
                    "FAILED" if job.status == JobStatus.FAILED else "BLOCKED",
                    actor="dependency",
                    message="Automatic SEC preparation failed.",
                    error_message=job.error_message,
                )
            return
        if job.status == JobStatus.CANCELLED:
            dependency.state = "CANCELLED"
            dependency.completed_at = now
            dependency.updated_at = now
            _publish_dependency_latency(db, dependency, now, outcome="cancelled")
            if pipeline.status not in TERMINAL_PIPELINE_STATES:
                transition_pipeline(
                    db,
                    pipeline,
                    "CANCELLED",
                    actor="dependency",
                    message="Pipeline cancelled while waiting for SEC preparation.",
                )
                _cancel_incomplete_steps(db, pipeline.id)


def _enqueue_dependency_continuation(
    db: Session,
    pipeline: PipelineRun,
    dependency: PipelineDependency,
    child: BackgroundJob,
) -> BackgroundJob:
    from app.services.pipeline_service import (
        PIPELINE_JOB_MAX_RETRIES,
        PIPELINE_JOB_PRIORITY,
        _pipeline_context_payload,
    )
    from app.services.scope_refresh_adoption import (
        bind_semantic_authority,
        require_semantic_authority,
    )

    if dependency.continuation_job_id is not None:
        existing = db.get(BackgroundJob, dependency.continuation_job_id)
        if existing is None:
            raise ValueError("PIPELINE_DEPENDENCY_CONTINUATION_MISSING")
        return existing
    request_key = f"resume-pipeline:dependency:{dependency.continuation_identity}"
    continuation = enqueue_job(
        db,
        job_type="FULL_PIPELINE",
        payload={
            "pipeline_run_id": pipeline.id,
            "pipeline_dependency_id": dependency.id,
            "resume_from_step": dependency.continuation_step,
            **_pipeline_context_payload(db, pipeline),
        },
        related_run_id=pipeline.upload_run_id,
        priority=PIPELINE_JOB_PRIORITY,
        max_retries=PIPELINE_JOB_MAX_RETRIES,
        request_key=request_key,
        workflow_key=f"pipeline:{pipeline.id}:dependency-continuation",
        root_job_id=dependency.root_job_id,
        parent_job_id=child.id,
        trigger_source="PIPELINE_DEPENDENCY_COMPLETED",
        pipeline_dependency_id=dependency.id,
    )
    bind_semantic_authority(continuation, require_semantic_authority(pipeline))
    dependency.continuation_job_id = continuation.id
    dependency.updated_at = _utcnow()
    transition_pipeline(
        db,
        pipeline,
        "QUEUED",
        actor="dependency",
        current_step=dependency.continuation_step,
        message="SEC preparation completed; pipeline continuation queued.",
    )
    step = _pipeline_step(db, pipeline.id, dependency.continuation_step)
    if step is not None:
        transition_pipeline_step(
            db,
            step,
            "PENDING",
            message="SEC preparation completed; continuing pipeline.",
        )
    pipeline.result_json = {
        **(pipeline.result_json or {}),
        "background_job_id": continuation.id,
        "dependency_state": dependency.state,
        "continuation_job_id": continuation.id,
        "resume_from_step": dependency.continuation_step,
        "blocked_reason": None,
        "blocked_diagnostics": None,
    }
    db.flush()
    publish_after_commit(
        db,
        "increment",
        "swinglens_pipeline_continuations_created_total",
        dependency_type=dependency.dependency_type,
    )
    logger.info(
        "pipeline.dependency.continuation_queued",
        extra={
            "pipeline_id": pipeline.id,
            "dependency_id": dependency.id,
            "child_job_id": child.id,
            "continuation_job_id": continuation.id,
        },
    )
    return continuation


def _reconcile_pipeline_root_job(db: Session, job: BackgroundJob) -> None:
    pipeline_id = int((job.payload_json or {}).get("pipeline_run_id") or 0)
    if not pipeline_id:
        return
    pipeline = db.scalar(
        select(PipelineRun).where(PipelineRun.id == pipeline_id).with_for_update()
    )
    if pipeline is None or pipeline.status in TERMINAL_PIPELINE_STATES:
        return
    if job.status == JobStatus.CANCELLED:
        transition_pipeline(
            db,
            pipeline,
            "CANCELLED",
            actor="pipeline_orchestrator",
            message="Pipeline cancellation completed.",
        )
        _cancel_incomplete_steps(db, pipeline.id)
    elif job.status in {JobStatus.FAILED, JobStatus.BLOCKED}:
        transition_pipeline(
            db,
            pipeline,
            "FAILED" if job.status == JobStatus.FAILED else "BLOCKED",
            actor="pipeline_orchestrator",
            message="Pipeline execution failed.",
            error_message=job.error_message,
        )
    elif job.status == JobStatus.COMPLETED and not _root_has_active_dependency_handoff(
        db, pipeline, job
    ):
        transition_pipeline(
            db,
            pipeline,
            "FAILED",
            actor="pipeline_orchestrator",
            message="Pipeline root completed without a terminal pipeline transition.",
            error_message="PIPELINE_ROOT_TERMINAL_WITH_ACTIVE_PIPELINE",
        )


def _root_has_active_dependency_handoff(
    db: Session,
    pipeline: PipelineRun,
    root_job: BackgroundJob,
) -> bool:
    expected_type = (
        SEC_READINESS_DEPENDENCY
        if pipeline.status == "WAITING_DEPENDENCY"
        else CERI_WORKFLOW_DEPENDENCY
        if pipeline.status == "WAITING_FOR_CERI_COMPLETION"
        else None
    )
    if expected_type is None:
        return False
    active = list(
        db.scalars(
            select(PipelineDependency).where(
                PipelineDependency.pipeline_run_id == pipeline.id,
                PipelineDependency.root_job_id == root_job.id,
                PipelineDependency.dependency_type == expected_type,
                PipelineDependency.state.in_(DEPENDENCY_ACTIVE_STATES),
            )
        )
    )
    return len(active) == 1


def _required_subjects(readiness: dict[str, Any]) -> list[str]:
    direct = readiness.get("blocking_tickers") or []
    if direct:
        return sorted({str(value).strip().upper() for value in direct if str(value).strip()})
    return sorted(
        {
            str(item.get("ticker") or "").strip().upper()
            for item in readiness.get("tickers") or []
            if not item.get("accepted") and str(item.get("ticker") or "").strip()
        }
    )


def _pipeline_step(db: Session, pipeline_id: int, step_name: str) -> PipelineStep | None:
    return db.scalar(
        select(PipelineStep).where(
            PipelineStep.pipeline_run_id == pipeline_id,
            PipelineStep.step_name == step_name,
        )
    )


def _cancel_incomplete_steps(db: Session, pipeline_id: int) -> None:
    for step in db.scalars(
        select(PipelineStep).where(PipelineStep.pipeline_run_id == pipeline_id)
    ):
        if step.status in {"PENDING", "RUNNING"}:
            transition_pipeline_step(
                db,
                step,
                "CANCELLED",
                message="Pipeline cancelled.",
                completed_at=_utcnow(),
            )


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _publish_dependency_latency(
    db: Session,
    dependency: PipelineDependency,
    completed_at: datetime,
    *,
    outcome: str,
) -> None:
    created_at = dependency.created_at
    seconds = max(0.0, (completed_at - created_at).total_seconds()) if created_at else 0.0
    publish_after_commit(
        db,
        "observe",
        "swinglens_pipeline_dependency_wait_seconds",
        value=seconds,
        dependency_type=dependency.dependency_type,
        outcome=outcome,
    )
