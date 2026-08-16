from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import (
    BackgroundJob,
    PredictionEligibility,
    WinnerOutcomeDefinition,
    WinnerPredictionSnapshot,
    WinnerProcessingRun,
)
from app.services.background_job_service import JobStatus, enqueue_job, is_cancel_requested
from app.services.background_worker import CancelRequested
from app.services.redaction import redact_sensitive, redacted_token_metadata
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
from app.services.winner_probability.decision_time_estimate_service import (
    DecisionTimeEstimateService,
)
from app.services.winner_probability.outcome_orchestration_service import (
    H5NextOpenOrchestrationService,
)
from app.services.winner_probability.outcome_service import (
    OutcomeMaturationCancelled,
    OutcomeMaturationService,
)
from app.services.winner_probability.probability_estimator import ProbabilityEstimator
from app.services.winner_probability.repository import WinnerProbabilityRepository

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


class WinnerCohortRefreshCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class WinnerCohortRefreshResult:
    processed: int = 0
    estimated: int = 0
    duplicate: int = 0
    insufficient: int = 0
    failed: int = 0

    def as_dict(self) -> dict[str, int]:
        return self.__dict__.copy()


class WinnerCohortRefreshService:
    def __init__(
        self,
        *,
        repository: WinnerProbabilityRepository | None = None,
        decision_time_estimate_service: DecisionTimeEstimateService | None = None,
        probability_estimator: ProbabilityEstimator | None = None,
    ) -> None:
        self.repository = repository or WinnerProbabilityRepository()
        self.decision_time_estimate_service = (
            decision_time_estimate_service or DecisionTimeEstimateService(self.repository)
        )
        self.probability_estimator = probability_estimator or ProbabilityEstimator()

    def refresh_cohorts(
        self,
        db: Session,
        *,
        outcome_definition_id: str | None = None,
        training_cutoff_at: datetime | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> WinnerCohortRefreshResult:
        config = load_winner_probability_config()
        outcome_definition = self._outcome_definition(
            db,
            outcome_definition_id=outcome_definition_id,
            calculation_version=config.engine.calculation_version,
            default_definition_id=config.primary_outcome_definition.id,
        )
        counts = _MutableCohortRefreshCounts()
        training_cutoff_at = training_cutoff_at or _utcnow()
        for prediction in _eligible_predictions(db):
            if should_cancel is not None and should_cancel():
                raise WinnerCohortRefreshCancelled("winner cohort refresh was cancelled")
            counts.processed += 1
            try:
                result = self.probability_estimator.create_latest_rescore(
                    db,
                    prediction=prediction,
                    outcome_definition=outcome_definition,
                    as_of=training_cutoff_at,
                    config=config,
                )
            except Exception:
                counts.failed += 1
                continue
            if result.status == "duplicate":
                counts.duplicate += 1
            elif result.status == "insufficient":
                counts.insufficient += 1
            else:
                counts.estimated += 1
        return counts.to_result()

    def _outcome_definition(
        self,
        db: Session,
        *,
        outcome_definition_id: str | None,
        calculation_version: str,
        default_definition_id: str,
    ) -> WinnerOutcomeDefinition:
        definition_id = outcome_definition_id or default_definition_id
        outcome_definition = self.repository.get_outcome_definition(
            db,
            definition_id=definition_id,
            calculation_version=calculation_version,
        )
        if outcome_definition is None:
            raise ValueError(f"Winner outcome definition was not found: {definition_id}")
        return outcome_definition


@dataclass
class _MutableCohortRefreshCounts:
    processed: int = 0
    estimated: int = 0
    duplicate: int = 0
    insufficient: int = 0
    failed: int = 0

    def to_result(self) -> WinnerCohortRefreshResult:
        return WinnerCohortRefreshResult(
            processed=self.processed,
            estimated=self.estimated,
            duplicate=self.duplicate,
            insufficient=self.insufficient,
            failed=self.failed,
        )


def implemented_winner_job_handlers() -> dict[str, WinnerJobHandler]:
    return {
        WINNER_PREDICTION_CAPTURE: execute_prediction_capture_job,
        WINNER_OUTCOME_MATURATION: execute_outcome_maturation_job,
        WINNER_COHORT_REFRESH: execute_cohort_refresh_job,
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
    orchestration_service: H5NextOpenOrchestrationService | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    payload = job.payload_json or {}
    limit = _optional_int(payload, "limit") or 500
    max_batches = _optional_int(payload, "max_batches") or 10
    due_session = (
        datetime.fromisoformat(str(payload["due_session"])).date()
        if payload.get("due_session")
        else None
    )
    config = load_winner_probability_config()
    processing_run = _start_processing_run(
        db,
        job=job,
        process_type=WINNER_OUTCOME_MATURATION,
        run_id=None,
        config_hash=config.config_hash,
    )
    if orchestration_service is None and outcome_service is None:
        orchestration_service = H5NextOpenOrchestrationService()
    started_at = processing_run.started_at or _utcnow()

    try:
        if orchestration_service is not None:
            result = orchestration_service.drain_due(
                db,
                now=now,
                batch_size=limit,
                max_batches=max_batches,
                due_session=due_session,
                should_cancel=lambda: _heartbeat_and_check_cancel(db, job),
            )
        else:
            result = outcome_service.process_due_outcomes(  # type: ignore[union-attr]
                db,
                now=now,
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
        if (
            counts.get("failed", 0)
            or counts.get("failed_h5", 0)
            or counts.get("pending_h5_after_cycle", 0)
        )
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
            "last_completed_phase": "outcome_maturation",
            "limit": limit,
            "max_batches": max_batches,
            "remaining_queue_depth": counts.get("pending_h5_after_cycle"),
            "unvisited_queue_depth": counts.get("unvisited_h5_after_cycle"),
        },
    )
    if counts.get("target_stop_matured", 0):
        definition_id = config.primary_outcome_definition.id
        refresh_cutoff_at = _utcnow()
        enqueue_job(
            db,
            WINNER_COHORT_REFRESH,
            {
                "outcome_definition_id": definition_id,
                "training_cutoff_at": refresh_cutoff_at.isoformat(),
            },
            request_key=f"winner:cohort-refresh:{definition_id}",
        )
    if counts.get("unvisited_h5_after_cycle", 0):
        enqueue_job(
            db,
            WINNER_OUTCOME_MATURATION,
            {"limit": limit, "max_batches": max_batches, "continuation": True},
            request_key=f"winner:h5-next-open:continuation:{processing_run.id}",
        )
    return {
        "job_type": WINNER_OUTCOME_MATURATION,
        "processing_run_id": processing_run.id,
        "status": status,
        **counts,
    }


def execute_cohort_refresh_job(
    db: Session,
    job: BackgroundJob,
    *,
    cohort_refresh_service: WinnerCohortRefreshService | None = None,
) -> dict[str, Any]:
    payload = job.payload_json or {}
    outcome_definition_id = payload.get("outcome_definition_id")
    training_cutoff_at = (
        datetime.fromisoformat(str(payload["training_cutoff_at"]))
        if payload.get("training_cutoff_at")
        else None
    )
    config = load_winner_probability_config()
    processing_run = _start_processing_run(
        db,
        job=job,
        process_type=WINNER_COHORT_REFRESH,
        run_id=None,
        config_hash=config.config_hash,
    )
    cohort_refresh_service = cohort_refresh_service or WinnerCohortRefreshService()
    started_at = processing_run.started_at or _utcnow()

    try:
        result = cohort_refresh_service.refresh_cohorts(
            db,
            outcome_definition_id=str(outcome_definition_id)
            if outcome_definition_id is not None
            else None,
            training_cutoff_at=training_cutoff_at,
            should_cancel=lambda: _heartbeat_and_check_cancel(db, job),
        )
    except WinnerCohortRefreshCancelled as exc:
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
        checkpoint={
            "last_completed_phase": "cohort_refresh",
            "outcome_definition_id": outcome_definition_id or config.primary_outcome_definition.id,
        },
    )
    return {
        "job_type": WINNER_COHORT_REFRESH,
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
        JobStatus.PARTIAL if result.skipped_runs or counts.get("failed", 0) else JobStatus.COMPLETED
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
            **redacted_token_metadata(job.execution_token),
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


def _eligible_predictions(db: Session) -> list[WinnerPredictionSnapshot]:
    return list(
        db.scalars(
            select(WinnerPredictionSnapshot)
            .where(WinnerPredictionSnapshot.eligibility_status == PredictionEligibility.ELIGIBLE)
            .where(WinnerPredictionSnapshot.superseded_at.is_(None))
            .order_by(
                WinnerPredictionSnapshot.run_id.asc(),
                WinnerPredictionSnapshot.ticker.asc(),
                WinnerPredictionSnapshot.prediction_as_of_date.asc(),
                WinnerPredictionSnapshot.id.asc(),
            )
        )
    )


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
    return str(redact_sensitive(error)).replace("\n", " ").strip()[:500]


def _utcnow() -> datetime:
    return datetime.now(UTC)
