from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import (
    BackgroundJob,
    BackgroundWorker,
    PipelineDependency,
    PipelineRun,
    PipelineStep,
)
from app.services.background_job_service import ACTIVE_JOB_STATUSES, TERMINAL_JOB_STATUSES
from app.services.pipeline_execution_authority import (
    job_execution_authority_predicate,
    pipeline_execution_is_authorized,
)
from app.services.pipeline_state_machine import TERMINAL_PIPELINE_STATES


class InvariantDisposition(StrEnum):
    SAFE_AUTO_RECONCILE = "SAFE_AUTO_RECONCILE"
    REQUIRES_OPERATOR_REVIEW = "REQUIRES_OPERATOR_REVIEW"
    FATAL_STARTUP_INVARIANT = "FATAL_STARTUP_INVARIANT"


class InvariantScope(StrEnum):
    OPERATIONAL_ACTIONABLE = "OPERATIONAL_ACTIONABLE"
    HISTORICAL_FORENSIC = "HISTORICAL_FORENSIC"


@dataclass(frozen=True)
class PipelineInvariantFinding:
    code: str
    disposition: InvariantDisposition
    pipeline_id: int | None = None
    job_id: int | None = None
    dependency_id: int | None = None
    detail: str = ""
    scope: InvariantScope = InvariantScope.OPERATIONAL_ACTIONABLE

    @property
    def operational_actionable(self) -> bool:
        return self.scope is InvariantScope.OPERATIONAL_ACTIONABLE

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "disposition": self.disposition.value,
            "pipeline_id": self.pipeline_id,
            "job_id": self.job_id,
            "dependency_id": self.dependency_id,
            "detail": self.detail,
            "scope": self.scope.value,
            "operational_actionable": self.operational_actionable,
        }


