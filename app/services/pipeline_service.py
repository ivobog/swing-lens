from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models.tables import (
    BackgroundJob,
    PipelineDependency,
    PipelineRun,
    PipelineStep,
    RawCompanyRow,
    UploadRun,
)
from app.observability.transaction_metrics import publish_after_commit
from app.services.background_job_service import (
    JobStatus,
    active_job_for_request_key,
    enqueue_job,
    request_job_cancel,
)
from app.services.canonical_evidence import CanonicalEvidenceSerializer
from app.services.ceri.constants import CERI_PIPELINE_PROVIDER_INGEST_STEP, CERI_PIPELINE_STEPS
from app.services.ceri.feature_flags import ceri_flags
from app.services.market_data_prewarm_service import request_active_prewarm_preemption
from app.services.operational_metrics import operational_metrics
from app.services.scope_refresh_adoption import (
    SemanticWorkAuthority,
    admit_frozen_operation,
    bind_semantic_authority,
    require_semantic_authority,
)
from app.services.setup_lifecycle.constants import SLSE_PIPELINE_STEPS
from app.services.work_scope_identity import AcquisitionRequirement, ScopeMember
from app.settings import RuntimeMode, get_settings

FULL_PIPELINE_JOB_TYPE = "FULL_PIPELINE"
PIPELINE_JOB_PRIORITY = 100
PIPELINE_JOB_MAX_RETRIES = 3
PIPELINE_CANCELLATION_LOCK_TIMEOUT_MS = 750
DECISION_HANDOFF_PIPELINE_STEP = "FREEZING_DECISION_HANDOFF_MANIFEST"

PIPELINE_STEP_NAMES_BEFORE_OPTIONAL_RESEARCH = (
    "VALIDATING_RUN",
    "SCORING_FUNDAMENTALS",
    "FETCHING_MARKET_DATA",
    "SCORING_TECHNICALS",
    "MARKET_REGIME_SNAPSHOT",
    "COMBINING_RESULTS",
    "RANKING_PROFILES",
    "SECTOR_ROTATION_SNAPSHOT",
)
PIPELINE_STEP_NAMES = (
    *PIPELINE_STEP_NAMES_BEFORE_OPTIONAL_RESEARCH,
    "CAPTURING_WINNER_PREDICTIONS",
)

PIPELINE_TERMINAL_STATUSES = {
    "COMPLETED",
    "PARTIAL",
    "FAILED",
    "BLOCKED",
    "CANCELLED",
    "FEATURE_CERTIFIED",
}


class MarketDataPolicy(StrEnum):
    REQUIRE_IB = "REQUIRE_IB"
    ALLOW_CACHE_FALLBACK = "ALLOW_CACHE_FALLBACK"


class PipelineStatus:
    QUEUED = "QUEUED"
    PENDING = "PENDING"
    PREPARING = "PREPARING"
    RUNNING = "RUNNING"
    WAITING_DEPENDENCY = "WAITING_DEPENDENCY"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    WAITING_FOR_MARKET_DATA = "WAITING_FOR_MARKET_DATA"
    SCORING_FUNDAMENTALS = "SCORING_FUNDAMENTALS"
    FETCHING_MARKET_DATA = "FETCHING_MARKET_DATA"
    SCORING_TECHNICALS = "SCORING_TECHNICALS"
    MARKET_REGIME_SNAPSHOT = "MARKET_REGIME_SNAPSHOT"
    COMBINING_RESULTS = "COMBINING_RESULTS"
    RANKING_PROFILES = "RANKING_PROFILES"
    SECTOR_ROTATION_SNAPSHOT = "SECTOR_ROTATION_SNAPSHOT"
    FREEZING_DECISION_HANDOFF_MANIFEST = DECISION_HANDOFF_PIPELINE_STEP
    CERI_PROVIDER_INGEST = "CERI_PROVIDER_INGEST"
    WAITING_FOR_CERI_COMPLETION = "WAITING_FOR_CERI_COMPLETION"
    CERI_FEATURE_CERTIFYING = "CERI_FEATURE_CERTIFYING"
    FEATURE_CERTIFIED = "FEATURE_CERTIFIED"
    CERI_CAPTURE_SNAPSHOT = "CERI_CAPTURE_SNAPSHOT"
    CAPTURING_SETUP_SIGNALS = "CAPTURING_SETUP_SIGNALS"
    EVALUATING_SETUP_LIFECYCLES = "EVALUATING_SETUP_LIFECYCLES"
    CAPTURING_WINNER_PREDICTIONS = "CAPTURING_WINNER_PREDICTIONS"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


class PipelineStepStatus:
    PENDING = "PENDING"
    DISPATCHED = "DISPATCHED"
    WAITING_DEPENDENCY = "WAITING_DEPENDENCY"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"
    SKIPPED = "SKIPPED"


class PipelineCancellationContended(RuntimeError):
    def __init__(self, diagnostics: dict[str, Any]):
        super().__init__("PIPELINE_CANCELLATION_LOCK_CONTENDED")
        self.diagnostics = diagnostics


@dataclass(frozen=True)
class PipelineStepStatusDto:
    step_name: str
    step_order: int
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    message: str | None
    error_message: str | None
    retry_count: int
    original_started_at: datetime | None = None
    latest_attempt_started_at: datetime | None = None
    latest_attempt_finished_at: datetime | None = None
    attempt_count: int = 1


@dataclass(frozen=True)
class PipelineStatusDto:
    pipeline_run_id: int
    upload_run_id: int
    status: str
    current_step: str | None
    requested_by: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime | None
    message: str | None
    error_message: str | None
    background_job_id: int | None
    steps: list[PipelineStepStatusDto]
    result_json: dict[str, Any] | None = None
    scope_id: str | None = None
    refresh_cycle_id: str | None = None
    acquisition_plan_id: str | None = None
    scope_size: int | None = None
    required_child_counts: dict[str, int] | None = None


