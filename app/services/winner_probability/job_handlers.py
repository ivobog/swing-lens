from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import (
    BackgroundJob,
    PredictionEligibility,
    WinnerCohortGeneration,
    WinnerOutcomeDefinition,
    WinnerPredictionSnapshot,
    WinnerProcessingRun,
)
from app.observability.db_monitor import job_phase
from app.services.background_job_service import (
    JobStatus,
    active_job_for_type,
    enqueue_job,
    is_cancel_requested,
)
from app.services.background_worker import CancelRequested, JobDeferred
from app.services.operational_metrics import operational_metrics
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
from app.services.winner_probability.cohort_generation_service import (
    CohortGenerationService,
    CohortGenerationStatus,
    EvidenceWatermarkService,
    contract_for,
)
from app.services.winner_probability.cohort_materialization_service import (
    CohortMaterializationCancelled,
    CohortMaterializationService,
)
from app.services.winner_probability.cohort_refresh_planner import CohortRefreshPlanner
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
from app.settings import get_settings

WINNER_PREDICTION_CAPTURE = "WINNER_PREDICTION_CAPTURE"
WINNER_OUTCOME_MATURATION = "WINNER_OUTCOME_MATURATION"
WINNER_OUTCOME_REVISION_CHECK = "WINNER_OUTCOME_REVISION_CHECK"
WINNER_COHORT_REFRESH = "WINNER_COHORT_REFRESH"
WINNER_LATEST_RESCORE = "WINNER_LATEST_RESCORE"
WINNER_MODEL_TRAINING = "WINNER_MODEL_TRAINING"
WINNER_SIMILARITY_CACHE = "WINNER_SIMILARITY_CACHE"
WINNER_HISTORICAL_BACKFILL = "WINNER_HISTORICAL_BACKFILL"

WINNER_MATURATION_WORKFLOW_KEY = "winner:h5-next-open:maturation"
# A default slice can visit 5,000 rows (500 x 10). This permits bounded drains
# up to five million rows while still making an accidental chain finite.
MAX_MATURATION_CONTINUATION_DEPTH = 1000

logger = logging.getLogger(__name__)

FEATURE_NOT_ENABLED = "FEATURE_NOT_ENABLED"

WinnerJobHandler = Callable[[Session, BackgroundJob], dict[str, Any] | None]


class WinnerJobFeatureNotEnabled(RuntimeError):
    def __init__(self, job_type: str) -> None:
        super().__init__(f"{FEATURE_NOT_ENABLED}: {job_type} is not implemented or enabled.")
        self.job_type = job_type
        self.code = FEATURE_NOT_ENABLED


class WinnerCohortRefreshCancelled(RuntimeError):
    pass


def enqueue_outcome_maturation_workflow(
    db: Session,
    *,
    payload: dict[str, Any],
    trigger_source: str,
    request_key: str | None = None,
    priority: int = 100,
) -> BackgroundJob:
    """Enqueue or coalesce one root in the global primary-H5 workflow domain."""
    existing = active_job_for_type(db, WINNER_OUTCOME_MATURATION)
    if existing is not None:
        existing._coalesced = True
        operational_metrics.increment(
            "swinglens_jobs_coalesced_total",
            job_type=WINNER_OUTCOME_MATURATION,
            reason="maturation_active_type",
        )
        return existing
    job = enqueue_job(
        db,
        WINNER_OUTCOME_MATURATION,
        payload,
        request_key=request_key or f"winner:outcome-maturation:{uuid4().hex}",
        workflow_key=WINNER_MATURATION_WORKFLOW_KEY,
        priority=priority,
        single_flight_workflow=True,
        continuation_depth=0,
        trigger_source=trigger_source,
    )
    if not getattr(job, "_coalesced", False) and job.root_job_id is None:
        job.root_job_id = job.id
        db.flush()
    return job