def inspect_pipeline_invariants(
    db: Session,
    *,
    now: datetime | None = None,
) -> tuple[PipelineInvariantFinding, ...]:
    """Read-only consistency audit; historical ambiguity is never rewritten."""

    now = now or datetime.now(UTC)
    findings: list[PipelineInvariantFinding] = []
    pipelines = list(db.scalars(select(PipelineRun).order_by(PipelineRun.id)))
    pipeline_by_id = {pipeline.id: pipeline for pipeline in pipelines}
    jobs = list(db.scalars(select(BackgroundJob).order_by(BackgroundJob.id)))
    jobs_by_id = {job.id: job for job in jobs}
    dependencies = list(
        db.scalars(select(PipelineDependency).order_by(PipelineDependency.id))
    )
    dependencies_by_id = {dependency.id: dependency for dependency in dependencies}
    workers = {worker.worker_id: worker for worker in db.scalars(select(BackgroundWorker))}
    executable_job_ids = set(
        db.scalars(select(BackgroundJob.id).where(job_execution_authority_predicate()))
    )

    active_full_by_pipeline: dict[int, list[BackgroundJob]] = {}
    for job in jobs:
        pipeline_id = _job_pipeline_id(job)
        if job.job_type == "FULL_PIPELINE" and job.status in ACTIVE_JOB_STATUSES:
            if pipeline_id is None or pipeline_id not in pipeline_by_id:
                findings.append(
                    PipelineInvariantFinding(
                        "ACTIVE_ROOT_WITHOUT_VALID_PIPELINE",
                        InvariantDisposition.REQUIRES_OPERATOR_REVIEW,
                        pipeline_id=pipeline_id,
                        job_id=job.id,
                    )
                )
            elif job.pipeline_dependency_id is None:
                active_full_by_pipeline.setdefault(pipeline_id, []).append(job)
        if job.pipeline_dependency_id is not None:
            dependency = dependencies_by_id.get(job.pipeline_dependency_id)
            if dependency is None or dependency.pipeline_run_id != pipeline_id:
                findings.append(
                    PipelineInvariantFinding(
                        "CHILD_WITHOUT_VALID_PIPELINE_DEPENDENCY",
                        InvariantDisposition.FATAL_STARTUP_INVARIANT,
                        pipeline_id=pipeline_id,
                        job_id=job.id,
                        dependency_id=job.pipeline_dependency_id,
                    )
                )
        if (
            job.status == "RUNNING"
            and job.lease_expires_at is not None
            and job.lease_expires_at <= now
        ):
            findings.append(
                PipelineInvariantFinding(
                    "EXPIRED_WORKER_LEASE",
                    InvariantDisposition.SAFE_AUTO_RECONCILE,
                    pipeline_id=pipeline_id,
                    job_id=job.id,
                )
            )
        if job.status == "RUNNING" and job.worker_id:
            worker = workers.get(job.worker_id)
            if worker is None or (
                job.worker_instance_id
                and worker.instance_id
                and job.worker_instance_id != worker.instance_id
            ):
                findings.append(
                    PipelineInvariantFinding(
                        "STALE_GENERATION_WITH_APPARENT_AUTHORITY",
                        InvariantDisposition.REQUIRES_OPERATOR_REVIEW,
                        pipeline_id=pipeline_id,
                        job_id=job.id,
                    )
                )

    for pipeline_id, roots in active_full_by_pipeline.items():
        if len(roots) > 1:
            findings.append(
                PipelineInvariantFinding(
                    "DUPLICATE_ACTIVE_ROOT_JOBS",
                    InvariantDisposition.FATAL_STARTUP_INVARIANT,
                    pipeline_id=pipeline_id,
                    detail=",".join(str(job.id) for job in roots),
                )
            )

    deps_by_pipeline: dict[int, list[PipelineDependency]] = {}
    for dependency in dependencies:
        deps_by_pipeline.setdefault(dependency.pipeline_run_id, []).append(dependency)
        child = jobs_by_id.get(dependency.child_job_id) if dependency.child_job_id else None
        continuation = (
            jobs_by_id.get(dependency.continuation_job_id)
            if dependency.continuation_job_id
            else None
        )
        if dependency.state == "PENDING_ENQUEUE" and child is None:
            findings.append(
                PipelineInvariantFinding(
                    "WAITING_DEPENDENCY_REQUIRES_CHILD_ENQUEUE",
                    InvariantDisposition.SAFE_AUTO_RECONCILE,
                    pipeline_id=dependency.pipeline_run_id,
                    dependency_id=dependency.id,
                )
            )
        if dependency.state == "COMPLETED" and continuation is None:
            findings.append(
                PipelineInvariantFinding(
                    "COMPLETED_DEPENDENCY_REQUIRES_CONTINUATION",
                    InvariantDisposition.SAFE_AUTO_RECONCILE,
                    pipeline_id=dependency.pipeline_run_id,
                    dependency_id=dependency.id,
                )
            )
        active_continuations = [
            job
            for job in jobs
            if job.pipeline_dependency_id == dependency.id
            and job.job_type == "FULL_PIPELINE"
            and job.status in ACTIVE_JOB_STATUSES
        ]
        if len(active_continuations) > 1:
            findings.append(
                PipelineInvariantFinding(
                    "MULTIPLE_ACTIVE_DEPENDENCY_CONTINUATIONS",
                    InvariantDisposition.FATAL_STARTUP_INVARIANT,
                    pipeline_id=dependency.pipeline_run_id,
                    dependency_id=dependency.id,
                    detail=",".join(str(job.id) for job in active_continuations),
                )
            )

    for pipeline in pipelines:
        retained = pipeline.result_json or {}
        root_id = retained.get("pipeline_root_job_id") or retained.get("background_job_id")
        root = jobs_by_id.get(int(root_id)) if root_id is not None else None
        active_dependencies = [
            dependency
            for dependency in deps_by_pipeline.get(pipeline.id, [])
            if dependency.state in {"PENDING_ENQUEUE", "QUEUED", "RUNNING"}
        ]
        expected_dependency_type = (
            "SEC_READINESS"
            if pipeline.status == "WAITING_DEPENDENCY"
            else "CERI_WORKFLOW"
            if pipeline.status == "WAITING_FOR_CERI_COMPLETION"
            else None
        )
        valid_terminal_root_handoff = (
            expected_dependency_type is not None
            and len(active_dependencies) == 1
            and active_dependencies[0].dependency_type == expected_dependency_type
            and root is not None
            and active_dependencies[0].root_job_id == root.id
        )
        if (
            pipeline.status not in TERMINAL_PIPELINE_STATES
            and pipeline.status != "CANCEL_REQUESTED"
            and root is not None
            and root.status in TERMINAL_JOB_STATUSES
            and not valid_terminal_root_handoff
        ):
            findings.append(
                PipelineInvariantFinding(
                    "ACTIVE_PIPELINE_WITH_TERMINAL_ROOT_JOB",
                    InvariantDisposition.REQUIRES_OPERATOR_REVIEW,
                    pipeline_id=pipeline.id,
                    job_id=root.id,
                    detail=f"pipeline={pipeline.status};root={root.status}",
                )
            )
        if pipeline.status in {
            "WAITING_DEPENDENCY",
            "WAITING_FOR_CERI_COMPLETION",
        } and not valid_terminal_root_handoff:
            findings.append(
                PipelineInvariantFinding(
                    "WAITING_PIPELINE_DEPENDENCY_CARDINALITY_INVALID",
                    InvariantDisposition.REQUIRES_OPERATOR_REVIEW,
                    pipeline_id=pipeline.id,
                    detail=f"active_dependencies={len(active_dependencies)}",
                )
            )
        live_continuations = [
            job
            for job in jobs
            if _job_pipeline_id(job) == pipeline.id
            and job.job_type == "FULL_PIPELINE"
            and job.pipeline_dependency_id is not None
            and job.status in ACTIVE_JOB_STATUSES
        ]
        if pipeline.status in TERMINAL_PIPELINE_STATES and live_continuations:
            findings.append(
                PipelineInvariantFinding(
                    "TERMINAL_PIPELINE_WITH_LIVE_CONTINUATION",
                    InvariantDisposition.FATAL_STARTUP_INVARIANT,
                    pipeline_id=pipeline.id,
                    detail=",".join(str(job.id) for job in live_continuations),
                )
            )
        if pipeline.status == "CANCEL_REQUESTED":
            live_jobs = [
                job
                for job in jobs
                if _job_pipeline_id(job) == pipeline.id and job.status in ACTIVE_JOB_STATUSES
            ]
            if not live_jobs:
                findings.append(
                    PipelineInvariantFinding(
                        "CANCEL_REQUESTED_WITHOUT_RECONCILIATION_PATH",
                        InvariantDisposition.SAFE_AUTO_RECONCILE,
                        pipeline_id=pipeline.id,
                    )
                )
        step_states = list(
            db.scalars(
                select(PipelineStep.status).where(PipelineStep.pipeline_run_id == pipeline.id)
            )
        )
        if pipeline.status == "COMPLETED" and any(
            state not in {"COMPLETED", "SKIPPED"} for state in step_states
        ):
            findings.append(
                PipelineInvariantFinding(
                    "PIPELINE_STEP_STATE_INCOMPATIBLE",
                    InvariantDisposition.REQUIRES_OPERATOR_REVIEW,
                    pipeline_id=pipeline.id,
                    detail="completed pipeline has unfinished step",
                )
            )
        if pipeline.status == "CANCELLED" and any(
            state in {"PENDING", "RUNNING"} for state in step_states
        ):
            findings.append(
                PipelineInvariantFinding(
                    "PIPELINE_STEP_STATE_INCOMPATIBLE",
                    InvariantDisposition.REQUIRES_OPERATOR_REVIEW,
                    pipeline_id=pipeline.id,
                    detail="cancelled pipeline has active step",
                )
            )
    return tuple(
        replace(
            finding,
            scope=_finding_scope(
                finding,
                executable_job_ids=executable_job_ids,
                pipeline_by_id=pipeline_by_id,
                dependencies_by_id=dependencies_by_id,
            ),
        )
        for finding in findings
    )