def start_pipeline(
    db: Session,
    upload_run_id: int,
    requested_by: str | None = None,
    ceri_run_capture_enabled: bool | None = None,
    ceri_provider_ingest_enabled: bool | None = None,
    setup_lifecycle_pipeline_step_enabled: bool | None = None,
    market_data_policy: MarketDataPolicy | str = MarketDataPolicy.REQUIRE_IB,
    ib_preflight_status: dict[str, Any] | None = None,
    transition_preflight_plan_id: int | None = None,
    transition_candidate_discovery: Any | None = None,
) -> PipelineRun:
    settings = get_settings()
    certification_mode = (
        getattr(settings, "runtime_mode", RuntimeMode.NORMAL) is RuntimeMode.CERTIFICATION
    )
    upload_run = db.get(UploadRun, upload_run_id)
    if upload_run is None:
        raise ValueError(f"Upload run {upload_run_id} was not found.")

    try:
        policy = MarketDataPolicy(market_data_policy)
    except ValueError as exc:
        raise ValueError(f"Unsupported market data policy: {market_data_policy}") from exc

    verified_transition_preflight = None
    operational_gate_result = None
    if transition_preflight_plan_id is not None:
        from app.services.transition_preflight_plan_service import (
            TransitionPreflightError,
            pipeline_for_consumed_preflight,
            verify_transition_preflight_for_enqueue,
        )

        consumed_pipeline = pipeline_for_consumed_preflight(db, transition_preflight_plan_id)
        if consumed_pipeline is not None:
            consumed_pipeline._coalesced = True
            return consumed_pipeline
        if certification_mode:
            from app.services.pre_enqueue_operational_gate import (
                validate_pre_enqueue_operational_gate,
            )

            operational_gate_result = validate_pre_enqueue_operational_gate(
                db,
                upload_run_id=upload_run_id,
                plan_id=transition_preflight_plan_id,
                settings=settings,
                discovery=transition_candidate_discovery,
            )
            verified_transition_preflight = operational_gate_result.verified_preflight
            ib_preflight_status = operational_gate_result.ib_status.to_dict()
        else:
            verified_transition_preflight = verify_transition_preflight_for_enqueue(
                db,
                plan_id=transition_preflight_plan_id,
                upload_run_id=upload_run_id,
                discovery=transition_candidate_discovery,
            )
    elif certification_mode:
        from app.services.pre_enqueue_operational_gate import PreEnqueueOperationalGateError

        raise PreEnqueueOperationalGateError(
            "CERTIFICATION_PREFLIGHT_REQUIRED",
            "Certification pipeline enqueue requires a reserved transition preflight plan.",
            plan_id=None,
        )

    step_names = pipeline_step_names(
        ceri_run_capture_enabled=ceri_run_capture_enabled,
        ceri_provider_ingest_enabled=ceri_provider_ingest_enabled,
        setup_lifecycle_pipeline_step_enabled=setup_lifecycle_pipeline_step_enabled,
    )
    if verified_transition_preflight is not None or bool(
        getattr(settings, "winner_probability_capture_in_pipeline", False)
    ):
        if CERI_PIPELINE_PROVIDER_INGEST_STEP in step_names:
            handoff_index = step_names.index(CERI_PIPELINE_PROVIDER_INGEST_STEP) + 1
        else:
            handoff_index = next(
                (
                    index
                    for index, step_name in enumerate(step_names)
                    if step_name
                    in {
                        *SLSE_PIPELINE_STEPS,
                        "CAPTURING_WINNER_PREDICTIONS",
                    }
                ),
                len(step_names),
            )
        step_names = (
            *step_names[:handoff_index],
            DECISION_HANDOFF_PIPELINE_STEP,
            *step_names[handoff_index:],
        )
    authoritative = _authoritative_pipeline_for_run(db, upload_run_id)
    if authoritative is not None:
        if verified_transition_preflight is not None:
            raise TransitionPreflightError(
                "PRECONDITION_CHANGED",
                f"upload run already has authoritative pipeline {authoritative.id}",
            )
        if _is_recoverable_sec_block(authoritative):
            from app.services.ceri.sec.readiness_repair import (
                schedule_sec_readiness_repair,
            )

            diagnostics = dict((authoritative.result_json or {}).get("blocked_diagnostics") or {})
            schedule_sec_readiness_repair(
                db,
                pipeline=authoritative,
                diagnostics=diagnostics,
            )
        authoritative._coalesced = True
        publish_after_commit(
            db,
            "increment",
            "swinglens_pipelines_coalesced_total",
            status=authoritative.status,
        )
        return authoritative

    request_key = _pipeline_request_key(upload_run_id, step_names, policy)
    existing_job = active_job_for_request_key(db, FULL_PIPELINE_JOB_TYPE, request_key)
    existing_pipeline = _pipeline_for_job(db, existing_job)
    if existing_pipeline is not None:
        existing_pipeline._coalesced = True
        publish_after_commit(
            db,
            "increment",
            "swinglens_pipelines_coalesced_total",
            status=existing_pipeline.status,
        )
        return existing_pipeline

    pipeline = PipelineRun(
        upload_run_id=upload_run_id,
        status=PipelineStatus.PENDING,
        current_step=step_names[0],
        requested_by=requested_by,
        message="Full pipeline is queued.",
    )
    db.add(pipeline)
    db.flush()

    if verified_transition_preflight is None:
        from app.services.market_calculation_context_service import (
            create_pipeline_market_context,
        )

        market_cutoff = create_pipeline_market_context(db, pipeline)
    else:
        from app.services.transition_preflight_plan_service import (
            consume_transition_preflight,
        )

        market_cutoff = consume_transition_preflight(
            db,
            verified=verified_transition_preflight,
            pipeline=pipeline,
        )

    if isinstance(db, Session):
        authority = _admit_pipeline_authority(
            db,
            pipeline=pipeline,
            step_names=step_names,
            policy=policy,
            market_cutoff=market_cutoff,
        )
        bind_semantic_authority(pipeline, authority)

    for step_order, step_name in enumerate(step_names, start=1):
        db.add(
            PipelineStep(
                pipeline_run_id=pipeline.id,
                step_name=step_name,
                step_order=step_order,
                status=PipelineStepStatus.PENDING,
                retry_count=0,
            )
        )
    db.flush()

    if certification_mode:
        from app.services.certification_runtime import certification_root_payload

        certification_authorization = certification_root_payload(
            plan_id=int(transition_preflight_plan_id), settings=settings
        )
    else:
        certification_authorization = {}
    job = enqueue_job(
        db,
        job_type=FULL_PIPELINE_JOB_TYPE,
        payload={
            "pipeline_run_id": pipeline.id,
            "market_calculation_context_id": market_cutoff.context_id,
            "market_cutoff_at": CanonicalEvidenceSerializer.canonicalize(market_cutoff.cutoff_at),
            "input_as_of_session": market_cutoff.latest_completed_session.isoformat(),
            "market_calendar_version": market_cutoff.calendar_version,
            "bar_readiness_version": market_cutoff.bar_readiness_version,
            "transition_preflight_plan_id": transition_preflight_plan_id,
            **certification_authorization,
            "transition_evidence_fingerprint": (
                verified_transition_preflight.plan.evidence_fingerprint
                if verified_transition_preflight is not None
                else None
            ),
        },
        related_run_id=upload_run_id,
        priority=PIPELINE_JOB_PRIORITY,
        max_retries=PIPELINE_JOB_MAX_RETRIES,
        request_key=request_key,
    )
    if isinstance(db, Session):
        bind_semantic_authority(job, require_semantic_authority(pipeline))
    if getattr(job, "_coalesced", False):
        existing_pipeline = _pipeline_for_job(db, job)
        if existing_pipeline is not None:
            from app.services.pipeline_state_machine import transition_pipeline

            transition_pipeline(
                db,
                pipeline,
                PipelineStatus.CANCELLED,
                actor="pipeline_orchestrator",
                message="Duplicate pipeline request coalesced into an active run.",
            )
            _cancel_pending_steps(db, pipeline.id)
            existing_pipeline._coalesced = True
            db.flush()
            publish_after_commit(
                db,
                "increment",
                "swinglens_pipelines_coalesced_total",
                status=existing_pipeline.status,
            )
            return existing_pipeline

    preflight = dict(ib_preflight_status or {})
    pipeline.result_json = {
        "background_job_id": job.id,
        "market_data_policy": policy.value,
        "ib_preflight_status": preflight.get("status"),
        "ib_preflight_checked_at": preflight.get("checked_at"),
        "ib_host": preflight.get("host", getattr(settings, "ib_host", None)),
        "ib_port": preflight.get("port", getattr(settings, "ib_port", None)),
        "market_calculation_context_id": market_cutoff.context_id,
        "market_cutoff_at": CanonicalEvidenceSerializer.canonicalize(market_cutoff.cutoff_at),
        "input_as_of_session": market_cutoff.latest_completed_session.isoformat(),
        "market_calendar_version": market_cutoff.calendar_version,
        "bar_readiness_version": market_cutoff.bar_readiness_version,
        "transition_preflight_plan_id": transition_preflight_plan_id,
        "transition_evidence_fingerprint": (
            verified_transition_preflight.plan.evidence_fingerprint
            if verified_transition_preflight is not None
            else None
        ),
    }
    if operational_gate_result is not None:
        pipeline.result_json["pre_enqueue_operational_gate"] = operational_gate_result.to_dict()
    preempted_prewarm_jobs = request_active_prewarm_preemption(
        db,
        pipeline_run_id=pipeline.id,
    )
    if preempted_prewarm_jobs:
        pipeline.result_json = {
            **pipeline.result_json,
            "preempted_prewarm_job_ids": preempted_prewarm_jobs,
        }
    db.flush()
    publish_after_commit(db, "increment", "swinglens_pipelines_started_total")
    return pipeline


