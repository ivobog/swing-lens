from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import (
    CombinedResult,
    IBFetchRun,
    PipelineRun,
    PipelineStep,
    RawCompanyRow,
    UploadRun,
)
from app.services.background_job_service import JobLeaseLost
from app.services.bar_cache_service import DEFAULT_WHAT_TO_SHOW
from app.services.ceri.constants import CERI_PIPELINE_CAPTURE_STEP
from app.services.combined_decision import refresh_combined_results
from app.services.fundamental_score_service import recalculate_run_fundamentals
from app.services.ib_fetch_executor import execute_fetch_plan
from app.services.ib_fetch_plan_service import FetchPlan, build_fetch_plan
from app.services.market_regime_command_center import MarketRegimeCommandCenterService
from app.services.market_regime_dtos import MarketRegimeCommandCenterDto
from app.services.pipeline_service import (
    PipelineStatus,
    PipelineStepStatus,
)
from app.services.redaction import redact_sensitive
from app.services.sector_rotation_dtos import SectorRotationSnapshotDto
from app.services.sector_rotation_service import SectorRotationService
from app.services.setup_lifecycle.constants import (
    SLSE_PIPELINE_CAPTURE_STEP,
    SLSE_PIPELINE_EVALUATION_STEP,
)
from app.services.technical_score_service import score_run_technicals
from app.settings import get_settings


class PipelineCancelled(Exception):
    pass


def build_market_regime_snapshot_for_run(
    db: Session,
    run_id: int,
) -> MarketRegimeCommandCenterDto:
    return MarketRegimeCommandCenterService().build_snapshot(db, run_id=run_id)


def build_sector_rotation_snapshot_for_run(
    db: Session,
    run_id: int,
) -> SectorRotationSnapshotDto:
    return SectorRotationService().build_sector_rotation_snapshot(db, run_id=run_id)


@dataclass(frozen=True)
class PipelineExecutionResult:
    pipeline_run_id: int
    upload_run_id: int
    status: str
    uploaded_rows: int
    fundamental_scores: int
    ib_planned_requests: int
    ib_executed_requests: int
    ib_success_count: int
    ib_failure_count: int
    ib_skipped_count: int
    technical_scores: int
    market_regime_snapshots: int
    market_regime: str | None
    market_risk_state: str | None
    market_regime_confidence: str | None
    market_regime_warning_count: int
    combined_results: int
    sector_rotation_snapshots: int
    sector_rotation_sector_count: int
    sector_rotation_leading_sector: str | None
    sector_rotation_weakest_sector: str | None
    sector_rotation_warning_count: int
    ceri_score_snapshots: int
    ceri_change_events: int
    ceri_alerts: int
    ceri_unrated: int
    ceri_quarantined: int
    ceri_conflicted: int
    ceri_stale: int
    ceri_failed: int
    ceri_capture_skipped: int
    setup_lifecycle_snapshots_captured: int
    setup_lifecycle_canonical_snapshots: int
    setup_lifecycle_change_events: int
    setup_lifecycle_transitions: int
    setup_lifecycle_alerts: int
    setup_lifecycle_active_episodes: int
    setup_lifecycle_low_confidence: int
    setup_lifecycle_failed: int
    setup_lifecycle_capture_skipped: int
    setup_lifecycle_evaluation_skipped: int
    winner_prediction_inserted: int
    winner_prediction_duplicate: int
    winner_prediction_excluded: int
    winner_prediction_failed: int
    winner_prediction_pending_outcomes: int
    winner_prediction_decision_time_estimates: int
    winner_prediction_capture_skipped: int
    incomplete_rows: int
    warning_rows: int


