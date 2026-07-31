from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import BackgroundJob, WinnerPredictionSnapshot, WinnerProcessingRun
from app.services.background_job_service import JobStatus, is_cancel_requested
from app.services.background_worker import CancelRequested
from app.services.winner_probability.backfill import (
    BackfillRequest,
    WinnerBackfillCancelled,
    WinnerProbabilityBackfillService,
)
from app.services.winner_probability.capture_service import (
    WinnerPredictionCaptureCancelled,
    WinnerPredictionCaptureService,
)
from app.services.winner_probability.config import load_winner_probability_config
from app.services.winner_probability.outcome_service import (
    OutcomeMaturationCancelled,
    OutcomeMaturationService,
)

WINNER_PREDICTION_CAPTURE = "WINNER_PREDICTION_CAPTURE"
WINNER_OUTCOME_MATURATION = "WINNER_OUTCOME_MATURATION"
WINNER_OUTCOME_REVISION_CHECK = "WINNER_OUTCOME_REVISION_CHECK"
WINNER_COHORT_REFRESH = "WINNER_COHORT_REFRESH"
WINNER_MODEL_TRAINING = "WINNER_MODEL_TRAINING"
WINNER_SIMILARITY_CACHE = "WINNER_SIMILARITY_CACHE"
WINNER_HISTORICAL_BACKFILL = "WINNER_HISTORICAL_BACKFILL"

FEATURE_NOT_ENABLED = "FEATURE_NOT_ENABLED"

WinnerJobHandler = Callable[[Session, BackgroundJob], dict[str, Any] | None]


class WinnerJobFeatureNotEnabled(RuntimeError):
    def __init__(self, job_type: str) -> None:
        super().__init__(f"{FEATURE_NOT_ENABLED}: {job_type} is not implemented or enabled.")
        self.job_type = job_type
        self.code = FEATURE_NOT_ENABLED


def implemented_winner_job_handlers() -> dict[str, WinnerJobHandler]:
    return {
        WINNER_PREDICTION_CAPTURE: execute_prediction_capture_job,
        WINNER_OUTCOME_MATURATION: execute_outcome_maturation_job,
        WINNER_HISTORICAL_BACKFILL: execute_historical_backfill_job,
    }


def disabled_winner_job_handler(job_type: str) -> WinnerJobHandler:
    def handler(db: Session, job: BackgroundJob) -> dict[str, Any] | None:
        run_id = _optional_int(job.payload_json or {}, "run_id")
        started_at = _utcnow()
        processing_run = _start_processing_run(
            db,
            job=job,
            process_type=job_type,
            run_id=run_id,
            config_hash=None,
        )
        _finish_processing_run(
            db,
            processing_run,
            status=JobStatus.FAILED,
            started_at=started_at,
            counts={"failed": 1},
            error=f"{FEATURE_NOT_ENABLED}: {job_type}",
        )
        raise WinnerJobFeatureNotEnabled(job_type)

    return handler


def execute_prediction_capture_job(
    db: Session,
    job: BackgroundJob,
    *,
    capture_service: WinnerPredictionCaptureService | None = None,
) -> dict[str, Any]:
    run_id = _required_int(job.payload_json or {}, "run_id")
    config = load_winner_probability_config()
    processing_run = _start_processing_run(
        db,
        job=job,
        process_type=WINNER_PREDICTION_CAPTURE,
        run_id=run_id,
        config_hash=config.config_hash,
    )
    capture_service = capture_service or WinnerPredictionCaptureService()
    started_at = processing_run.started_at or _utcnow()

    try:
        result = capture_service.capture_run(
            db,
            run_id=run_id,
            config=config,
            should_cancel=lambda: _heartbeat_and_check_cancel(db, job),
        )
    except WinnerPredictionCaptureCancelled as exc:
        _finish_processing_run(
            db,
            processing_run,
            status=JobStatus.CANCELLED,
            started_at=started_at,
            error=str(exc),
        )
        raise CancelRequested(str(exc)) from exc
    except Exception as exc:
        _finish_processing_run(
            db,
            processing_run,
            status=JobStatus.FAILED,
            started_at=started_at,
            error=str(exc),
        )
        raise

    counts = result.as_dict()
    status = JobStatus.PARTIAL if counts.get("failed", 0) else JobStatus.COMPLETED
    if status == JobStatus.PARTIAL:
        job.status = JobStatus.PARTIAL
    _finish_processing_run(
        db,
        processing_run,
        status=status,
        started_at=started_at,
        counts=counts,
        checkpoint={"last_completed_phase": "prediction_capture"},
        source_cutoff_at=_latest_source_cutoff_at(db, run_id),
    )
    return {
        "job_type": WINNER_PREDICTION_CAPTURE,
        "run_id": run_id,
        "processing_run_id": processing_run.id,
        "status": status,
        **counts,
    }


