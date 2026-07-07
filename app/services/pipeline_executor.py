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
from app.services.bar_cache_service import DEFAULT_WHAT_TO_SHOW
from app.services.combined_decision import refresh_combined_results
from app.services.fundamental_score_service import recalculate_run_fundamentals
from app.services.ib_fetch_executor import execute_fetch_plan
from app.services.ib_fetch_plan_service import FetchPlan, build_fetch_plan
from app.services.pipeline_service import (
    PipelineStatus,
    PipelineStepStatus,
)
from app.services.technical_score_service import score_run_technicals


class PipelineCancelled(Exception):
    pass


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
    combined_results: int
    incomplete_rows: int
    warning_rows: int


@dataclass(frozen=True)
class PipelineExecutionDependencies:
    recalculate_fundamentals: Callable[[Session, int], list[Any]] = recalculate_run_fundamentals
    build_fetch_plan: Callable[..., FetchPlan] = build_fetch_plan
    execute_fetch_plan: Callable[..., IBFetchRun] = execute_fetch_plan
    score_technicals: Callable[[Session, int], list[Any]] = score_run_technicals
    refresh_combined: Callable[[Session, int], list[CombinedResult]] = refresh_combined_results


def execute_full_pipeline(
    db: Session,
    pipeline_run_id: int,
    should_cancel: Callable[[], bool] | None = None,
    dependencies: PipelineExecutionDependencies | None = None,
) -> PipelineExecutionResult:
    dependencies = dependencies or PipelineExecutionDependencies()
    pipeline = _require_pipeline(db, pipeline_run_id)
    upload_run = _require_upload_run(db, pipeline.upload_run_id)
    should_cancel = should_cancel or (lambda: False)

    result = _empty_result(pipeline, upload_run)
    try:
        _mark_pipeline_running(db, pipeline)
        _raise_if_cancelled(should_cancel)

        with _pipeline_step(db, pipeline, "VALIDATING_RUN"):
            tickers = _tickers_for_run(db, upload_run.id)
            if not tickers:
                raise ValueError("No uploaded tickers are available for this run.")
            result["uploaded_rows"] = upload_run.row_count or len(tickers)

        _raise_if_cancelled(should_cancel)
        with _pipeline_step(db, pipeline, "SCORING_FUNDAMENTALS"):
            fundamental_scores = dependencies.recalculate_fundamentals(db, upload_run.id)
            result["fundamental_scores"] = len(fundamental_scores)

        _raise_if_cancelled(should_cancel)
        with _pipeline_step(db, pipeline, "FETCHING_MARKET_DATA"):
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
        with _pipeline_step(db, pipeline, "SCORING_TECHNICALS"):
            technical_scores = dependencies.score_technicals(db, upload_run.id)
            result["technical_scores"] = len(technical_scores)
            result["technical_error_count"] = _technical_error_count(technical_scores)

        _raise_if_cancelled(should_cancel)
        with _pipeline_step(db, pipeline, "COMBINING_RESULTS"):
            combined_results = dependencies.refresh_combined(db, upload_run.id)
            result["combined_results"] = len(combined_results)
            result["incomplete_rows"] = sum(not row.is_complete for row in combined_results)
            result["warning_rows"] = sum(row.has_warning for row in combined_results)

        final_status = _final_pipeline_status(result)
        _mark_pipeline_finished(db, pipeline, final_status, result)
        return _to_execution_result(pipeline, result)
    except PipelineCancelled:
        _mark_pipeline_cancelled(db, pipeline)
        raise
    except Exception as exc:
        _mark_pipeline_failed(db, pipeline, exc)
        raise


@contextmanager
def _pipeline_step(db: Session, pipeline: PipelineRun, step_name: str):
    step = _require_step(db, pipeline.id, step_name)
    pipeline.current_step = step_name
    pipeline.status = _pipeline_status_for_step(step_name)
    step.status = PipelineStepStatus.RUNNING
    step.started_at = step.started_at or _utcnow()
    step.error_message = None
    _save_progress(db)
    try:
        yield step
    except PipelineCancelled:
        step.status = PipelineStepStatus.CANCELLED
        step.completed_at = _utcnow()
        step.error_message = "Pipeline cancellation requested."
        _save_progress(db)
        raise
    except Exception as exc:
        step.status = PipelineStepStatus.FAILED
        step.completed_at = _utcnow()
        step.error_message = _safe_message(str(exc))
        _save_progress(db)
        raise
    else:
        step.status = PipelineStepStatus.COMPLETED
        step.completed_at = _utcnow()
        _save_progress(db)


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