def pipeline_step_names(
    ceri_run_capture_enabled: bool | None = None,
    ceri_provider_ingest_enabled: bool | None = None,
    setup_lifecycle_pipeline_step_enabled: bool | None = None,
) -> tuple[str, ...]:
    effective_flags = ceri_flags()
    ceri_enabled = (
        effective_flags.run_capture
        if ceri_run_capture_enabled is None
        else effective_flags.enabled and bool(ceri_run_capture_enabled)
    )
    setup_enabled = (
        get_settings().setup_lifecycle_pipeline_step_enabled
        if setup_lifecycle_pipeline_step_enabled is None
        else setup_lifecycle_pipeline_step_enabled
    )
    provider_ingest_enabled = (
        effective_flags.provider_ingest
        if ceri_provider_ingest_enabled is None
        else effective_flags.enabled and bool(ceri_provider_ingest_enabled)
    )
    if provider_ingest_enabled and ceri_provider_ingest_enabled is None:
        settings = get_settings()
        provider_ingest_enabled = bool(
            settings.ceri_legacy_pipeline_scheduling_enabled
            or settings.ceri_batched_workflow_enabled
        )
    if not ceri_enabled and not provider_ingest_enabled and not setup_enabled:
        return PIPELINE_STEP_NAMES
    optional_steps: tuple[str, ...] = ()
    if provider_ingest_enabled:
        optional_steps = (*optional_steps, CERI_PIPELINE_PROVIDER_INGEST_STEP)
    if ceri_enabled and not provider_ingest_enabled:
        optional_steps = (*optional_steps, *CERI_PIPELINE_STEPS)
    if setup_enabled:
        optional_steps = (*optional_steps, *SLSE_PIPELINE_STEPS)
    return (
        *PIPELINE_STEP_NAMES_BEFORE_OPTIONAL_RESEARCH,
        *optional_steps,
        "CAPTURING_WINNER_PREDICTIONS",
    )


def _step_status_dto(step: PipelineStep) -> PipelineStepStatusDto:
    history = list((step.result_json or {}).get("attempt_history") or [])

    def parsed(value: Any) -> datetime | None:
        if value in (None, ""):
            return None
        try:
            result = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
        return result.replace(tzinfo=UTC) if result.tzinfo is None else result

    starts = [value for item in history for value in (parsed(item.get("started_at")),) if value]
    original_started_at = min(starts, default=step.started_at)
    return PipelineStepStatusDto(
        step_name=step.step_name,
        step_order=step.step_order,
        status=step.status,
        started_at=step.started_at,
        completed_at=step.completed_at,
        message=step.message,
        error_message=step.error_message,
        retry_count=step.retry_count,
        original_started_at=original_started_at,
        latest_attempt_started_at=step.started_at,
        latest_attempt_finished_at=step.completed_at,
        attempt_count=int(step.retry_count or 0) + 1,
    )


def _ceri_async_visibility(db: Session, pipeline: PipelineRun) -> dict[str, Any] | None:
    workflow_key = str((pipeline.result_json or {}).get("ceri_provider_workflow_key") or "")
    if not workflow_key.startswith("ceri:pipeline:"):
        return None
    jobs = list(
        db.scalars(
            select(BackgroundJob)
            .where(BackgroundJob.workflow_key == workflow_key)
            .order_by(BackgroundJob.created_at, BackgroundJob.id)
        )
    )
    dependency = db.scalar(
        select(PipelineDependency).where(PipelineDependency.pipeline_run_id == pipeline.id)
    )
    if dependency is not None and dependency.continuation_job_id is not None:
        continuation = db.get(BackgroundJob, dependency.continuation_job_id)
        if continuation is not None:
            jobs.append(continuation)
    phase_types = {
        "provider_acquisition": {"CERI_PROVIDER_INGEST_BATCH"},
        "normalization": {"CERI_NORMALIZE_BATCH"},
        "feature_computation": {"CERI_FEATURE_BATCH"},
        "capture": {"CERI_CAPTURE_RUN"},
        "certification_barrier": {
            "CERI_RUN_FINALIZE",
            "CERI_CHANGE_DETECTION",
            "CERI_ALERT_REBUILD",
        },
        "continuation": {FULL_PIPELINE_JOB_TYPE},
    }

    def phase_view(types: set[str]) -> dict[str, Any]:
        members = [job for job in jobs if job.job_type in types]
        statuses = {str(job.status) for job in members}
        if not members:
            state = "PENDING"
        elif statuses & {JobStatus.FAILED, JobStatus.BLOCKED, JobStatus.CANCELLED}:
            state = "FAILED"
        elif statuses & {JobStatus.RUNNING, JobStatus.RECOVERING, JobStatus.STALLED}:
            state = "RUNNING"
        elif all(status in {JobStatus.COMPLETED, JobStatus.PARTIAL} for status in statuses):
            state = "COMPLETED"
        else:
            state = "DISPATCHED"
        return {
            "state": state,
            "job_count": len(members),
            "completed_jobs": sum(
                job.status in {JobStatus.COMPLETED, JobStatus.PARTIAL} for job in members
            ),
            "processed": sum(int(job.progress_processed or 0) for job in members),
            "total": sum(int(job.progress_total or 0) for job in members),
            "original_started_at": min(
                (job.started_at for job in members if job.started_at is not None),
                default=None,
            ),
            "latest_finished_at": max(
                (job.completed_at for job in members if job.completed_at is not None),
                default=None,
            ),
        }

    return {
        "workflow_key": workflow_key,
        "dependency_state": dependency.state if dependency is not None else None,
        "phases": {name: phase_view(types) for name, types in phase_types.items()},
    }


def get_pipeline_status(db: Session, pipeline_run_id: int) -> PipelineStatusDto:
    pipeline = db.get(PipelineRun, pipeline_run_id)
    if pipeline is None:
        raise ValueError(f"Pipeline run {pipeline_run_id} was not found.")

    steps = _load_pipeline_steps(db, pipeline_run_id)
    scope_size = None
    child_counts = None
    if isinstance(db, Session) and pipeline.scope_id:
        from app.services.scope_refresh_adoption import (
            required_child_counts,
            retained_scope_members,
        )

        scope_size = len(retained_scope_members(db, pipeline.scope_id))
        child_counts = required_child_counts(db, pipeline.scope_id)
    visible_result = dict(pipeline.result_json or {})
    async_visibility = _ceri_async_visibility(db, pipeline)
    if async_visibility is not None:
        visible_result["ceri_async"] = async_visibility
    status = PipelineStatusDto(
        pipeline_run_id=pipeline.id,
        upload_run_id=pipeline.upload_run_id,
        status=pipeline.status,
        current_step=pipeline.current_step,
        requested_by=pipeline.requested_by,
        started_at=pipeline.started_at,
        completed_at=pipeline.completed_at,
        created_at=pipeline.created_at,
        message=pipeline.message,
        error_message=pipeline.error_message,
        background_job_id=_background_job_id(pipeline),
        steps=[_step_status_dto(step) for step in steps],
        result_json=visible_result,
        scope_id=pipeline.scope_id,
        refresh_cycle_id=pipeline.refresh_cycle_id,
        acquisition_plan_id=pipeline.acquisition_plan_id,
        scope_size=scope_size,
        required_child_counts=child_counts,
    )
    failed_repair = _failed_sec_repair(db, pipeline)
    if failed_repair is not None:
        from app.services.background_job_service import classify_job_failure
        from app.services.ceri.sec.readiness_repair import sec_repair_failure_view

        view = sec_repair_failure_view(
            pipeline,
            failed_repair,
            classify_job_failure(failed_repair.error_message or "SEC_REPAIR_FAILED"),
        )
        target = (failed_repair.payload_json or {}).get("resume_from_step") or "VALIDATING_RUN"
        status = replace(
            status,
            status=view["status"],
            message=view["message"],
            error_message=view["detail"],
            completed_at=failed_repair.completed_at,
            result_json={
                **(status.result_json or {}),
                "blocked_reason": view["code"],
                "sec_repair": {
                    **((status.result_json or {}).get("sec_repair") or {}),
                    "repair_stage": view["stage"],
                    "last_error_code": view["code"],
                    "last_error_detail": view["detail"],
                },
            },
            steps=[
                replace(
                    step,
                    status=view["status"],
                    message=view["message"],
                    error_message=view["detail"],
                    completed_at=failed_repair.completed_at,
                )
                if step.step_name == target
                else step
                for step in status.steps
            ],
        )
    failed_execution = _failed_pipeline_job(db, pipeline)
    if failed_execution is not None:
        from app.services.background_job_service import classify_job_failure

        view = _pipeline_job_failure_view(
            pipeline,
            failed_execution,
            classify_job_failure(failed_execution.error_message or "PIPELINE_FAILED"),
        )
        target = (
            (failed_execution.payload_json or {}).get("resume_from_step")
            or pipeline.current_step
            or "VALIDATING_RUN"
        )
        status = replace(
            status,
            status=view["status"],
            message=view["message"],
            error_message=view["detail"],
            completed_at=failed_execution.completed_at,
            steps=[
                replace(
                    step,
                    status=view["status"],
                    message=view["message"],
                    error_message=view["detail"],
                    completed_at=failed_execution.completed_at,
                )
                if step.step_name == target
                else step
                for step in status.steps
            ],
        )
    return status