def execute_outcome_maturation_job(
    db: Session,
    job: BackgroundJob,
    *,
    outcome_service: OutcomeMaturationService | None = None,
) -> dict[str, Any]:
    limit = _optional_int(job.payload_json or {}, "limit") or 500
    config = load_winner_probability_config()
    processing_run = _start_processing_run(
        db,
        job=job,
        process_type=WINNER_OUTCOME_MATURATION,
        run_id=None,
        config_hash=config.config_hash,
    )
    outcome_service = outcome_service or OutcomeMaturationService()
    started_at = processing_run.started_at or _utcnow()

    try:
        result = outcome_service.process_due_outcomes(
            db,
            limit=limit,
            should_cancel=lambda: _heartbeat_and_check_cancel(db, job),
        )
    except OutcomeMaturationCancelled as exc:
        _finish_processing_run(
            db,
            processing_run,
            status=JobStatus.CANCELLED,
            started_at=started_at,
            error=str(exc),
        )
        raise CancelRequested(str(exc)) from exc
    except Exception as exc:
        _finish_processing_run(
            db,
            processing_run,
            status=JobStatus.FAILED,
            started_at=started_at,
            error=str(exc),
        )
        raise

    counts = result.as_dict()
    status = (
        JobStatus.PARTIAL
        if counts.get("failed", 0) or counts.get("warnings", 0)
        else JobStatus.COMPLETED
    )
    if status == JobStatus.PARTIAL:
        job.status = JobStatus.PARTIAL
    _finish_processing_run(
        db,
        processing_run,
        status=status,
        started_at=started_at,
        counts=counts,
        checkpoint={"last_completed_phase": "outcome_maturation", "limit": limit},
    )
    return {
        "job_type": WINNER_OUTCOME_MATURATION,
        "processing_run_id": processing_run.id,
        "status": status,
        **counts,
    }


def execute_historical_backfill_job(
    db: Session,
    job: BackgroundJob,
    *,
    backfill_service: WinnerProbabilityBackfillService | None = None,
) -> dict[str, Any]:
    payload = job.payload_json or {}
    run_ids = tuple(_int_list(payload, "run_ids"))
    trusted_run_ids = tuple(_int_list(payload, "trusted_run_ids", required=False))
    limit = _optional_int(payload, "limit") or 100
    reconstruction_method = str(payload.get("reconstruction_method") or "HISTORICAL_AS_OF_REPLAY")
    allow_reconstructed_training = bool(payload.get("allow_reconstructed_training") or False)
    config = load_winner_probability_config()
    processing_run = _start_processing_run(
        db,
        job=job,
        process_type=WINNER_HISTORICAL_BACKFILL,
        run_id=None,
        config_hash=config.config_hash,
    )
    backfill_service = backfill_service or WinnerProbabilityBackfillService()
    started_at = processing_run.started_at or _utcnow()

    try:
        result = backfill_service.execute_backfill(
            db,
            BackfillRequest(
                run_ids=run_ids,
                trusted_run_ids=trusted_run_ids,
                reconstruction_method=reconstruction_method,
                limit=limit,
                allow_reconstructed_training=allow_reconstructed_training,
            ),
            config=config,
            should_cancel=lambda: _heartbeat_and_check_cancel(db, job),
        )
    except WinnerBackfillCancelled as exc:
        _finish_processing_run(
            db,
            processing_run,
            status=JobStatus.CANCELLED,
            started_at=started_at,
            error=str(exc),
        )
        raise CancelRequested(str(exc)) from exc
    except Exception as exc:
        _finish_processing_run(
            db,
            processing_run,
            status=JobStatus.FAILED,
            started_at=started_at,
            error=str(exc),
        )
        raise

    counts = {
        "planned_runs": len(result.plan.items),
        "ready_runs": result.plan.ready_count,
        "skipped_runs": result.skipped_runs,
        "captured_runs": result.captured_runs,
        **result.counts,
    }
    status = (
        JobStatus.PARTIAL
        if result.skipped_runs or counts.get("failed", 0)
        else JobStatus.COMPLETED
    )
    if status == JobStatus.PARTIAL:
        job.status = JobStatus.PARTIAL
    _finish_processing_run(
        db,
        processing_run,
        status=status,
        started_at=started_at,
        counts=counts,
        checkpoint={
            "last_completed_phase": "historical_backfill",
            "reconstruction_method": reconstruction_method,
        },
    )
    return {
        "job_type": WINNER_HISTORICAL_BACKFILL,
        "processing_run_id": processing_run.id,
        "status": status,
        **counts,
    }