@dataclass(frozen=True)
class WinnerCohortRefreshResult:
    processed: int = 0
    estimated: int = 0
    duplicate: int = 0
    insufficient: int = 0
    failed: int = 0
    generation_id: int | None = None
    generation_key: str | None = None
    evidence_rows_loaded: int = 0
    planned_groups: int = 0
    completed_groups: int = 0
    groups_in_slice: int = 0
    manifest_members_inserted: int = 0
    continuation_required: bool = False
    desired_watermark_advanced: bool = False
    no_op: bool = False

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class WinnerCohortRefreshService:
    def __init__(
        self,
        *,
        repository: WinnerProbabilityRepository | None = None,
        decision_time_estimate_service: DecisionTimeEstimateService | None = None,
        probability_estimator: ProbabilityEstimator | None = None,
        watermark_service: EvidenceWatermarkService | None = None,
        generation_service: CohortGenerationService | None = None,
        materialization_service: CohortMaterializationService | None = None,
    ) -> None:
        self.repository = repository or WinnerProbabilityRepository()
        self.decision_time_estimate_service = (
            decision_time_estimate_service or DecisionTimeEstimateService(self.repository)
        )
        self.probability_estimator = probability_estimator or ProbabilityEstimator()
        self.watermark_service = watermark_service or EvidenceWatermarkService()
        self.generation_service = generation_service or CohortGenerationService()
        self.materialization_service = materialization_service or CohortMaterializationService(
            generation_service=self.generation_service
        )

    def refresh_cohorts(
        self,
        db: Session,
        *,
        outcome_definition_id: str | None = None,
        training_cutoff_at: datetime | None = None,
        should_cancel: Callable[[], bool] | None = None,
        lease_guard: Callable[[], None] | None = None,
        on_generation_captured: Callable[[WinnerCohortGeneration], None] | None = None,
        max_groups: int = 100,
        max_wall_seconds: float = 45.0,
    ) -> WinnerCohortRefreshResult:
        config = load_winner_probability_config()
        outcome_definition = self._outcome_definition(
            db,
            outcome_definition_id=outcome_definition_id,
            calculation_version=config.engine.calculation_version,
            default_definition_id=config.primary_outcome_definition.id,
        )
        # Legacy payload cutoffs are audit-only. Material identity comes from
        # the durable evidence watermark and never from request wall time.
        del training_cutoff_at
        lease_guard = lease_guard or (lambda: None)
        should_cancel = should_cancel or (lambda: False)
        advance = self.watermark_service.advance_to_current_material_evidence(
            db,
            outcome_definition=outcome_definition,
            config=config,
        )
        state = advance.state
        if (
            state.published_generation_id is not None
            and state.published_watermark_hash == state.desired_watermark_hash
        ):
            published = self.generation_service.published_for_state(db, state)
            if published is not None:
                return WinnerCohortRefreshResult(
                    generation_id=published.id,
                    generation_key=published.generation_key,
                    completed_groups=published.completed_group_count,
                    planned_groups=int(published.planned_group_count or 0),
                    no_op=True,
                )
        generation = self.generation_service.capture_or_resume(
            db,
            state=state,
            contract=contract_for(outcome_definition, config),
        )
        if on_generation_captured is not None:
            on_generation_captured(generation)
        materialized = self.materialization_service.materialize_slice(
            db,
            generation=generation,
            outcome_definition=outcome_definition,
            config=config,
            lease_guard=lease_guard,
            should_cancel=should_cancel,
            max_groups=max_groups,
            max_wall_seconds=max_wall_seconds,
        )
        return WinnerCohortRefreshResult(
            processed=materialized.groups_in_slice,
            generation_id=materialized.generation_id,
            generation_key=materialized.generation_key,
            evidence_rows_loaded=materialized.evidence_rows_loaded,
            planned_groups=materialized.planned_groups,
            completed_groups=materialized.completed_groups,
            groups_in_slice=materialized.groups_in_slice,
            manifest_members_inserted=materialized.manifest_members_inserted,
            continuation_required=materialized.continuation_required,
            desired_watermark_advanced=materialized.desired_watermark_advanced,
            no_op=materialized.no_op,
        )

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
        WINNER_OUTCOME_REVISION_CHECK: execute_outcome_revision_check_job,
        WINNER_COHORT_REFRESH: execute_cohort_refresh_job,
        WINNER_LATEST_RESCORE: execute_latest_rescore_job,
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
            reason_code=type(exc).__name__.upper(),
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
        with job_phase("outcome_calculation_and_persistence"):
            if orchestration_service is not None:
                result = orchestration_service.drain_due(
                    db,
                    now=now,
                    batch_size=limit,
                    max_batches=max_batches,
                    due_session=due_session,
                    should_cancel=lambda: _heartbeat_and_check_cancel(db, job),
                    lease_guard=lambda: _heartbeat_only(job),
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
            reason_code=type(exc).__name__.upper(),
        )
        raise

    counts = result.as_dict()
    processed = int(counts.get("processed_h5", counts.get("processed", 0)) or 0)
    eligible_remaining = int(counts.get("eligible_remaining", 0) or 0)
    retry_deferred = int(counts.get("retry_deferred", 0) or 0)
    pending_after = int(counts.get("pending_h5_after_cycle", 0) or 0)
    depth = int(job.continuation_depth or 0)
    root_job_id = job.root_job_id or job.id
    trigger_source = job.trigger_source or (
        "RECOVERY" if int(job.recovery_count or 0) > 0 else "UNKNOWN"
    )
    continuation_decision = "DRAIN_COMPLETE"
    continuation_reason = "NO_WORK_REMAINING"
    defer_until: datetime | None = None

    if processed == 0:
        operational_metrics.increment("winner_maturation_zero_progress_total")
        if retry_deferred > 0:
            continuation_decision = "DEFER_SAME_JOB"
            continuation_reason = "RETRY_DEFERRED"
            defer_until = _parse_optional_datetime(counts.get("earliest_retry_not_before"))
        elif eligible_remaining > 0 or pending_after > 0:
            continuation_decision = "STOP_ZERO_PROGRESS"
            continuation_reason = "ZERO_PROGRESS_BLOCKED"
    elif eligible_remaining > 0:
        if depth >= MAX_MATURATION_CONTINUATION_DEPTH:
            continuation_decision = "STOP_CONTINUATION_LIMIT"
            continuation_reason = "CONTINUATION_LIMIT_REACHED"
        else:
            continuation_decision = "ENQUEUE_CONTINUATION"
            continuation_reason = "CONTINUATION_REQUIRED"
    elif retry_deferred > 0:
        continuation_decision = "DEFER_SAME_JOB"
        continuation_reason = "RETRY_DEFERRED"
        defer_until = _parse_optional_datetime(counts.get("earliest_retry_not_before"))
    elif pending_after > 0:
        continuation_decision = "STOP_NO_ELIGIBLE_WORK"
        continuation_reason = "NO_RETRY_ELIGIBLE_ROWS"

    counts.update(
        {
            "workflow_key": job.workflow_key or WINNER_MATURATION_WORKFLOW_KEY,
            "root_job_id": root_job_id,
            "parent_job_id": job.parent_job_id,
            "continuation_depth": depth,
            "trigger_source": trigger_source,
            "continuation_decision": continuation_decision,
            "continuation_reason": continuation_reason,
        }
    )
    operational_metrics.increment("winner_maturation_jobs_total", trigger_source=trigger_source)
    operational_metrics.set_gauge("winner_maturation_continuation_depth", depth)
    for metric_name, field_name in (
        ("winner_maturation_due_total", "due_total"),
        ("winner_maturation_retry_eligible", "retry_eligible_now"),
        ("winner_maturation_retry_deferred", "retry_deferred"),
    ):
        operational_metrics.set_gauge(metric_name, int(counts.get(field_name, 0) or 0))

    status = classify_maturation_status(counts)
    if continuation_decision != "DEFER_SAME_JOB" and status == JobStatus.PARTIAL:
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
            "eligible_remaining": eligible_remaining,
            "continuation_decision": continuation_decision,
        },
        reason_code=continuation_reason,
    )
    definition_id = config.primary_outcome_definition.id
    if isinstance(db, Session):
        definition = WinnerProbabilityRepository().get_outcome_definition(
            db,
            definition_id=definition_id,
            calculation_version=config.engine.calculation_version,
        )
        if definition is None:
            raise ValueError(f"Winner outcome definition was not found: {definition_id}")
        planned = CohortRefreshPlanner().request_for_current_evidence(
            db,
            outcome_definition=definition,
            config=config,
            observed_at=now,
            enqueue_refresh=(
                bool(counts.get("target_stop_matured", 0))
                and get_settings().winner_probability_auto_cohort_refresh_enabled
            ),
        )
        refresh_state = planned.watermark.state
        observed = now or _utcnow()
        if counts.get("scan_completed"):
            refresh_state.last_full_scan_at = observed
        if not counts.get("pending_h5_after_cycle", 0):
            refresh_state.last_zero_due_backlog_at = observed
        refresh_state.current_due_count = int(counts.get("pending_h5_after_cycle", 0) or 0)
        refresh_state.current_deferred_count = int(counts.get("retry_deferred", 0) or 0)
        oldest_due = counts.get("oldest_due_h5_session")
        refresh_state.oldest_due_session = (
            datetime.fromisoformat(str(oldest_due)).date() if oldest_due else None
        )
        db.flush()
    elif counts.get("target_stop_matured", 0):
        enqueue_job(
            db,
            WINNER_COHORT_REFRESH,
            {"outcome_definition_id": definition_id},
            request_key=f"winner:cohort-refresh:{definition_id}",
        )
    if continuation_decision == "ENQUEUE_CONTINUATION":
        enqueue_job(
            db,
            WINNER_OUTCOME_MATURATION,
            {"limit": limit, "max_batches": max_batches, "continuation": True},
            request_key=f"winner:h5-next-open:continuation:{processing_run.id}",
            workflow_key=WINNER_MATURATION_WORKFLOW_KEY,
            single_flight_workflow=True,
            root_job_id=root_job_id,
            parent_job_id=job.id,
            continuation_depth=depth + 1,
            trigger_source="CONTINUATION",
        )
        operational_metrics.increment("winner_maturation_continuations_total")

    logger.info(
        "winner.maturation.continuation_decision",
        extra={
            "workflow_key": counts["workflow_key"],
            "root_job_id": root_job_id,
            "background_job_id": job.id,
            "processing_run_id": processing_run.id,
            "trigger_source": trigger_source,
            "continuation_depth": depth,
            "due_total": counts.get("due_total"),
            "retry_eligible_now": counts.get("retry_eligible_now"),
            "retry_deferred": retry_deferred,
            "processed_h5": processed,
            "matured_h5": counts.get("matured_h5", counts.get("matured")),
            "eligible_remaining": eligible_remaining,
            "earliest_retry_not_before": counts.get("earliest_retry_not_before"),
            "continuation_decision": continuation_decision,
            "continuation_reason": continuation_reason,
        },
    )

    response = {
        "job_type": WINNER_OUTCOME_MATURATION,
        "processing_run_id": processing_run.id,
        "status": status,
        **counts,
    }
    if continuation_decision == "DEFER_SAME_JOB" and defer_until is not None:
        observed = now or _utcnow()
        delay_seconds = max(1, math.ceil((defer_until - observed).total_seconds()))
        raise JobDeferred(
            f"{continuation_reason}: Winner maturation retry cooldown is active",
            delay_seconds=delay_seconds,
        )
    return response


