from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import PipelineRun, PipelineStep, UploadRun
from app.services.background_job_service import enqueue_job, request_job_cancel

FULL_PIPELINE_JOB_TYPE = "FULL_PIPELINE"
PIPELINE_JOB_PRIORITY = 100
PIPELINE_JOB_MAX_RETRIES = 3

PIPELINE_STEP_NAMES = (
    "VALIDATING_RUN",
    "SCORING_FUNDAMENTALS",
    "FETCHING_MARKET_DATA",
    "SCORING_TECHNICALS",
    "COMBINING_RESULTS",
)

PIPELINE_TERMINAL_STATUSES = {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED"}


class PipelineStatus:
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_FOR_MARKET_DATA = "WAITING_FOR_MARKET_DATA"
    SCORING_FUNDAMENTALS = "SCORING_FUNDAMENTALS"
    FETCHING_MARKET_DATA = "FETCHING_MARKET_DATA"
    SCORING_TECHNICALS = "SCORING_TECHNICALS"
    COMBINING_RESULTS = "COMBINING_RESULTS"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class PipelineStepStatus:
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
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


def start_pipeline(
    db: Session,
    upload_run_id: int,
    requested_by: str | None = None,
) -> PipelineRun:
    upload_run = db.get(UploadRun, upload_run_id)
    if upload_run is None:
        raise ValueError(f"Upload run {upload_run_id} was not found.")

    pipeline = PipelineRun(
        upload_run_id=upload_run_id,
        status=PipelineStatus.PENDING,
        current_step=PIPELINE_STEP_NAMES[0],
        requested_by=requested_by,
        message="Full pipeline is queued.",
    )
    db.add(pipeline)
    db.flush()

    for step_order, step_name in enumerate(PIPELINE_STEP_NAMES, start=1):
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
    )
    pipeline.result_json = {"background_job_id": job.id}
    db.flush()
    return pipeline


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


def _utcnow() -> datetime:
    return datetime.now(UTC)
