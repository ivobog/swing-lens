from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import BackgroundJob, PipelineRun, PipelineStep, UploadRun
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
from app.services.setup_lifecycle.constants import SLSE_PIPELINE_STEPS
from app.settings import RuntimeMode, get_settings

FULL_PIPELINE_JOB_TYPE = "FULL_PIPELINE"
PIPELINE_JOB_PRIORITY = 100
PIPELINE_JOB_MAX_RETRIES = 3
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

PIPELINE_TERMINAL_STATUSES = {"COMPLETED", "PARTIAL", "FAILED", "BLOCKED", "CANCELLED"}


class MarketDataPolicy(StrEnum):
    REQUIRE_IB = "REQUIRE_IB"
    ALLOW_CACHE_FALLBACK = "ALLOW_CACHE_FALLBACK"


class PipelineStatus:
    PENDING = "PENDING"
    PREPARING = "PREPARING"
    RUNNING = "RUNNING"
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
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"
    SKIPPED = "SKIPPED"


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
        handoff_index = next(
            (
                index
                for index, step_name in enumerate(step_names)
                if step_name
                in {
                    CERI_PIPELINE_PROVIDER_INGEST_STEP,
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
    if getattr(job, "_coalesced", False):
        existing_pipeline = _pipeline_for_job(db, job)
        if existing_pipeline is not None:
            pipeline.status = PipelineStatus.CANCELLED
            pipeline.completed_at = _utcnow()
            pipeline.message = "Duplicate pipeline request coalesced into an active run."
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


def get_pipeline_status(db: Session, pipeline_run_id: int) -> PipelineStatusDto:
    pipeline = db.get(PipelineRun, pipeline_run_id)
    if pipeline is None:
        raise ValueError(f"Pipeline run {pipeline_run_id} was not found.")

    steps = _load_pipeline_steps(db, pipeline_run_id)
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
        steps=[
            PipelineStepStatusDto(
                step_name=step.step_name,
                step_order=step.step_order,
                status=step.status,
                started_at=step.started_at,
                completed_at=step.completed_at,
                message=step.message,
                error_message=step.error_message,
                retry_count=step.retry_count,
            )
            for step in steps
        ],
        result_json=pipeline.result_json,
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
        message += " Frozen configuration or execution lineage could not be verified."
    return {
        "status": PipelineStatus.FAILED if failure["retryable"] else PipelineStatus.BLOCKED,
        "message": message,
        "detail": f"{message} Pipeline {pipeline.id}, job {job.id}: {failure['code']}.",
    }


def mark_pipeline_job_failure(db, job, failure):
    pipeline = db.scalar(
        select(PipelineRun)
        .where(PipelineRun.result_json["background_job_id"].astext == str(job.id))
        .with_for_update()
    )
    if pipeline is None or pipeline.status in PIPELINE_TERMINAL_STATUSES:
        return
    if (pipeline.result_json or {}).get("background_job_id") != job.id:
        return
    view = _pipeline_job_failure_view(pipeline, job, failure)
    pipeline.status = view["status"]
    pipeline.completed_at = job.completed_at or _utcnow()
    pipeline.message = view["message"]
    pipeline.error_message = view["detail"]
    pipeline.result_json = {**(pipeline.result_json or {}), "blocked_reason": failure["code"]}
    target = pipeline.current_step or "VALIDATING_RUN"
    step = db.scalar(
        select(PipelineStep).where(
            PipelineStep.pipeline_run_id == pipeline.id, PipelineStep.step_name == target
        )
    )
    if step is not None:
        step.status = view["status"]
        step.completed_at = pipeline.completed_at
        step.message = view["message"]
        step.error_message = view["detail"]
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
    pipeline = db.get(PipelineRun, pipeline_run_id)
    if pipeline is None:
        raise ValueError(f"Pipeline run {pipeline_run_id} was not found.")

    background_job_id = _background_job_id(pipeline)
    if background_job_id is not None:
        request_job_cancel(db, background_job_id)

    if pipeline.status == PipelineStatus.PENDING:
        pipeline.status = PipelineStatus.CANCELLED
        pipeline.completed_at = _utcnow()
        pipeline.message = "Pipeline cancellation requested."
        _cancel_pending_steps(db, pipeline_run_id)
    elif pipeline.status not in PIPELINE_TERMINAL_STATUSES:
        pipeline.message = "Pipeline cancellation requested."

    db.flush()
    publish_after_commit(
        db,
        "increment",
        "swinglens_pipelines_cancel_requested_total",
        status=pipeline.status,
    )
    return pipeline


def resume_pipeline(
    db: Session,
    pipeline_run_id: int,
    *,
    resume_from_step: str | None = None,
) -> PipelineRun:
    pipeline = db.get(PipelineRun, pipeline_run_id)
    if pipeline is None:
        raise ValueError(f"Pipeline run {pipeline_run_id} was not found.")
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
    pipeline.status = PipelineStatus.PENDING
    pipeline.current_step = target
    pipeline.completed_at = None
    pipeline.message = f"Pipeline resume queued from {target}."
    pipeline.error_message = None
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
    if getattr(job, "_coalesced", False):
        return job
    pipeline.status = PipelineStatus.PENDING
    pipeline.current_step = resume_from_step
    pipeline.completed_at = None
    pipeline.message = "SEC preparation completed; pipeline continuation queued."
    pipeline.error_message = None
    step.status = PipelineStepStatus.PENDING
    step.completed_at = None
    step.message = "SEC preparation completed; continuing pipeline."
    step.error_message = None
    pipeline.result_json = {
        **(pipeline.result_json or {}),
        "background_job_id": job.id,
        "resume_from_step": resume_from_step,
        "blocked_reason": None,
        "blocked_diagnostics": None,
    }
    db.flush()
    return job


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
    return {
        "market_calculation_context_id": context.context_id,
        "market_cutoff_at": CanonicalEvidenceSerializer.canonicalize(context.cutoff_at),
        "input_as_of_session": context.latest_completed_session.isoformat(),
        "market_calendar_version": context.calendar_version,
        "bar_readiness_version": context.bar_readiness_version,
    }


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