def execute_outcome_revision_check_job(
    db: Session,
    job: BackgroundJob,
    *,
    outcome_service: OutcomeMaturationService | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    payload = job.payload_json or {}
    limit = _optional_int(payload, "limit") or 500
    forward_outcome_ids = tuple(_int_list(payload, "forward_outcome_ids", required=False))
    config = load_winner_probability_config()
    processing_run = _start_processing_run(
        db,
        job=job,
        process_type=WINNER_OUTCOME_REVISION_CHECK,
        run_id=None,
        config_hash=config.config_hash,
    )
    started_at = processing_run.started_at or _utcnow()
    outcome_service = outcome_service or OutcomeMaturationService()
    try:
        result = outcome_service.process_current_revisions(
            db,
            now=now,
            limit=limit,
            forward_outcome_ids=forward_outcome_ids,
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
            reason_code=type(exc).__name__.upper(),
        )
        raise
    counts = result.as_dict()
    status = JobStatus.PARTIAL if counts.get("failed", 0) else JobStatus.COMPLETED
    _finish_processing_run(
        db,
        processing_run,
        status=status,
        started_at=started_at,
        counts=counts,
        checkpoint={"last_completed_phase": "outcome_revision_check"},
    )
    if counts.get("target_stop_matured", 0) and isinstance(db, Session):
        definition = WinnerProbabilityRepository().get_outcome_definition(
            db,
            definition_id=config.primary_outcome_definition.id,
            calculation_version=config.engine.calculation_version,
        )
        if definition is None:
            raise ValueError("active Winner outcome definition was not found")
        CohortRefreshPlanner().request_for_current_evidence(
            db,
            outcome_definition=definition,
            config=config,
            observed_at=now,
            enqueue_refresh=get_settings().winner_probability_auto_cohort_refresh_enabled,
        )
    return {
        "job_type": WINNER_OUTCOME_REVISION_CHECK,
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
    settings = get_settings()
    max_groups = (
        _optional_int(payload, "max_groups") or settings.winner_cohort_refresh_max_groups_per_slice
    )
    max_wall_seconds = float(
        payload.get("max_wall_seconds") or settings.winner_cohort_refresh_max_wall_seconds
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

    def link_generation(generation: WinnerCohortGeneration) -> None:
        processing_run.cohort_generation_id = generation.id
        processing_run.last_checkpoint_at = _utcnow()
        processing_run.checkpoint_json = {
            "last_completed_phase": "generation_captured",
            "generation_id": generation.id,
            "generation_status": generation.status,
        }
        db.flush()

    try:
        result = cohort_refresh_service.refresh_cohorts(
            db,
            outcome_definition_id=str(outcome_definition_id)
            if outcome_definition_id is not None
            else None,
            training_cutoff_at=training_cutoff_at,
            # materialize_slice fences with lease_guard before every check;
            # avoid a second commit that would expire the same domain session.
            should_cancel=lambda: _check_cancel_only(db, job),
            lease_guard=lambda: _heartbeat_only(job),
            on_generation_captured=link_generation,
            max_groups=max_groups,
            max_wall_seconds=max_wall_seconds,
        )
    except (WinnerCohortRefreshCancelled, CohortMaterializationCancelled) as exc:
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
            reason_code=type(exc).__name__.upper(),
        )
        raise

    counts = result.as_dict()
    processing_run.cohort_generation_id = result.generation_id
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
            "last_completed_phase": (
                "cohort_refresh_slice" if result.continuation_required else "cohort_refresh"
            ),
            "outcome_definition_id": outcome_definition_id or config.primary_outcome_definition.id,
            "generation_id": result.generation_id,
            "completed_groups": result.completed_groups,
            "planned_groups": result.planned_groups,
        },
    )
    if result.continuation_required:
        processing_run.terminal_reason_code = "SLICE_COMPLETE"
        db.flush()
        _heartbeat_only(job)
        raise JobDeferred("winner cohort generation continuation", delay_seconds=1)
    return {
        "job_type": WINNER_COHORT_REFRESH,
        "processing_run_id": processing_run.id,
        "status": status,
        **counts,
    }