def _start_processing_run(
    db: Session,
    *,
    job: BackgroundJob,
    process_type: str,
    run_id: int | None,
    config_hash: str | None,
) -> WinnerProcessingRun:
    now = _utcnow()
    processing_run = WinnerProcessingRun(
        background_job_id=job.id,
        run_id=run_id,
        process_type=process_type,
        status=JobStatus.RUNNING,
        config_hash=config_hash,
        started_at=now,
        counts_json={},
        checkpoint_json={},
        metadata_json={
            "background_job_type": job.job_type,
            "execution_token": job.execution_token,
            "lease_owner": job.lease_owner,
        },
    )
    db.add(processing_run)
    db.flush()
    return processing_run


def _finish_processing_run(
    db: Session,
    processing_run: WinnerProcessingRun,
    *,
    status: str,
    started_at: datetime,
    counts: dict[str, Any] | None = None,
    checkpoint: dict[str, Any] | None = None,
    source_cutoff_at: datetime | None = None,
    error: str | None = None,
) -> None:
    completed_at = _utcnow()
    processing_run.status = status
    processing_run.completed_at = completed_at
    if source_cutoff_at is not None:
        processing_run.source_cutoff_at = source_cutoff_at
    processing_run.counts_json = counts or processing_run.counts_json or {}
    processing_run.checkpoint_json = checkpoint or processing_run.checkpoint_json or {}
    processing_run.error_message = _safe_error(error) if error else None
    processing_run.metadata_json = {
        **(processing_run.metadata_json or {}),
        "duration_seconds": (completed_at - started_at).total_seconds(),
    }
    db.flush()


def _heartbeat_and_check_cancel(db: Session, job: BackgroundJob) -> bool:
    heartbeat = getattr(job, "_heartbeat", None)
    if callable(heartbeat):
        heartbeat()
    return is_cancel_requested(db, job.id)


def _latest_source_cutoff_at(db: Session, run_id: int) -> datetime | None:
    value = db.scalar(
        select(func.max(WinnerPredictionSnapshot.source_data_cutoff_at)).where(
            WinnerPredictionSnapshot.run_id == run_id
        )
    )
    return value if isinstance(value, datetime) else None


def _required_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if value is None:
        raise ValueError(f"{WINNER_PREDICTION_CAPTURE} job payload is missing {key}.")
    return _coerce_int(value, key)


def _optional_int(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    return _coerce_int(value, key)


def _int_list(
    payload: dict[str, Any],
    key: str,
    *,
    required: bool = True,
) -> list[int]:
    value = payload.get(key)
    if value is None:
        if required:
            raise ValueError(f"{WINNER_HISTORICAL_BACKFILL} job payload is missing {key}.")
        return []
    if not isinstance(value, list | tuple):
        raise ValueError(f"{WINNER_HISTORICAL_BACKFILL} job payload has invalid {key}.")
    return [_coerce_int(item, key) for item in value]


def _coerce_int(value: Any, key: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{WINNER_PREDICTION_CAPTURE} job payload has invalid {key}.") from exc


def _safe_error(error: str | None) -> str | None:
    if error is None:
        return None
    return error.replace("\n", " ").strip()[:500]


def _utcnow() -> datetime:
    return datetime.now(UTC)