@dataclass(frozen=True)
class PipelineExecutionDependencies:
    recalculate_fundamentals: Callable[[Session, int], list[Any]] = recalculate_run_fundamentals
    build_fetch_plan: Callable[..., FetchPlan] = build_fetch_plan
    execute_fetch_plan: Callable[..., IBFetchRun] = execute_fetch_plan
    score_technicals: Callable[[Session, int], list[Any]] = score_run_technicals
    build_market_regime_snapshot: Callable[[Session, int], MarketRegimeCommandCenterDto] = (
        build_market_regime_snapshot_for_run
    )
    refresh_combined: Callable[[Session, int], list[CombinedResult]] = refresh_combined_results
    build_sector_rotation_snapshot: Callable[[Session, int], SectorRotationSnapshotDto] = (
        build_sector_rotation_snapshot_for_run
    )
    capture_ceri_snapshot: Callable[[Session, int], Any] | None = None
    ceri_run_capture_enabled: bool | None = None
    capture_setup_signals: Callable[[Session, int], Any] | None = None
    evaluate_setup_lifecycles: Callable[[Session, int], Any] | None = None
    setup_lifecycle_pipeline_step_enabled: bool | None = None
    capture_winner_predictions: Callable[[Session, int], Any] | None = None
    winner_probability_capture_enabled: bool | None = None