def _failed_pipeline_job(db, pipeline):
    if pipeline.status in PIPELINE_TERMINAL_STATUSES:
        return None
    job_id = (pipeline.result_json or {}).get("background_job_id")
    job = db.get(BackgroundJob, job_id) if job_id is not None else None
    if (
        job is not None
        and job.job_type == FULL_PIPELINE_JOB_TYPE
        and job.status == JobStatus.FAILED
    ):
        return job
    return None


def _pipeline_job_failure_view(pipeline, job, failure):
    continuation = bool((job.payload_json or {}).get("resume_from_step"))
    message = "Pipeline continuation failed." if continuation else "Pipeline execution failed."
    if not failure["retryable"]:
        message += " A deterministic failure requires remediation or a typed replan."
    diagnostics = failure.get("diagnostics") or {}
    affected = ", ".join(
        f"{item['ticker']}/{item['feed']}:{item['reason']}"
        for item in diagnostics.get("failed_items", [])[:12]
    )
    detail = f"{message} Pipeline {pipeline.id}, job {job.id}: {failure['code']}."
    if affected:
        detail += f" Failed acquisition items: {affected}."
    return {
        "status": PipelineStatus.FAILED if failure["retryable"] else PipelineStatus.BLOCKED,
        "message": message,
        "detail": detail,
    }


def mark_pipeline_job_failure(db, job, failure):
    pipeline = db.scalar(
        select(PipelineRun)
        .where(PipelineRun.result_json["background_job_id"].astext == str(job.id))
        .with_for_update()
    )
    if pipeline is None or (
        pipeline.status in PIPELINE_TERMINAL_STATUSES and pipeline.status != PipelineStatus.FAILED
    ):
        return
    if (pipeline.result_json or {}).get("background_job_id") != job.id:
        return
    view = _pipeline_job_failure_view(pipeline, job, failure)
    # The stage wrapper may already have marked FAILED before the durable job
    # classifier runs. Preserve that terminal state while adding the typed,
    # ticker-level failure reason; never erase earlier stage evidence.
    already_failed = pipeline.status == PipelineStatus.FAILED
    from app.services.pipeline_state_machine import transition_pipeline, transition_pipeline_step

    transition_pipeline(
        db,
        pipeline,
        PipelineStatus.FAILED if already_failed else view["status"],
        actor="pipeline_orchestrator",
        message=view["message"],
        error_message=view["detail"],
        completed_at=job.completed_at or _utcnow(),
    )
    pipeline.result_json = {
        **(pipeline.result_json or {}),
        "blocked_reason": failure["code"],
        "job_failure_classification": failure,
    }
    target = pipeline.current_step or "VALIDATING_RUN"
    step = db.scalar(
        select(PipelineStep).where(
            PipelineStep.pipeline_run_id == pipeline.id, PipelineStep.step_name == target
        )
    )
    if step is not None:
        transition_pipeline_step(
            db,
            step,
            PipelineStepStatus.FAILED if already_failed else view["status"],
            completed_at=pipeline.completed_at,
            message=view["message"],
            error_message=view["detail"],
        )
    db.flush()


def _failed_sec_repair(db, pipeline):
    if pipeline.status != PipelineStatus.PREPARING:
        return None
    repair_id = (pipeline.result_json or {}).get("repair_job_id")
    job = db.get(BackgroundJob, repair_id) if repair_id is not None else None
    if (
        job is not None
        and job.job_type == "SEC_READINESS_REPAIR"
        and job.status == JobStatus.FAILED
    ):
        return job
    return None


