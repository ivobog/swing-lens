from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import BackgroundJob, PipelineRun, PipelineStep, UploadRun
from app.services.background_job_service import (
    active_job_for_request_key,
    enqueue_job,
    request_job_cancel,
)
from app.services.ceri.constants import CERI_PIPELINE_PROVIDER_INGEST_STEP, CERI_PIPELINE_STEPS
from app.services.ceri.feature_flags import ceri_flags
from app.services.market_data_prewarm_service import request_active_prewarm_preemption
from app.services.operational_metrics import operational_metrics
from app.services.setup_lifecycle.constants import SLSE_PIPELINE_STEPS
from app.settings import get_settings

FULL_PIPELINE_JOB_TYPE = "FULL_PIPELINE"
PIPELINE_JOB_PRIORITY = 100
PIPELINE_JOB_MAX_RETRIES = 3

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
    RUNNING = "RUNNING"
    WAITING_FOR_MARKET_DATA = "WAITING_FOR_MARKET_DATA"
    SCORING_FUNDAMENTALS = "SCORING_FUNDAMENTALS"
    FETCHING_MARKET_DATA = "FETCHING_MARKET_DATA"
    SCORING_TECHNICALS = "SCORING_TECHNICALS"
    MARKET_REGIME_SNAPSHOT = "MARKET_REGIME_SNAPSHOT"
    COMBINING_RESULTS = "COMBINING_RESULTS"
    RANKING_PROFILES = "RANKING_PROFILES"
    SECTOR_ROTATION_SNAPSHOT = "SECTOR_ROTATION_SNAPSHOT"
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
) -> PipelineRun:
    upload_run = db.get(UploadRun, upload_run_id)
    if upload_run is None:
        raise ValueError(f"Upload run {upload_run_id} was not found.")

    try:
        policy = MarketDataPolicy(market_data_policy)
    except ValueError as exc:
        raise ValueError(f"Unsupported market data policy: {market_data_policy}") from exc

    step_names = pipeline_step_names(
        ceri_run_capture_enabled=ceri_run_capture_enabled,
        ceri_provider_ingest_enabled=ceri_provider_ingest_enabled,
        setup_lifecycle_pipeline_step_enabled=setup_lifecycle_pipeline_step_enabled,
    )
    request_key = _pipeline_request_key(upload_run_id, step_names, policy)
    existing_job = active_job_for_request_key(db, FULL_PIPELINE_JOB_TYPE, request_key)
    existing_pipeline = _pipeline_for_job(db, existing_job)
    if existing_pipeline is not None:
        existing_pipeline._coalesced = True
        operational_metrics.increment(
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

    job = enqueue_job(
        db,
        job_type=FULL_PIPELINE_JOB_TYPE,
        payload={"pipeline_run_id": pipeline.id},
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
            operational_metrics.increment(
                "swinglens_pipelines_coalesced_total",
                status=existing_pipeline.status,
            )
            return existing_pipeline

    settings = get_settings()
    preflight = dict(ib_preflight_status or {})
    pipeline.result_json = {
        "background_job_id": job.id,
        "market_data_policy": policy.value,
        "ib_preflight_status": preflight.get("status"),
        "ib_preflight_checked_at": preflight.get("checked_at"),
        "ib_host": preflight.get("host", getattr(settings, "ib_host", None)),
        "ib_port": preflight.get("port", getattr(settings, "ib_port", None)),
    }
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
    operational_metrics.increment(
        "swinglens_pipelines_started_total",
        step_count=len(step_names),
    )
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
    return PipelineStatusDto(
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
    operational_metrics.increment(
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
        payload={"pipeline_run_id": pipeline.id, "resume_from_step": target},
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


def _pipeline_request_key(
    upload_run_id: int,
    step_names: tuple[str, ...],
    market_data_policy: MarketDataPolicy = MarketDataPolicy.REQUIRE_IB,
) -> str:
    return (
        f"full-pipeline:run:{upload_run_id}:policy:{market_data_policy.value}:"
        f"steps:{','.join(step_names)}"
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