def execute_full_pipeline(
    db: Session,
    pipeline_run_id: int,
    should_cancel: Callable[[], bool] | None = None,
    lease_guard: Callable[[], None] | None = None,
    dependencies: PipelineExecutionDependencies | None = None,
) -> PipelineExecutionResult:
    dependencies = dependencies or PipelineExecutionDependencies()
    pipeline = _require_pipeline(db, pipeline_run_id)
    upload_run = _require_upload_run(db, pipeline.upload_run_id)
    should_cancel = should_cancel or (lambda: False)

    result = _empty_result(pipeline, upload_run)
    try:
        _mark_pipeline_running(db, pipeline, lease_guard=lease_guard)
        _raise_if_cancelled(should_cancel)

        with _pipeline_step(db, pipeline, "VALIDATING_RUN", lease_guard=lease_guard):
            tickers = _tickers_for_run(db, upload_run.id)
            if not tickers:
                raise ValueError("No uploaded tickers are available for this run.")
            result["uploaded_rows"] = upload_run.row_count or len(tickers)

        _raise_if_cancelled(should_cancel)
        with _pipeline_step(db, pipeline, "SCORING_FUNDAMENTALS", lease_guard=lease_guard):
            fundamental_scores = dependencies.recalculate_fundamentals(db, upload_run.id)
            result["fundamental_scores"] = len(fundamental_scores)

        _raise_if_cancelled(should_cancel)
        with _pipeline_step(db, pipeline, "FETCHING_MARKET_DATA", lease_guard=lease_guard):
            plan = dependencies.build_fetch_plan(
                db=db,
                tickers=tickers,
                run_id=upload_run.id,
                include_benchmarks=True,
                what_to_show_values=DEFAULT_WHAT_TO_SHOW,
            )
            result["ib_planned_requests"] = plan.estimated_request_count
            fetch_run = None
            if plan.estimated_request_count:
                fetch_run = dependencies.execute_fetch_plan(
                    db=db,
                    plan=plan,
                    include_benchmarks=True,
                    should_cancel=should_cancel,
                )
                _apply_fetch_result(result, fetch_run)
                if fetch_run.status == "CANCELLED":
                    raise PipelineCancelled("Pipeline cancelled during market data fetch.")
            else:
                result["ib_skipped_count"] = plan.estimated_skips

        _raise_if_cancelled(should_cancel)
        with _pipeline_step(db, pipeline, "SCORING_TECHNICALS", lease_guard=lease_guard):
            technical_scores = dependencies.score_technicals(db, upload_run.id)
            result["technical_scores"] = len(technical_scores)
            result["technical_error_count"] = _technical_error_count(technical_scores)

        _raise_if_cancelled(should_cancel)
        with _pipeline_step(db, pipeline, "MARKET_REGIME_SNAPSHOT", lease_guard=lease_guard):
            snapshot = dependencies.build_market_regime_snapshot(db, upload_run.id)
            result["market_regime_snapshots"] = 1
            result["market_regime"] = snapshot.regime
            result["market_risk_state"] = snapshot.risk_state
            result["market_regime_confidence"] = snapshot.confidence
            result["market_regime_warning_count"] = len(snapshot.warnings)
            result["market_regime_low_confidence"] = int(snapshot.confidence == "low")

        _raise_if_cancelled(should_cancel)
        with _pipeline_step(db, pipeline, "COMBINING_RESULTS", lease_guard=lease_guard):
            combined_results = dependencies.refresh_combined(db, upload_run.id)
            result["combined_results"] = len(combined_results)
            result["incomplete_rows"] = sum(not row.is_complete for row in combined_results)
            result["warning_rows"] = sum(row.has_warning for row in combined_results)

        _raise_if_cancelled(should_cancel)
        with _pipeline_step(db, pipeline, "SECTOR_ROTATION_SNAPSHOT", lease_guard=lease_guard):
            sector_snapshot = dependencies.build_sector_rotation_snapshot(db, upload_run.id)
            result["sector_rotation_snapshots"] = 1
            result["sector_rotation_sector_count"] = int(
                sector_snapshot.summary.get("sector_count") or len(sector_snapshot.rows)
            )
            result["sector_rotation_leading_sector"] = sector_snapshot.summary.get("leading_sector")
            result["sector_rotation_weakest_sector"] = sector_snapshot.summary.get("weakest_sector")
            result["sector_rotation_warning_count"] = len(sector_snapshot.warnings)

        if _ceri_run_capture_enabled(dependencies):
            _raise_if_cancelled(should_cancel)
            with _pipeline_step(db, pipeline, CERI_PIPELINE_CAPTURE_STEP, lease_guard=lease_guard):
                capture = dependencies.capture_ceri_snapshot or _capture_ceri_snapshot
                ceri_result = capture(db, upload_run.id)
                _apply_ceri_capture_result(result, ceri_result)

        if _setup_lifecycle_pipeline_step_enabled(dependencies):
            _raise_if_cancelled(should_cancel)
            with _pipeline_step(
                db,
                pipeline,
                SLSE_PIPELINE_CAPTURE_STEP,
                lease_guard=lease_guard,
            ):
                capture = dependencies.capture_setup_signals or _capture_setup_signals
                capture_result = capture(db, upload_run.id)
                _apply_setup_lifecycle_capture_result(result, capture_result)

            _raise_if_cancelled(should_cancel)
            with _pipeline_step(
                db,
                pipeline,
                SLSE_PIPELINE_EVALUATION_STEP,
                lease_guard=lease_guard,
            ):
                evaluate = dependencies.evaluate_setup_lifecycles or _evaluate_setup_lifecycles
                evaluation_result = evaluate(
                    db,
                    upload_run.id,
                )
                _apply_setup_lifecycle_evaluation_result(result, evaluation_result)

        _raise_if_cancelled(should_cancel)
        with _pipeline_step(
            db,
            pipeline,
            "CAPTURING_WINNER_PREDICTIONS",
            lease_guard=lease_guard,
        ):
            if _winner_probability_capture_enabled(dependencies):
                capture = dependencies.capture_winner_predictions or _capture_winner_predictions
                capture_result = capture(db, upload_run.id)
                _apply_winner_capture_result(result, capture_result)
            else:
                result["winner_prediction_capture_skipped"] = 1

        final_status = _final_pipeline_status(result)
        _mark_pipeline_finished(db, pipeline, final_status, result, lease_guard=lease_guard)
        return _to_execution_result(pipeline, result)
    except JobLeaseLost:
        raise
    except PipelineCancelled:
        _mark_pipeline_cancelled(db, pipeline, lease_guard=lease_guard)
        raise
    except Exception as exc:
        _mark_pipeline_failed(db, pipeline, exc, lease_guard=lease_guard)
        raise