def execute_latest_rescore_job(
    db: Session,
    job: BackgroundJob,
    *,
    probability_estimator: ProbabilityEstimator | None = None,
) -> dict[str, Any]:
    """Rescore one frozen target manifest against one published generation."""
    payload = dict(job.payload_json or {})
    config = load_winner_probability_config()
    processing_run = _start_processing_run(
        db,
        job=job,
        process_type=WINNER_LATEST_RESCORE,
        run_id=None,
        config_hash=config.config_hash,
    )
    started_at = processing_run.started_at or _utcnow()
    probability_estimator = probability_estimator or ProbabilityEstimator()
    generation_service = CohortGenerationService()
    repository = WinnerProbabilityRepository()

    try:
        definition = repository.get_outcome_definition(
            db,
            definition_id=str(
                payload.get("outcome_definition_id") or config.primary_outcome_definition.id
            ),
            calculation_version=config.engine.calculation_version,
        )
        if definition is None:
            raise ValueError("active Winner outcome definition was not found")
        generation_id = _optional_int(payload, "cohort_generation_id")
        generation = (
            db.get(WinnerCohortGeneration, generation_id)
            if generation_id is not None
            else generation_service.get_published_cohort_generation(
                db, contract=contract_for(definition, config)
            )
        )
        if generation is None:
            raise ValueError("no published cohort generation is available")
        processing_run.cohort_generation_id = generation.id

        target_ids = payload.get("target_prediction_ids")
        if target_ids is None:
            target_ids = _freeze_latest_rescore_targets(db, payload)
            payload = {
                **payload,
                "target_prediction_ids": target_ids,
                "planned": len(target_ids),
                "cursor_prediction_id": 0,
                "cohort_generation_id": generation.id,
            }
            job.payload_json = dict(payload)
            db.flush()
            _heartbeat_only(job)
        target_ids = sorted({_coerce_int(value, "target_prediction_ids") for value in target_ids})
        if len(target_ids) > 10_000:
            raise ValueError("latest rescore target manifest exceeds the 10,000-row safety cap")
        batch_size = min(
            500,
            max(
                1,
                _optional_int(payload, "batch_size")
                or get_settings().winner_latest_rescore_max_predictions_per_slice,
            ),
        )
        cursor = _optional_int(payload, "cursor_prediction_id") or 0
        batch_ids = [prediction_id for prediction_id in target_ids if prediction_id > cursor][
            :batch_size
        ]
        predictions = {
            row.id: row
            for row in db.scalars(
                select(WinnerPredictionSnapshot).where(WinnerPredictionSnapshot.id.in_(batch_ids))
            )
        }
        counts = {"processed": 0, "estimated": 0, "duplicate": 0, "insufficient": 0}
        if generation.status != CohortGenerationStatus.PUBLISHED:
            counts["stale_generation"] = 1
            batch_ids = []
        for prediction_id in batch_ids:
            _heartbeat_only(job)
            if generation.status != CohortGenerationStatus.PUBLISHED:
                counts["stale_generation"] = 1
                break
            prediction = predictions.get(prediction_id)
            if prediction is None:
                counts["insufficient"] += 1
            else:
                result = probability_estimator.create_latest_rescore_from_generation(
                    db,
                    prediction=prediction,
                    outcome_definition=definition,
                    generation=generation,
                    config=config,
                )
                counts[result.status] += 1
            counts["processed"] += 1
            payload = {**payload, "cursor_prediction_id": prediction_id}
            job.payload_json = dict(payload)
            db.flush()
        _heartbeat_only(job)
    except Exception as exc:
        _finish_processing_run(
            db,
            processing_run,
            status=JobStatus.FAILED,
            started_at=started_at,
            error=str(exc),
            reason_code=type(exc).__name__.upper(),
        )
        raise

    cursor = int(payload.get("cursor_prediction_id") or 0)
    remaining = sum(prediction_id > cursor for prediction_id in target_ids)
    checkpoint = {
        "phase": "RESCORE",
        "cohort_generation_id": generation.id,
        "last_prediction_id": cursor or None,
        "completed": len(target_ids) - remaining,
        "planned": len(target_ids),
    }
    _finish_processing_run(
        db,
        processing_run,
        status=JobStatus.COMPLETED,
        started_at=started_at,
        counts={**counts, "remaining": remaining},
        checkpoint=checkpoint,
    )
    if remaining and not counts.get("stale_generation"):
        processing_run.terminal_reason_code = "SLICE_COMPLETE"
        db.flush()
        raise JobDeferred("winner latest rescore continuation", delay_seconds=1)
    return {
        "job_type": WINNER_LATEST_RESCORE,
        "processing_run_id": processing_run.id,
        "status": JobStatus.COMPLETED,
        "cohort_generation_id": generation.id,
        **counts,
        "remaining": remaining,
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
            reason_code=type(exc).__name__.upper(),
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
    prior_running: list[WinnerProcessingRun] = []
    attempt_no = 1
    if isinstance(db, Session) and job.id is not None:
        prior_running = list(
            db.scalars(
                select(WinnerProcessingRun)
                .where(WinnerProcessingRun.background_job_id == job.id)
                .where(WinnerProcessingRun.status == JobStatus.RUNNING)
                .with_for_update()
            )
        )
        attempt_no = (
            int(
                db.scalar(
                    select(func.max(WinnerProcessingRun.attempt_no)).where(
                        WinnerProcessingRun.background_job_id == job.id
                    )
                )
                or 0
            )
            + 1
        )
        for prior in prior_running:
            prior.status = "SUPERSEDED"
            prior.completed_at = now
            prior.terminal_reason_code = "NEW_ATTEMPT_SUPERSEDED"
    token_metadata = redacted_token_metadata(job.execution_token)
    processing_run = WinnerProcessingRun(
        background_job_id=job.id,
        run_id=run_id,
        process_type=process_type,
        status=JobStatus.RUNNING,
        config_hash=config_hash,
        attempt_no=attempt_no,
        attempt_correlation_id=token_metadata.get("execution_token_hash"),
        started_at=now,
        counts_json={},
        checkpoint_json={},
        metadata_json={
            "background_job_type": job.job_type,
            **token_metadata,
            "lease_owner": job.lease_owner,
            "workflow_key": job.workflow_key,
            "root_job_id": job.root_job_id,
            "parent_job_id": job.parent_job_id,
            "continuation_depth": job.continuation_depth,
            "trigger_source": job.trigger_source,
        },
    )
    db.add(processing_run)
    db.flush()
    for prior in prior_running:
        prior.superseded_by_processing_run_id = processing_run.id
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
    reason_code: str | None = None,
) -> None:
    completed_at = _utcnow()
    processing_run.status = status
    processing_run.completed_at = completed_at
    if source_cutoff_at is not None:
        processing_run.source_cutoff_at = source_cutoff_at
    processing_run.counts_json = counts or processing_run.counts_json or {}
    processing_run.checkpoint_json = checkpoint or processing_run.checkpoint_json or {}
    if checkpoint is not None:
        processing_run.last_checkpoint_at = completed_at
    processing_run.error_message = _safe_error(error) if error else None
    if reason_code is not None:
        processing_run.terminal_reason_code = reason_code
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