def cancel_pipeline(db: Session, pipeline_run_id: int) -> PipelineRun:
    cancellation_started_at = datetime.now(UTC)
    if isinstance(db, Session):
        db.execute(text(f"SET LOCAL lock_timeout = '{PIPELINE_CANCELLATION_LOCK_TIMEOUT_MS}ms'"))
        try:
            pipeline = db.scalar(
                select(PipelineRun)
                .where(PipelineRun.id == pipeline_run_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        except OperationalError as exc:
            db.rollback()
            if getattr(getattr(exc, "orig", None), "sqlstate", None) != "55P03":
                raise
            diagnostics = _pipeline_cancellation_lock_diagnostics(
                db, pipeline_id=pipeline_run_id, job_id=0
            )
            operational_metrics.increment("swinglens_pipeline_cancellation_lock_contention_total")
            raise PipelineCancellationContended(diagnostics) from exc
    else:
        pipeline = db.get(PipelineRun, pipeline_run_id)
    if pipeline is None:
        raise ValueError(f"Pipeline run {pipeline_run_id} was not found.")

    if pipeline.status in PIPELINE_TERMINAL_STATUSES:
        return pipeline
    from app.services.pipeline_state_machine import transition_pipeline

    transition_pipeline(
        db,
        pipeline,
        PipelineStatus.CANCEL_REQUESTED,
        actor="user_control",
        message="Pipeline cancellation requested.",
    )
    # Publish cancellation intent without waiting for a control-row lock. This
    # transaction is the durable convergence point even if a job row is busy.
    if isinstance(db, Session):
        db.commit()

    target_ids = _active_pipeline_control_job_ids(db, pipeline_run_id)
    for job_id in target_ids:
        try:
            if isinstance(db, Session):
                db.execute(
                    text(f"SET LOCAL lock_timeout = '{PIPELINE_CANCELLATION_LOCK_TIMEOUT_MS}ms'")
                )
            request_job_cancel(db, job_id)
            if isinstance(db, Session):
                db.commit()
        except OperationalError as exc:
            if isinstance(db, Session):
                db.rollback()
            if getattr(getattr(exc, "orig", None), "sqlstate", None) != "55P03":
                raise
            diagnostics = _pipeline_cancellation_lock_diagnostics(
                db, pipeline_id=pipeline_run_id, job_id=job_id
            )
            operational_metrics.increment("swinglens_pipeline_cancellation_lock_contention_total")
            raise PipelineCancellationContended(diagnostics) from exc

    pipeline = (
        db.scalar(
            select(PipelineRun)
            .where(PipelineRun.id == pipeline_run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if isinstance(db, Session)
        else db.get(PipelineRun, pipeline_run_id)
    )
    if pipeline is None:
        raise ValueError(f"Pipeline run {pipeline_run_id} was not found.")
    # Another authoritative transaction may have won while cancellation was
    # being published to child jobs.  Terminal observation is idempotent and
    # must never be rewritten as FAILED -> CANCELLED.
    if pipeline.status in PIPELINE_TERMINAL_STATUSES:
        db.flush()
        return pipeline
    remaining = _active_pipeline_control_job_ids(db, pipeline_run_id)
    if not remaining:
        transition_pipeline(
            db,
            pipeline,
            PipelineStatus.CANCELLED,
            actor="pipeline_orchestrator",
            message="Pipeline cancellation completed.",
        )
        _cancel_pending_steps(db, pipeline_run_id)
        if isinstance(db, Session):
            for dependency in db.scalars(
                select(PipelineDependency).where(
                    PipelineDependency.pipeline_run_id == pipeline_run_id,
                    PipelineDependency.state.in_(("PENDING_ENQUEUE", "QUEUED", "RUNNING")),
                )
            ):
                dependency.state = "CANCELLED"
                dependency.completed_at = _utcnow()
                dependency.updated_at = dependency.completed_at
    db.flush()
    publish_after_commit(
        db,
        "increment",
        "swinglens_pipelines_cancel_requested_total",
        status=pipeline.status,
    )
    publish_after_commit(
        db,
        "observe",
        "swinglens_pipeline_cancellation_wait_seconds",
        value=max(0.0, (datetime.now(UTC) - cancellation_started_at).total_seconds()),
        status=pipeline.status,
    )
    return pipeline


def _active_pipeline_control_job_ids(db: Session, pipeline_id: int) -> tuple[int, ...]:
    pipeline = db.get(PipelineRun, pipeline_id)
    if pipeline is None:
        return ()
    candidates = {
        int(value)
        for value in (
            (pipeline.result_json or {}).get("background_job_id"),
            (pipeline.result_json or {}).get("repair_job_id"),
            (pipeline.result_json or {}).get("continuation_job_id"),
        )
        if value is not None
    }
    if isinstance(db, Session):
        candidates.update(
            int(value)
            for value in db.scalars(
                select(BackgroundJob.id)
                .join(
                    PipelineDependency,
                    BackgroundJob.pipeline_dependency_id == PipelineDependency.id,
                )
                .where(PipelineDependency.pipeline_run_id == pipeline_id)
            )
        )
    if not candidates:
        return ()
    return tuple(
        job_id
        for job_id in sorted(candidates)
        if (job := db.get(BackgroundJob, job_id)) is not None
        and job.status in (JobStatus.QUEUED, JobStatus.RUNNING)
    )


def _pipeline_cancellation_lock_diagnostics(
    db: Session,
    *,
    pipeline_id: int,
    job_id: int,
) -> dict[str, Any]:
    rows = db.execute(
        text(
            """
            SELECT pid, state, wait_event_type, wait_event,
                   EXTRACT(EPOCH FROM (clock_timestamp() - xact_start)) AS transaction_age_seconds,
                   pg_blocking_pids(pid) AS blocking_pids,
                   left(query, 500) AS query
            FROM pg_stat_activity
            WHERE datname = current_database()
              AND xact_start IS NOT NULL
            ORDER BY xact_start
            LIMIT 20
            """
        )
    ).mappings()
    return {
        "code": "PIPELINE_CANCELLATION_LOCK_CONTENDED",
        "pipeline_id": pipeline_id,
        "job_id": job_id,
        "lock_timeout_ms": PIPELINE_CANCELLATION_LOCK_TIMEOUT_MS,
        "sessions": [
            {
                "pid": row["pid"],
                "state": row["state"],
                "wait_event_type": row["wait_event_type"],
                "wait_event": row["wait_event"],
                "transaction_age_seconds": float(row["transaction_age_seconds"] or 0),
                "blocking_pids": list(row["blocking_pids"] or []),
                "query": row["query"],
            }
            for row in rows
        ],
    }


def resume_pipeline(
    db: Session,
    pipeline_run_id: int,
    *,
    resume_from_step: str | None = None,
) -> PipelineRun:
    pipeline = db.get(PipelineRun, pipeline_run_id)
    if pipeline is None:
        raise ValueError(f"Pipeline run {pipeline_run_id} was not found.")
    authority = require_semantic_authority(pipeline) if isinstance(db, Session) else None
    failed_repair = _failed_sec_repair(db, pipeline)
    if failed_repair is not None:
        from app.services.background_job_service import classify_job_failure
        from app.services.ceri.sec.readiness_repair import mark_sec_repair_failure

        mark_sec_repair_failure(
            db,
            failed_repair,
            failed_repair.error_message,
            classify_job_failure(failed_repair.error_message or "SEC_REPAIR_FAILED"),
        )
    failed_execution = _failed_pipeline_job(db, pipeline)
    if failed_execution is not None:
        from app.services.background_job_service import classify_job_failure

        mark_pipeline_job_failure(
            db,
            failed_execution,
            classify_job_failure(failed_execution.error_message or "PIPELINE_FAILED"),
        )
    if pipeline.status not in {
        PipelineStatus.BLOCKED,
        PipelineStatus.FAILED,
        PipelineStatus.PARTIAL,
    }:
        raise ValueError("Only BLOCKED, FAILED, or PARTIAL pipelines can be resumed.")
    steps = _load_pipeline_steps(db, pipeline_run_id)
    target = resume_from_step or next(
        (
            step.step_name
            for step in steps
            if step.status
            in {
                PipelineStepStatus.BLOCKED,
                PipelineStepStatus.FAILED,
                PipelineStepStatus.PENDING,
            }
        ),
        None,
    )
    if target is None:
        raise ValueError("Pipeline has no incomplete stage to resume.")
    target_step = next((step for step in steps if step.step_name == target), None)
    if target_step is None:
        raise ValueError(f"Pipeline has no stage named {target}.")
    invalid_prior = [
        step.step_name
        for step in steps
        if step.step_order < target_step.step_order
        and step.status not in {PipelineStepStatus.COMPLETED, PipelineStepStatus.SKIPPED}
    ]
    if invalid_prior:
        raise ValueError(
            "Cannot resume while prior stages are incomplete: " + ", ".join(invalid_prior)
        )
    request_key = (
        f"resume-pipeline:{pipeline.id}:from:{target}:attempt:{target_step.retry_count + 1}"
    )
    job = enqueue_job(
        db,
        job_type=FULL_PIPELINE_JOB_TYPE,
        payload={
            "pipeline_run_id": pipeline.id,
            "resume_from_step": target,
            **_pipeline_context_payload(db, pipeline),
        },
        related_run_id=pipeline.upload_run_id,
        priority=PIPELINE_JOB_PRIORITY,
        max_retries=PIPELINE_JOB_MAX_RETRIES,
        request_key=request_key,
    )
    if authority is not None:
        bind_semantic_authority(job, authority)
    from app.services.pipeline_state_machine import transition_pipeline

    transition_pipeline(
        db,
        pipeline,
        PipelineStatus.QUEUED,
        actor="operator",
        operator_resume=True,
        current_step=target,
        message=f"Pipeline resume queued from {target}.",
    )
    pipeline.result_json = {
        **(pipeline.result_json or {}),
        "background_job_id": job.id,
        "resume_from_step": target,
    }
    db.flush()
    return pipeline


def existing_sec_repair_continuation(db, pipeline, *, processor_signature, resume_from_step):
    if not isinstance(db, Session):
        return None
    from app.services.configuration_delivery import (
        binding_reference,
        execution_configuration_reference,
    )

    request_key = (
        f"resume-pipeline:{pipeline.id}:after-sec-repair:{processor_signature}:"
        f"from:{resume_from_step}"
    )
    existing = db.scalar(
        select(BackgroundJob)
        .where(
            BackgroundJob.job_type == FULL_PIPELINE_JOB_TYPE,
            BackgroundJob.request_key == request_key,
        )
        .order_by(BackgroundJob.id)
        .limit(1)
    )
    if existing is not None:
        expected = binding_reference(db, pipeline_run_id=pipeline.id)
        if execution_configuration_reference(db, existing) != expected:
            raise ValueError("CONFIGURATION_ANCHOR_PARENT_MISMATCH")
    return existing


def enqueue_pipeline_after_sec_repair(
    db: Session,
    pipeline: PipelineRun,
    *,
    processor_signature: str,
    resume_from_step: str = "VALIDATING_RUN",
) -> BackgroundJob:
    authority = require_semantic_authority(pipeline) if isinstance(db, Session) else None
    if isinstance(db, Session):
        db.execute(select(PipelineRun.id).where(PipelineRun.id == pipeline.id).with_for_update())
    step = next(
        (
            item
            for item in _load_pipeline_steps(db, pipeline.id)
            if item.step_name == resume_from_step
        ),
        None,
    )
    if step is None:
        raise ValueError(f"Pipeline has no {resume_from_step} stage.")
    request_key = (
        f"resume-pipeline:{pipeline.id}:after-sec-repair:{processor_signature}:"
        f"from:{resume_from_step}"
    )
    existing = existing_sec_repair_continuation(
        db, pipeline, processor_signature=processor_signature, resume_from_step=resume_from_step
    )
    if existing is not None:
        if authority is not None:
            bind_semantic_authority(existing, authority)
        return existing
    job = enqueue_job(
        db,
        job_type=FULL_PIPELINE_JOB_TYPE,
        payload={
            "pipeline_run_id": pipeline.id,
            "resume_from_step": resume_from_step,
            **_pipeline_context_payload(db, pipeline),
        },
        related_run_id=pipeline.upload_run_id,
        priority=PIPELINE_JOB_PRIORITY,
        max_retries=PIPELINE_JOB_MAX_RETRIES,
        request_key=request_key,
        workflow_key=f"pipeline:{pipeline.id}:sec-continuation",
    )
    if authority is not None:
        bind_semantic_authority(job, authority)
    if getattr(job, "_coalesced", False):
        return job
    from app.services.pipeline_state_machine import transition_pipeline, transition_pipeline_step

    transition_pipeline(
        db,
        pipeline,
        PipelineStatus.QUEUED,
        actor="dependency",
        current_step=resume_from_step,
        message="SEC preparation completed; pipeline continuation queued.",
    )
    transition_pipeline_step(
        db,
        step,
        PipelineStepStatus.PENDING,
        message="SEC preparation completed; continuing pipeline.",
    )
    pipeline.result_json = {
        **(pipeline.result_json or {}),
        "background_job_id": job.id,
        "resume_from_step": resume_from_step,
        "blocked_reason": None,
        "blocked_diagnostics": None,
    }
    db.flush()
    return job


def enqueue_pipeline_after_ceri_completion(
    db: Session,
    trigger_job: BackgroundJob,
) -> BackgroundJob | None:
    """Release a provider-owned pipeline exactly once after certified CERI completion."""

    from sqlalchemy import func

    from app.models.ceri_tables import CeriScoreSnapshot
    from app.services.configuration_delivery import (
        binding_reference,
        execution_configuration_reference,
    )

    payload = trigger_job.payload_json or {}
    pipeline_id = payload.get("pipeline_run_id")
    workflow_key = str(trigger_job.workflow_key or payload.get("workflow_key") or "")
    if pipeline_id is None or not workflow_key.startswith("ceri:pipeline:"):
        return None

    pipeline = db.scalar(
        select(PipelineRun).where(PipelineRun.id == int(pipeline_id)).with_for_update()
    )
    if pipeline is None:
        raise ValueError(f"Pipeline run {pipeline_id} was not found.")
    retained = dict(pipeline.result_json or {})
    retained_workflow_key = retained.get("ceri_provider_workflow_key")
    if retained_workflow_key is None:
        return None
    if retained_workflow_key != workflow_key:
        raise ValueError("CERI_PROVIDER_WORKFLOW_IDENTITY_MISMATCH")
    if pipeline.status in PIPELINE_TERMINAL_STATUSES:
        return None

    workflow_jobs = list(
        db.scalars(
            select(BackgroundJob)
            .where(BackgroundJob.workflow_key == workflow_key)
            .order_by(BackgroundJob.id)
        )
    )
    effective_statuses = {
        row.id: (
            JobStatus.PARTIAL
            if row.id == trigger_job.id and trigger_job.status == JobStatus.PARTIAL
            else JobStatus.COMPLETED
            if row.id == trigger_job.id
            else row.status
        )
        for row in workflow_jobs
    }
    nonterminal = [
        row
        for row in workflow_jobs
        if effective_statuses[row.id]
        not in {
            JobStatus.COMPLETED,
            JobStatus.PARTIAL,
            JobStatus.FAILED,
            JobStatus.BLOCKED,
            JobStatus.CANCELLED,
            JobStatus.STALE,
        }
    ]
    if nonterminal:
        return None
    unsuccessful = [
        row for row in workflow_jobs if effective_statuses[row.id] != JobStatus.COMPLETED
    ]
    if unsuccessful:
        _roll_up_ceri_pipeline_failure(
            db,
            pipeline,
            workflow_key=workflow_key,
            failed_jobs=unsuccessful,
        )
        return None

    expected = int(
        db.scalar(
            select(func.count(func.distinct(RawCompanyRow.ticker))).where(
                RawCompanyRow.run_id == pipeline.upload_run_id
            )
        )
        or 0
    )
    context_id = int(payload.get("calculation_context_id") or 0)
    certified = int(
        db.scalar(
            select(func.count(func.distinct(CeriScoreSnapshot.ticker))).where(
                CeriScoreSnapshot.run_id == pipeline.upload_run_id,
                CeriScoreSnapshot.calculation_context_id == context_id,
                CeriScoreSnapshot.evidence_id.is_not(None),
            )
        )
        or 0
    )
    if expected <= 0 or certified != expected:
        _roll_up_ceri_pipeline_failure(
            db,
            pipeline,
            workflow_key=workflow_key,
            failed_jobs=(),
            reason=(
                "CERI_CERTIFIED_CAPTURE_INCOMPLETE: "
                f"expected {expected} certified run snapshots, observed {certified}"
            ),
        )
        return None

    resume_from_step = DECISION_HANDOFF_PIPELINE_STEP
    request_key = f"resume-pipeline:{pipeline.id}:after-ceri:{workflow_key}:from:{resume_from_step}"
    existing = db.scalar(
        select(BackgroundJob)
        .where(
            BackgroundJob.job_type == FULL_PIPELINE_JOB_TYPE,
            BackgroundJob.request_key == request_key,
        )
        .order_by(BackgroundJob.id)
        .limit(1)
    )
    if existing is not None:
        expected_anchor = binding_reference(db, pipeline_run_id=pipeline.id)
        if execution_configuration_reference(db, existing) != expected_anchor:
            raise ValueError("CONFIGURATION_ANCHOR_PARENT_MISMATCH")
        from app.services.pipeline_dependency_service import (
            complete_ceri_workflow_dependency,
        )

        complete_ceri_workflow_dependency(
            db,
            pipeline=pipeline,
            workflow_key=workflow_key,
            trigger_job=trigger_job,
            continuation=existing,
            certified_count=certified,
        )
        return existing

    authority = require_semantic_authority(pipeline)
    continuation = enqueue_job(
        db,
        job_type=FULL_PIPELINE_JOB_TYPE,
        payload={
            "pipeline_run_id": pipeline.id,
            "resume_from_step": resume_from_step,
            "ceri_provider_workflow_key": workflow_key,
            **_pipeline_context_payload(db, pipeline),
        },
        related_run_id=pipeline.upload_run_id,
        priority=PIPELINE_JOB_PRIORITY,
        max_retries=PIPELINE_JOB_MAX_RETRIES,
        request_key=request_key,
        workflow_key=f"pipeline:{pipeline.id}:ceri-continuation",
        parent_job_id=trigger_job.id,
        trigger_source="CERI_COMPLETION_BARRIER",
    )
    bind_semantic_authority(continuation, authority)
    from app.services.pipeline_dependency_service import complete_ceri_workflow_dependency

    complete_ceri_workflow_dependency(
        db,
        pipeline=pipeline,
        workflow_key=workflow_key,
        trigger_job=trigger_job,
        continuation=continuation,
        certified_count=certified,
    )
    if getattr(continuation, "_coalesced", False):
        return continuation

    from app.services.pipeline_state_machine import transition_pipeline

    transition_pipeline(
        db,
        pipeline,
        PipelineStatus.PENDING,
        actor="dependency",
        current_step=resume_from_step,
        message="Certified CERI workflow completed; downstream continuation queued.",
    )
    provider_step = db.scalar(
        select(PipelineStep).where(
            PipelineStep.pipeline_run_id == pipeline.id,
            PipelineStep.step_name == CERI_PIPELINE_PROVIDER_INGEST_STEP,
        )
    )
    if provider_step is not None:
        provider_step.status = PipelineStepStatus.COMPLETED
        provider_step.completed_at = _utcnow()
        provider_step.message = "Required CERI child workflow completed and certified."
    pipeline.result_json = {
        **retained,
        "background_job_id": continuation.id,
        "resume_from_step": resume_from_step,
        "ceri_completion_state": "CERTIFIED",
        "ceri_async_state": "CONTINUATION_QUEUED",
        "ceri_certified_capture_count": certified,
        "ceri_continuation_request_key": request_key,
        "ceri_continuation_job_id": continuation.id,
    }
    _finalize_ceri_async_timing(
        db,
        pipeline=pipeline,
        workflow_key=workflow_key,
        provider_step=provider_step,
        status=PipelineStepStatus.COMPLETED,
    )
    db.flush()
    return continuation


def _ceri_child_failure_detail(job: BackgroundJob) -> dict[str, Any]:
    payload = dict(job.payload_json or {})
    result = dict(job.result_json or {})
    metadata = dict(job.operational_metadata_json or {})
    classification = dict(
        result.get("failure_classification") or metadata.get("failure_classification") or {}
    )
    return {
        "job_id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "ceri_stage": job.progress_stage or job.job_type,
        "feature_batch": payload.get("batch_index"),
        "calculation_context_id": payload.get("calculation_context_id"),
        "pipeline_run_id": payload.get("pipeline_run_id"),
        "upload_run_id": payload.get("run_id") or job.related_run_id,
        "classification": classification or None,
        "reason": classification.get("code") or job.error_message,
    }


def _finalize_ceri_async_timing(
    db: Session,
    *,
    pipeline: PipelineRun,
    workflow_key: str,
    provider_step: PipelineStep | None,
    status: str,
) -> None:
    if provider_step is None:
        return
    completed_at = provider_step.completed_at or pipeline.completed_at or _utcnow()
    step_timing = dict((provider_step.result_json or {}).get("async_timing") or {})
    dispatch_ms = _optional_float(step_timing.get("dispatch_duration_ms"))
    wait_started = _optional_datetime(step_timing.get("dependency_wait_started_at"))
    dependency_wait_ms = (
        max(0.0, (completed_at - wait_started).total_seconds() * 1000)
        if wait_started is not None
        else None
    )
    total_ms = (
        max(0.0, (completed_at - provider_step.started_at).total_seconds() * 1000)
        if provider_step.started_at is not None
        else None
    )
    children = list(
        db.scalars(select(BackgroundJob).where(BackgroundJob.workflow_key == workflow_key))
    )
    child_starts = [row.started_at for row in children if row.started_at is not None]
    child_finishes = [row.completed_at for row in children if row.completed_at is not None]
    child_execution_ms = None
    if child_starts:
        child_completed_at = max([*child_finishes, completed_at])
        child_execution_ms = max(
            0.0,
            (child_completed_at - min(child_starts)).total_seconds() * 1000,
        )
    timing = {
        "dispatch_duration_ms": dispatch_ms,
        "dependency_wait_duration_ms": round(dependency_wait_ms, 3)
        if dependency_wait_ms is not None
        else None,
        "child_execution_duration_ms": round(child_execution_ms, 3)
        if child_execution_ms is not None
        else None,
        "total_logical_duration_ms": round(total_ms, 3) if total_ms is not None else None,
        "dependency_wait_started_at": (
            wait_started.isoformat() if wait_started is not None else None
        ),
        "completed_at": completed_at.isoformat(),
        "status": status,
    }
    provider_step.result_json = {
        **(provider_step.result_json or {}),
        "async_timing": timing,
    }
    retained = dict(pipeline.result_json or {})
    performance = dict(retained.get("performance") or {})
    async_timings = dict(performance.get("async_step_timings") or {})
    async_timings[CERI_PIPELINE_PROVIDER_INGEST_STEP] = timing
    performance["async_step_timings"] = async_timings
    if total_ms is not None:
        durations = dict(performance.get("step_durations_ms") or {})
        durations[CERI_PIPELINE_PROVIDER_INGEST_STEP] = round(total_ms, 3)
        performance["step_durations_ms"] = durations
    pipeline.result_json = {**retained, "performance": performance}


def _optional_float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_datetime(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def roll_up_ceri_pipeline_job_failure(db: Session, job: BackgroundJob) -> None:
    """Expose a terminal provider-DAG failure on its waiting parent pipeline."""

    payload = job.payload_json or {}
    pipeline_id = payload.get("pipeline_run_id")
    workflow_key = str(job.workflow_key or payload.get("workflow_key") or "")
    if pipeline_id is None or not workflow_key.startswith(
        ("ceri:pipeline:", "ceri:feature-certification:")
    ):
        return
    pipeline = db.scalar(
        select(PipelineRun).where(PipelineRun.id == int(pipeline_id)).with_for_update()
    )
    if pipeline is None or pipeline.status in PIPELINE_TERMINAL_STATUSES:
        return
    retained = pipeline.result_json or {}
    expected_workflow_key = (
        retained.get("feature_certification_workflow_key")
        if workflow_key.startswith("ceri:feature-certification:")
        else retained.get("ceri_provider_workflow_key")
    )
    if expected_workflow_key != workflow_key:
        return
    _roll_up_ceri_pipeline_failure(
        db,
        pipeline,
        workflow_key=workflow_key,
        failed_jobs=(job,),
    )


def _roll_up_ceri_pipeline_failure(
    db: Session,
    pipeline: PipelineRun,
    *,
    workflow_key: str,
    failed_jobs: tuple[BackgroundJob, ...] | list[BackgroundJob],
    reason: str | None = None,
) -> None:
    details = [_ceri_child_failure_detail(row) for row in failed_jobs]
    partial_only = bool(details) and all(row["status"] == JobStatus.PARTIAL for row in details)
    feature_certification = workflow_key.startswith("ceri:feature-certification:")
    message = (
        "CERI feature-only certification did not complete successfully."
        if feature_certification
        else "Required CERI provider workflow did not complete successfully."
    )
    error_message = reason or (
        "CERI_FEATURE_CERTIFICATION_FAILED"
        if feature_certification
        else "CERI_PROVIDER_WORKFLOW_FAILED"
    )
    if not feature_certification:
        from app.services.pipeline_dependency_service import (
            fail_ceri_workflow_dependency,
        )

        fail_ceri_workflow_dependency(
            db,
            pipeline=pipeline,
            workflow_key=workflow_key,
            error_message=error_message,
        )
    from app.services.pipeline_state_machine import transition_pipeline

    transition_pipeline(
        db,
        pipeline,
        PipelineStatus.PARTIAL if partial_only else PipelineStatus.FAILED,
        actor="dependency",
        message=message,
        error_message=error_message,
    )
    pipeline.result_json = {
        **(pipeline.result_json or {}),
        "ceri_completion_state": "FAILED",
        **(
            {
                "feature_certification_state": "FAILED",
                "feature_certification_workflow_key": workflow_key,
            }
            if feature_certification
            else {"ceri_provider_workflow_key": workflow_key}
        ),
        "ceri_failure_jobs": details,
        "ceri_failure_detail": details[0] if details else None,
        "ceri_failure_reason": reason
        or (
            "CERI_FEATURE_CERTIFICATION_FAILED"
            if feature_certification
            else "CERI_PROVIDER_WORKFLOW_FAILED"
        ),
    }
    step = next(
        (
            row
            for row in _load_pipeline_steps(db, pipeline.id)
            if row.step_name
            == (
                "CERI_FEATURE_CERTIFICATION"
                if feature_certification
                else CERI_PIPELINE_PROVIDER_INGEST_STEP
            )
        ),
        None,
    )
    if step is not None:
        step.status = PipelineStepStatus.FAILED
        step.completed_at = pipeline.completed_at
        failure_code = ((details[0].get("classification") or {}).get("code")) if details else None
        step.message = (
            f"{pipeline.message} Child job {details[0]['job_id']} failed: {failure_code}."
            if failure_code
            else pipeline.message
        )
        step.error_message = pipeline.error_message
    _finalize_ceri_async_timing(
        db,
        pipeline=pipeline,
        workflow_key=workflow_key,
        provider_step=step,
        status=PipelineStepStatus.FAILED,
    )
    if feature_certification:
        upload_run = db.get(UploadRun, pipeline.upload_run_id)
        if upload_run is not None:
            upload_run.status = PipelineStatus.FAILED
            upload_run.processed_at = pipeline.completed_at
            upload_run.error_message = pipeline.error_message
    _block_queued_ceri_siblings(
        db,
        workflow_key=workflow_key,
        failed_job_ids={int(row.id) for row in failed_jobs if row.id is not None},
        completed_at=pipeline.completed_at,
        pipeline_id=pipeline.id,
    )
    db.flush()


def _block_queued_ceri_siblings(
    db: Session,
    *,
    workflow_key: str,
    failed_job_ids: set[int],
    completed_at: datetime,
    pipeline_id: int,
) -> None:
    """Block unclaimed siblings in the same terminal-transition transaction.

    ``skip_locked`` avoids lock inversion with a concurrently claimed/running
    sibling. Such a sibling is fenced by the authoritative pipeline row before
    its next domain checkpoint.
    """

    siblings = list(
        db.scalars(
            select(BackgroundJob)
            .where(
                BackgroundJob.workflow_key == workflow_key,
                BackgroundJob.status.in_(
                    (JobStatus.QUEUED, JobStatus.STALLED, JobStatus.RECOVERING)
                ),
            )
            .with_for_update(skip_locked=True)
        )
    )
    for sibling in siblings:
        if sibling.id in failed_job_ids:
            continue
        certification_root_failed = (
            workflow_key.startswith("ceri:feature-certification:")
            and sibling.job_type == "CERI_FEATURE_CERTIFICATION"
        )
        sibling.status = JobStatus.FAILED if certification_root_failed else JobStatus.BLOCKED
        sibling.completed_at = completed_at
        sibling.error_message = (
            "Required feature-certification child failed."
            if certification_root_failed
            else "Authoritative parent pipeline is terminal."
        )
        sibling.locked_at = None
        sibling.heartbeat_at = None
        sibling.lease_expires_at = None
        sibling.worker_id = None
        sibling.worker_instance_id = None
        sibling.lease_owner = None
        sibling.execution_token = None
        sibling.result_json = {
            **(sibling.result_json or {}),
            "status": sibling.status,
            "reason_code": (
                "CERI_FEATURE_CERTIFICATION_CHILD_FAILED"
                if certification_root_failed
                else "CERI_PARENT_PIPELINE_TERMINAL"
            ),
            "diagnostics": {
                "pipeline_run_id": pipeline_id,
                "failed_job_ids": sorted(failed_job_ids),
            },
        }


def _load_pipeline_steps(db: Session, pipeline_run_id: int) -> list[PipelineStep]:
    return list(
        db.scalars(
            select(PipelineStep)
            .where(PipelineStep.pipeline_run_id == pipeline_run_id)
            .order_by(PipelineStep.step_order.asc())
        ).all()
    )


def _cancel_pending_steps(db: Session, pipeline_run_id: int) -> None:
    for step in _load_pipeline_steps(db, pipeline_run_id):
        if step.status == PipelineStepStatus.PENDING:
            step.status = PipelineStepStatus.CANCELLED
            step.completed_at = _utcnow()


def _background_job_id(pipeline: PipelineRun) -> int | None:
    result = pipeline.result_json or {}
    value = result.get("background_job_id")
    return int(value) if value is not None else None


def _pipeline_context_payload(db: Session, pipeline: PipelineRun) -> dict[str, Any]:
    from app.services.market_calculation_context_service import market_context_for_pipeline

    context = market_context_for_pipeline(db, pipeline)
    payload = {
        "market_calculation_context_id": context.context_id,
        "market_cutoff_at": CanonicalEvidenceSerializer.canonicalize(context.cutoff_at),
        "input_as_of_session": context.latest_completed_session.isoformat(),
        "market_calendar_version": context.calendar_version,
        "bar_readiness_version": context.bar_readiness_version,
    }
    if isinstance(db, Session):
        payload.update(require_semantic_authority(pipeline).as_dict())
    return payload


def _admit_pipeline_authority(
    db: Session,
    *,
    pipeline: PipelineRun,
    step_names: tuple[str, ...],
    policy: MarketDataPolicy,
    market_cutoff: Any,
) -> SemanticWorkAuthority:
    tickers = tuple(
        dict.fromkeys(
            str(value).strip().upper()
            for value in db.scalars(
                select(RawCompanyRow.ticker)
                .where(RawCompanyRow.run_id == pipeline.upload_run_id)
                .order_by(RawCompanyRow.row_number)
            )
            if str(value).strip()
        )
    )
    members = tuple(ScopeMember("TICKER", ticker) for ticker in tickers)
    cutoff = market_cutoff.cutoff_at
    cycle_key = f"pipeline-market-context:{market_cutoff.context_id}"
    policy_identity = CanonicalEvidenceSerializer.fingerprint(
        {
            "kind": "full-pipeline-admission",
            "market_data_policy": policy.value,
            "steps": list(step_names),
        }
    )
    return admit_frozen_operation(
        db,
        operation_kind="full-pipeline-run",
        subject_kind="ticker",
        members=members,
        cycle_key=cycle_key,
        business_cutoff=cutoff,
        provider_source_class="PIPELINE_INPUTS",
        request_type="FULL_PIPELINE",
        requirements=tuple(AcquisitionRequirement(step) for step in step_names),
        policy_identity=policy_identity,
        scope_definition={
            "upload_run_id": pipeline.upload_run_id,
            "market_calculation_context_id": market_cutoff.context_id,
            "market_data_policy": policy.value,
            "stages": list(step_names),
        },
        refresh_reason="FULL_PIPELINE_ADMISSION",
    )


def _pipeline_request_key(
    upload_run_id: int,
    step_names: tuple[str, ...],
    market_data_policy: MarketDataPolicy = MarketDataPolicy.REQUIRE_IB,
) -> str:
    return (
        f"full-pipeline:run:{upload_run_id}:policy:{market_data_policy.value}:"
        f"steps:{','.join(step_names)}"
    )


def _authoritative_pipeline_for_run(
    db: Session,
    upload_run_id: int,
) -> PipelineRun | None:
    local_rows = getattr(db, "pipeline_runs", None)
    if local_rows is not None:
        candidates = local_rows.values() if isinstance(local_rows, dict) else local_rows
        rows = [
            row
            for row in candidates
            if isinstance(row, PipelineRun) and row.upload_run_id == upload_run_id
        ]
    else:
        rows = list(
            db.scalars(
                select(PipelineRun)
                .where(PipelineRun.upload_run_id == upload_run_id)
                .order_by(PipelineRun.id.desc())
            ).all()
        )
    for pipeline in sorted(rows, key=lambda row: int(row.id or 0), reverse=True):
        if pipeline.status not in PIPELINE_TERMINAL_STATUSES or _is_recoverable_sec_block(pipeline):
            return pipeline
    return None


def _is_recoverable_sec_block(pipeline: PipelineRun) -> bool:
    result = pipeline.result_json or {}
    return (
        pipeline.status == PipelineStatus.BLOCKED
        and result.get("blocked_reason") == "SEC_BOOTSTRAP_REQUIRED"
        and pipeline.current_step in {None, "VALIDATING_RUN", "CERI_PROVIDER_INGEST"}
    )


def _pipeline_for_job(db: Session, job: BackgroundJob | None) -> PipelineRun | None:
    if job is None:
        return None
    pipeline_id = (job.payload_json or {}).get("pipeline_run_id")
    if pipeline_id is None:
        return None
    try:
        return db.get(PipelineRun, int(pipeline_id))
    except (TypeError, ValueError):
        return None


def _utcnow() -> datetime:
    return datetime.now(UTC)