@contextmanager
def _pipeline_step(
    db: Session,
    pipeline: PipelineRun,
    step_name: str,
    *,
    lease_guard: Callable[[], None] | None = None,
):
    step = _require_step(db, pipeline.id, step_name)
    if step.status in {
        PipelineStepStatus.RUNNING,
        PipelineStepStatus.COMPLETED,
        PipelineStepStatus.FAILED,
        PipelineStepStatus.CANCELLED,
    }:
        step.retry_count = (step.retry_count or 0) + 1
        step.message = f"Replaying step attempt {step.retry_count + 1}."
    else:
        step.message = None
    pipeline.current_step = step_name
    pipeline.status = _pipeline_status_for_step(step_name)
    step.status = PipelineStepStatus.RUNNING
    step.started_at = step.started_at or _utcnow()
    step.error_message = None
    _save_progress(db, lease_guard=lease_guard)
    try:
        yield step
    except JobLeaseLost:
        raise
    except PipelineCancelled:
        step.status = PipelineStepStatus.CANCELLED
        step.completed_at = _utcnow()
        step.error_message = "Pipeline cancellation requested."
        _save_progress(db, lease_guard=lease_guard)
        raise
    except Exception as exc:
        step.status = PipelineStepStatus.FAILED
        step.completed_at = _utcnow()
        step.error_message = _safe_message(str(exc))
        _save_progress(db, lease_guard=lease_guard)
        raise
    else:
        step.status = PipelineStepStatus.COMPLETED
        step.completed_at = _utcnow()
        _save_progress(db, lease_guard=lease_guard)


def _require_pipeline(db: Session, pipeline_run_id: int) -> PipelineRun:
    pipeline = db.get(PipelineRun, pipeline_run_id)
    if pipeline is None:
        raise ValueError(f"Pipeline run {pipeline_run_id} was not found.")
    return pipeline


def _require_upload_run(db: Session, upload_run_id: int) -> UploadRun:
    upload_run = db.get(UploadRun, upload_run_id)
    if upload_run is None:
        raise ValueError(f"Upload run {upload_run_id} was not found.")
    return upload_run


def _require_step(db: Session, pipeline_run_id: int, step_name: str) -> PipelineStep:
    step = db.scalar(
        select(PipelineStep)
        .where(PipelineStep.pipeline_run_id == pipeline_run_id)
        .where(PipelineStep.step_name == step_name)
    )
    if step is None:
        raise ValueError(f"Pipeline step {step_name} was not found.")
    return step


def _tickers_for_run(db: Session, upload_run_id: int) -> list[str]:
    rows = db.scalars(
        select(RawCompanyRow.ticker)
        .where(RawCompanyRow.run_id == upload_run_id)
        .order_by(RawCompanyRow.row_number)
    )
    seen: set[str] = set()
    tickers: list[str] = []
    for value in rows:
        ticker = str(value).strip().upper()
        if ticker and ticker not in seen:
            seen.add(ticker)
            tickers.append(ticker)
    return tickers


def _mark_pipeline_running(
    db: Session,
    pipeline: PipelineRun,
    *,
    lease_guard: Callable[[], None] | None = None,
) -> None:
    pipeline.status = PipelineStatus.RUNNING
    pipeline.started_at = pipeline.started_at or _utcnow()
    pipeline.completed_at = None
    pipeline.error_message = None
    pipeline.message = "Full pipeline is running."
    _save_progress(db, lease_guard=lease_guard)


def _mark_pipeline_finished(
    db: Session,
    pipeline: PipelineRun,
    status: str,
    result: dict[str, Any],
    *,
    lease_guard: Callable[[], None] | None = None,
) -> None:
    pipeline.status = status
    pipeline.current_step = None
    pipeline.completed_at = _utcnow()
    pipeline.result_json = {
        **(pipeline.result_json or {}),
        **_public_result(result),
    }
    pipeline.message = _completion_message(status, result)
    pipeline.error_message = None
    _save_progress(db, lease_guard=lease_guard)