def _check_cancel_only(db: Session, job: BackgroundJob) -> bool:
    return is_cancel_requested(db, job.id)


def _heartbeat_only(job: BackgroundJob) -> None:
    heartbeat = getattr(job, "_heartbeat", None)
    if callable(heartbeat):
        heartbeat()


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


def _freeze_latest_rescore_targets(db: Session, payload: dict[str, Any]) -> list[int]:
    scope = payload.get("scope")
    if not isinstance(scope, dict):
        raise ValueError("latest rescore requires an explicit scope")
    scope_type = str(scope.get("type") or "").upper()
    if scope_type == "RUN":
        run_id = _coerce_int(scope.get("run_id"), "scope.run_id")
        return list(
            db.scalars(
                select(WinnerPredictionSnapshot.id)
                .where(WinnerPredictionSnapshot.run_id == run_id)
                .where(
                    WinnerPredictionSnapshot.eligibility_status == PredictionEligibility.ELIGIBLE
                )
                .where(WinnerPredictionSnapshot.superseded_at.is_(None))
                .order_by(WinnerPredictionSnapshot.id.asc())
            )
        )
    if scope_type in {"PREDICTION_IDS", "EXPLICIT_PREDICTIONS"}:
        values = scope.get("prediction_ids")
        if not isinstance(values, list | tuple):
            raise ValueError("explicit latest rescore scope requires prediction_ids")
        return sorted({_coerce_int(value, "scope.prediction_ids") for value in values})
    raise ValueError(
        "latest rescore scope must be RUN or EXPLICIT_PREDICTIONS; "
        "ALL_HISTORICAL_ELIGIBLE is not supported"
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


def _parse_optional_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


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


def classify_maturation_status(counts: dict[str, Any]) -> str:
    """Classify execution health independently from deferred market data."""
    return (
        JobStatus.PARTIAL
        if counts.get("failed", 0)
        or counts.get("failed_h5", 0)
        or counts.get("unvisited_h5_after_cycle", 0)
        else JobStatus.COMPLETED
    )


def _utcnow() -> datetime:
    return datetime.now(UTC)