def invariant_counts(findings: tuple[PipelineInvariantFinding, ...]) -> dict[str, int]:
    return {
        disposition.value: sum(
            finding.disposition is disposition and finding.operational_actionable
            for finding in findings
        )
        for disposition in InvariantDisposition
    }


def _finding_scope(
    finding: PipelineInvariantFinding,
    *,
    executable_job_ids: set[int],
    pipeline_by_id: dict[int, PipelineRun],
    dependencies_by_id: dict[int, PipelineDependency],
) -> InvariantScope:
    if finding.job_id is not None:
        actionable = finding.job_id in executable_job_ids
    elif finding.pipeline_id is not None:
        actionable = pipeline_execution_is_authorized(
            pipeline_by_id.get(finding.pipeline_id)
        )
    elif finding.dependency_id is not None:
        dependency = dependencies_by_id.get(finding.dependency_id)
        actionable = bool(
            dependency is not None
            and pipeline_execution_is_authorized(
                pipeline_by_id.get(dependency.pipeline_run_id)
            )
        )
    else:
        actionable = True
    return (
        InvariantScope.OPERATIONAL_ACTIONABLE
        if actionable
        else InvariantScope.HISTORICAL_FORENSIC
    )


def _job_pipeline_id(job: BackgroundJob) -> int | None:
    value = (job.payload_json or {}).get("pipeline_run_id")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