def _mark_pipeline_cancelled(
    db: Session,
    pipeline: PipelineRun,
    *,
    lease_guard: Callable[[], None] | None = None,
) -> None:
    pipeline.status = PipelineStatus.CANCELLED
    pipeline.completed_at = _utcnow()
    pipeline.message = "Pipeline was cancelled."
    pipeline.error_message = None
    _cancel_unfinished_steps(db, pipeline.id)
    _save_progress(db, lease_guard=lease_guard)


def _mark_pipeline_failed(
    db: Session,
    pipeline: PipelineRun,
    exc: Exception,
    *,
    lease_guard: Callable[[], None] | None = None,
) -> None:
    pipeline.status = PipelineStatus.FAILED
    pipeline.completed_at = _utcnow()
    pipeline.message = "Pipeline failed."
    pipeline.error_message = _safe_message(str(exc))
    _save_progress(db, lease_guard=lease_guard)


def _cancel_unfinished_steps(db: Session, pipeline_run_id: int) -> None:
    for step in db.scalars(
        select(PipelineStep).where(PipelineStep.pipeline_run_id == pipeline_run_id)
    ):
        if step.status in {PipelineStepStatus.PENDING, PipelineStepStatus.RUNNING}:
            step.status = PipelineStepStatus.CANCELLED
            step.completed_at = _utcnow()


def _save_progress(db: Session, *, lease_guard: Callable[[], None] | None = None) -> None:
    if lease_guard is not None:
        lease_guard()
    db.flush()
    commit = getattr(db, "commit", None)
    if callable(commit):
        commit()


def _raise_if_cancelled(should_cancel: Callable[[], bool]) -> None:
    if should_cancel():
        raise PipelineCancelled("Pipeline cancellation requested.")


def _pipeline_status_for_step(step_name: str) -> str:
    if step_name in {
        PipelineStatus.SCORING_FUNDAMENTALS,
        PipelineStatus.FETCHING_MARKET_DATA,
        PipelineStatus.SCORING_TECHNICALS,
        PipelineStatus.MARKET_REGIME_SNAPSHOT,
        PipelineStatus.COMBINING_RESULTS,
        PipelineStatus.SECTOR_ROTATION_SNAPSHOT,
        PipelineStatus.CERI_CAPTURE_SNAPSHOT,
        PipelineStatus.CAPTURING_SETUP_SIGNALS,
        PipelineStatus.EVALUATING_SETUP_LIFECYCLES,
        PipelineStatus.CAPTURING_WINNER_PREDICTIONS,
    }:
        return step_name
    return PipelineStatus.RUNNING


def _apply_fetch_result(result: dict[str, Any], fetch_run: IBFetchRun) -> None:
    result["ib_executed_requests"] = fetch_run.executed_request_count or 0
    result["ib_success_count"] = fetch_run.success_count or 0
    result["ib_failure_count"] = fetch_run.failure_count or 0
    result["ib_skipped_count"] = fetch_run.skipped_count or 0
    result["fetch_failed"] = int(fetch_run.status in {"FAILED", "PARTIAL"})


def _technical_error_count(scores: list[Any]) -> int:
    return sum(
        bool(getattr(score, "insufficient_data", False))
        or getattr(score, "technical_confidence", None) in {"low", "error"}
        for score in scores
    )


def _final_pipeline_status(result: dict[str, Any]) -> str:
    if result["combined_results"] <= 0:
        return PipelineStatus.FAILED
    if (
        result["incomplete_rows"]
        or result["warning_rows"]
        or result["ib_failure_count"]
        or result["technical_error_count"]
        or result["fetch_failed"]
        or result["market_regime_low_confidence"]
        or result["ceri_failed"]
        or result["setup_lifecycle_failed"]
        or result["winner_prediction_failed"]
    ):
        return PipelineStatus.PARTIAL
    return PipelineStatus.COMPLETED