def _mark_pipeline_running(db: Session, pipeline: PipelineRun) -> None:
    pipeline.status = PipelineStatus.RUNNING
    pipeline.started_at = pipeline.started_at or _utcnow()
    pipeline.completed_at = None
    pipeline.error_message = None
    pipeline.message = "Full pipeline is running."
    _save_progress(db)


def _mark_pipeline_finished(
    db: Session,
    pipeline: PipelineRun,
    status: str,
    result: dict[str, int],
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
    _save_progress(db)


def _mark_pipeline_cancelled(db: Session, pipeline: PipelineRun) -> None:
    pipeline.status = PipelineStatus.CANCELLED
    pipeline.completed_at = _utcnow()
    pipeline.message = "Pipeline was cancelled."
    pipeline.error_message = None
    _cancel_unfinished_steps(db, pipeline.id)
    _save_progress(db)


def _mark_pipeline_failed(db: Session, pipeline: PipelineRun, exc: Exception) -> None:
    pipeline.status = PipelineStatus.FAILED
    pipeline.completed_at = _utcnow()
    pipeline.message = "Pipeline failed."
    pipeline.error_message = _safe_message(str(exc))
    _save_progress(db)


def _cancel_unfinished_steps(db: Session, pipeline_run_id: int) -> None:
    for step in db.scalars(
        select(PipelineStep).where(PipelineStep.pipeline_run_id == pipeline_run_id)
    ):
        if step.status in {PipelineStepStatus.PENDING, PipelineStepStatus.RUNNING}:
            step.status = PipelineStepStatus.CANCELLED
            step.completed_at = _utcnow()


def _save_progress(db: Session) -> None:
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
        PipelineStatus.COMBINING_RESULTS,
    }:
        return step_name
    return PipelineStatus.RUNNING


def _apply_fetch_result(result: dict[str, int], fetch_run: IBFetchRun) -> None:
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


def _final_pipeline_status(result: dict[str, int]) -> str:
    if result["combined_results"] <= 0:
        return PipelineStatus.FAILED
    if (
        result["incomplete_rows"]
        or result["warning_rows"]
        or result["ib_failure_count"]
        or result["technical_error_count"]
        or result["fetch_failed"]
    ):
        return PipelineStatus.PARTIAL
    return PipelineStatus.COMPLETED


def _completion_message(status: str, result: dict[str, int]) -> str:
    if status == PipelineStatus.COMPLETED:
        return f"Pipeline completed with {result['combined_results']} combined rows."
    if status == PipelineStatus.PARTIAL:
        return (
            f"Pipeline completed partially with {result['combined_results']} combined rows, "
            f"{result['incomplete_rows']} incomplete rows, and "
            f"{result['ib_failure_count']} IB failures."
        )
    return "Pipeline failed before combined results were produced."


def _empty_result(pipeline: PipelineRun, upload_run: UploadRun) -> dict[str, int]:
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
        "combined_results": 0,
        "incomplete_rows": 0,
        "warning_rows": 0,
        "fetch_failed": 0,
    }


def _to_execution_result(
    pipeline: PipelineRun,
    result: dict[str, int],
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
        combined_results=result["combined_results"],
        incomplete_rows=result["incomplete_rows"],
        warning_rows=result["warning_rows"],
    )


def _public_result(result: dict[str, int]) -> dict[str, int]:
    return {
        key: value
        for key, value in result.items()
        if key not in {"fetch_failed", "technical_error_count"}
    }


def _safe_message(message: str) -> str:
    return message.replace("\n", " ").strip()[:500]


def _utcnow() -> datetime:
    return datetime.now(UTC)