def _completion_message(status: str, result: dict[str, Any]) -> str:
    if status == PipelineStatus.COMPLETED:
        return f"Pipeline completed with {result['combined_results']} combined rows."
    if status == PipelineStatus.PARTIAL:
        return (
            f"Pipeline completed partially with {result['combined_results']} combined rows, "
            f"{result['incomplete_rows']} incomplete rows, and "
            f"{result['ib_failure_count']} IB failures."
        )
    return "Pipeline failed before combined results were produced."


def _empty_result(pipeline: PipelineRun, upload_run: UploadRun) -> dict[str, Any]:
    return {
        "pipeline_run_id": pipeline.id,
        "upload_run_id": upload_run.id,
        "uploaded_rows": upload_run.row_count or 0,
        "fundamental_scores": 0,
        "ib_planned_requests": 0,
        "ib_executed_requests": 0,
        "ib_success_count": 0,
        "ib_failure_count": 0,
        "ib_skipped_count": 0,
        "technical_scores": 0,
        "technical_error_count": 0,
        "market_regime_snapshots": 0,
        "market_regime": None,
        "market_risk_state": None,
        "market_regime_confidence": None,
        "market_regime_warning_count": 0,
        "market_regime_low_confidence": 0,
        "combined_results": 0,
        "sector_rotation_snapshots": 0,
        "sector_rotation_sector_count": 0,
        "sector_rotation_leading_sector": None,
        "sector_rotation_weakest_sector": None,
        "sector_rotation_warning_count": 0,
        "ceri_score_snapshots": 0,
        "ceri_change_events": 0,
        "ceri_alerts": 0,
        "ceri_unrated": 0,
        "ceri_quarantined": 0,
        "ceri_conflicted": 0,
        "ceri_stale": 0,
        "ceri_failed": 0,
        "ceri_capture_skipped": 0,
        "setup_lifecycle_snapshots_captured": 0,
        "setup_lifecycle_canonical_snapshots": 0,
        "setup_lifecycle_change_events": 0,
        "setup_lifecycle_transitions": 0,
        "setup_lifecycle_alerts": 0,
        "setup_lifecycle_active_episodes": 0,
        "setup_lifecycle_low_confidence": 0,
        "setup_lifecycle_failed": 0,
        "setup_lifecycle_capture_skipped": 0,
        "setup_lifecycle_evaluation_skipped": 0,
        "winner_prediction_inserted": 0,
        "winner_prediction_duplicate": 0,
        "winner_prediction_excluded": 0,
        "winner_prediction_failed": 0,
        "winner_prediction_pending_outcomes": 0,
        "winner_prediction_decision_time_estimates": 0,
        "winner_prediction_capture_skipped": 0,
        "incomplete_rows": 0,
        "warning_rows": 0,
        "fetch_failed": 0,
    }


def _to_execution_result(
    pipeline: PipelineRun,
    result: dict[str, Any],
) -> PipelineExecutionResult:
    return PipelineExecutionResult(
        pipeline_run_id=pipeline.id,
        upload_run_id=result["upload_run_id"],
        status=pipeline.status,
        uploaded_rows=result["uploaded_rows"],
        fundamental_scores=result["fundamental_scores"],
        ib_planned_requests=result["ib_planned_requests"],
        ib_executed_requests=result["ib_executed_requests"],
        ib_success_count=result["ib_success_count"],
        ib_failure_count=result["ib_failure_count"],
        ib_skipped_count=result["ib_skipped_count"],
        technical_scores=result["technical_scores"],
        market_regime_snapshots=result["market_regime_snapshots"],
        market_regime=result["market_regime"],
        market_risk_state=result["market_risk_state"],
        market_regime_confidence=result["market_regime_confidence"],
        market_regime_warning_count=result["market_regime_warning_count"],
        combined_results=result["combined_results"],
        sector_rotation_snapshots=result["sector_rotation_snapshots"],
        sector_rotation_sector_count=result["sector_rotation_sector_count"],
        sector_rotation_leading_sector=result["sector_rotation_leading_sector"],
        sector_rotation_weakest_sector=result["sector_rotation_weakest_sector"],
        sector_rotation_warning_count=result["sector_rotation_warning_count"],
        ceri_score_snapshots=result["ceri_score_snapshots"],
        ceri_change_events=result["ceri_change_events"],
        ceri_alerts=result["ceri_alerts"],
        ceri_unrated=result["ceri_unrated"],
        ceri_quarantined=result["ceri_quarantined"],
        ceri_conflicted=result["ceri_conflicted"],
        ceri_stale=result["ceri_stale"],
        ceri_failed=result["ceri_failed"],
        ceri_capture_skipped=result["ceri_capture_skipped"],
        setup_lifecycle_snapshots_captured=result["setup_lifecycle_snapshots_captured"],
        setup_lifecycle_canonical_snapshots=result["setup_lifecycle_canonical_snapshots"],
        setup_lifecycle_change_events=result["setup_lifecycle_change_events"],
        setup_lifecycle_transitions=result["setup_lifecycle_transitions"],
        setup_lifecycle_alerts=result["setup_lifecycle_alerts"],
        setup_lifecycle_active_episodes=result["setup_lifecycle_active_episodes"],
        setup_lifecycle_low_confidence=result["setup_lifecycle_low_confidence"],
        setup_lifecycle_failed=result["setup_lifecycle_failed"],
        setup_lifecycle_capture_skipped=result["setup_lifecycle_capture_skipped"],
        setup_lifecycle_evaluation_skipped=result["setup_lifecycle_evaluation_skipped"],
        winner_prediction_inserted=result["winner_prediction_inserted"],
        winner_prediction_duplicate=result["winner_prediction_duplicate"],
        winner_prediction_excluded=result["winner_prediction_excluded"],
        winner_prediction_failed=result["winner_prediction_failed"],
        winner_prediction_pending_outcomes=result["winner_prediction_pending_outcomes"],
        winner_prediction_decision_time_estimates=result[
            "winner_prediction_decision_time_estimates"
        ],
        winner_prediction_capture_skipped=result["winner_prediction_capture_skipped"],
        incomplete_rows=result["incomplete_rows"],
        warning_rows=result["warning_rows"],
    )


def _public_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in result.items()
        if key
        not in {
            "fetch_failed",
            "technical_error_count",
            "market_regime_low_confidence",
        }
    }


def _safe_message(message: str) -> str:
    return str(redact_sensitive(message)).replace("\n", " ").strip()[:500]


def _setup_lifecycle_pipeline_step_enabled(dependencies: PipelineExecutionDependencies) -> bool:
    if dependencies.setup_lifecycle_pipeline_step_enabled is not None:
        return dependencies.setup_lifecycle_pipeline_step_enabled
    return get_settings().setup_lifecycle_pipeline_step_enabled


def _ceri_run_capture_enabled(dependencies: PipelineExecutionDependencies) -> bool:
    if dependencies.ceri_run_capture_enabled is not None:
        return dependencies.ceri_run_capture_enabled
    return get_settings().ceri_run_capture_enabled


def _winner_probability_capture_enabled(dependencies: PipelineExecutionDependencies) -> bool:
    if dependencies.winner_probability_capture_enabled is not None:
        return dependencies.winner_probability_capture_enabled
    return get_settings().winner_probability_capture_in_pipeline


def _capture_ceri_snapshot(db: Session, run_id: int):
    from app.services.ceri.capture_service import CeriRunCaptureService

    return CeriRunCaptureService().capture_run(db, run_id)


def _capture_winner_predictions(db: Session, run_id: int):
    from app.services.winner_probability.capture_service import WinnerPredictionCaptureService

    return WinnerPredictionCaptureService().capture_run(db, run_id=run_id)


def _capture_setup_signals(db: Session, run_id: int):
    from app.services.setup_lifecycle.snapshot_builder import (
        SetupLifecycleSnapshotCaptureService,
    )

    return SetupLifecycleSnapshotCaptureService().capture_snapshots_for_run(db, run_id)


def _evaluate_setup_lifecycles(db: Session, run_id: int):
    from app.services.setup_lifecycle.evaluation_service import (
        SetupLifecycleEvaluationService,
    )

    return SetupLifecycleEvaluationService().evaluate_run(db, run_id)


def _apply_ceri_capture_result(result: dict[str, Any], ceri_result: Any) -> None:
    values = ceri_result.as_dict() if hasattr(ceri_result, "as_dict") else dict(ceri_result)
    result["ceri_score_snapshots"] = int(
        values.get("score_snapshots", values.get("snapshots_captured", 0))
    )
    result["ceri_change_events"] = int(values.get("change_events", values.get("changes", 0)))
    result["ceri_alerts"] = int(values.get("alerts", 0))
    result["ceri_unrated"] = int(values.get("unrated", 0))
    result["ceri_quarantined"] = int(values.get("quarantined", 0))
    result["ceri_conflicted"] = int(values.get("conflicted", 0))
    result["ceri_stale"] = int(values.get("stale", 0))
    result["ceri_failed"] = int(values.get("failed", 0))
    result["ceri_capture_skipped"] = int(values.get("skipped", 0))


def _apply_winner_capture_result(result: dict[str, Any], capture_result: Any) -> None:
    values = (
        capture_result.as_dict() if hasattr(capture_result, "as_dict") else dict(capture_result)
    )
    result["winner_prediction_inserted"] = int(values.get("inserted", 0))
    result["winner_prediction_duplicate"] = int(values.get("duplicate", 0))
    result["winner_prediction_excluded"] = int(values.get("excluded", 0))
    result["winner_prediction_failed"] = int(values.get("failed", 0))
    result["winner_prediction_pending_outcomes"] = int(values.get("pending_outcomes", 0))
    result["winner_prediction_decision_time_estimates"] = int(
        values.get("decision_time_estimates", 0)
    )


def _apply_setup_lifecycle_capture_result(result: dict[str, Any], capture_result: Any) -> None:
    values = (
        capture_result.as_dict()
        if hasattr(capture_result, "as_dict")
        else dict(capture_result)
    )
    result["setup_lifecycle_snapshots_captured"] = int(
        values.get("snapshots_captured", values.get("captured", 0))
    )
    result["setup_lifecycle_canonical_snapshots"] = int(
        values.get("canonical_snapshots", values.get("canonical", 0))
    )
    result["setup_lifecycle_low_confidence"] += int(values.get("low_confidence", 0))
    result["setup_lifecycle_failed"] += int(values.get("failed", 0))


def _apply_setup_lifecycle_evaluation_result(
    result: dict[str, Any],
    evaluation_result: Any,
) -> None:
    values = (
        evaluation_result.as_dict()
        if hasattr(evaluation_result, "as_dict")
        else dict(evaluation_result)
    )
    result["setup_lifecycle_change_events"] = int(
        values.get("change_events", values.get("changed", 0))
    )
    result["setup_lifecycle_canonical_snapshots"] = int(
        values.get(
            "canonical_snapshots",
            values.get("canonical", result["setup_lifecycle_canonical_snapshots"]),
        )
    )
    result["setup_lifecycle_transitions"] = int(
        values.get("lifecycle_transitions", values.get("transitioned", 0))
    )
    result["setup_lifecycle_alerts"] = int(values.get("alerts", values.get("alerted", 0)))
    result["setup_lifecycle_active_episodes"] = int(values.get("active_episodes", 0))
    result["setup_lifecycle_low_confidence"] += int(values.get("low_confidence", 0))
    result["setup_lifecycle_failed"] += int(values.get("failed", 0))


def _utcnow() -> datetime:
    return datetime.now(UTC)
