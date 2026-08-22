from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.tables import BackgroundJob, IBFetchRun
from app.services.background_job_service import (
    active_job_for_request_key,
    enqueue_job,
    is_cancel_requested,
    record_job_progress,
    request_job_cancel,
)
from app.services.ib_fetch_executor import execute_fetch_plan
from app.services.ib_fetch_plan_service import FetchPlan, build_fetch_plan
from app.services.operational_metrics import operational_metrics
from app.services.process_memory import (
    WorkerMemoryCritical,
    memory_status,
    process_memory_snapshot,
    runtime_memory_diagnostics,
)
from app.settings import get_settings

FETCH_TERMINAL_STATUSES = {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED"}
ITEM_TERMINAL_STATUSES = {"SUCCESS", "FAILED", "SKIPPED"}
IB_FETCH_JOB_TYPE = "IB_FETCH"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FetchJobOptions:
    include_benchmarks: bool = True
    force_refresh: bool = False
    force_full_backfill: bool = False


def create_queued_fetch_run(
    db: Session,
    plan: FetchPlan,
    options: FetchJobOptions,
) -> IBFetchRun:
    fetch_run = IBFetchRun(
        run_id=plan.run_id,
        requested_tickers=plan.requested_tickers,
        symbols_including_benchmarks=plan.symbols_including_benchmarks,
        include_benchmarks=options.include_benchmarks,
        force_refresh=options.force_refresh,
        force_full_backfill=options.force_full_backfill,
        decision_counts_json=plan.decision_counts,
        planned_request_count=plan.estimated_request_count,
        status="QUEUED",
        message="IB fetch is queued.",
    )
    db.add(fetch_run)
    db.flush()
    return fetch_run


def submit_fetch_job(
    db: Session,
    fetch_run_id: int,
    plan: FetchPlan,
    options: FetchJobOptions,
) -> BackgroundJob:
    """Queue an IB fetch durably; the web process never executes broker work."""
    what_to_show_values = sorted({item.what_to_show for item in plan.items})
    return enqueue_job(
        db,
        IB_FETCH_JOB_TYPE,
        {
            "fetch_run_id": fetch_run_id,
            "run_id": plan.run_id,
            "tickers": plan.requested_tickers,
            "include_benchmarks": options.include_benchmarks,
            "force_refresh": options.force_refresh,
            "force_full_backfill": options.force_full_backfill,
            "what_to_show_values": what_to_show_values,
        },
        related_run_id=plan.run_id,
        request_key=_request_key(fetch_run_id),
        max_retries=3,
    )


def cancel_fetch_job(db: Session, fetch_run_id: int) -> dict[str, Any]:
    fetch_run = _load_fetch_run(db, fetch_run_id)
    if fetch_run is None:
        raise ValueError(f"IB fetch run {fetch_run_id} was not found.")

    if fetch_run.status in FETCH_TERMINAL_STATUSES:
        return fetch_progress(fetch_run)

    job = active_job_for_request_key(db, IB_FETCH_JOB_TYPE, _request_key(fetch_run_id))
    if job is not None:
        request_job_cancel(db, job.id)
    if fetch_run.status == "QUEUED":
        fetch_run.status = "CANCELLED"
        fetch_run.message = "IB fetch was cancelled before it started."
    else:
        fetch_run.message = "Cancellation requested; the current IB request will finish first."
    db.flush()
    return fetch_progress(fetch_run, cancel_requested=job is not None)


def resume_fetch_job(
    db: Session,
    fetch_run_id: int,
) -> tuple[IBFetchRun, FetchPlan, FetchJobOptions]:
    previous = _load_fetch_run(db, fetch_run_id)
    if previous is None:
        raise ValueError(f"IB fetch run {fetch_run_id} was not found.")
    if previous.status not in {"FAILED", "PARTIAL", "CANCELLED"}:
        raise ValueError(f"IB fetch run {fetch_run_id} cannot be resumed from {previous.status}.")

    failed_items = [item for item in previous.items if item.status == "FAILED"]
    tickers = _unique_tickers([item.ticker for item in failed_items]) or previous.requested_tickers
    what_to_show_values = _unique_values([item.what_to_show for item in failed_items])
    resume_what_to_show = (
        tuple(what_to_show_values) if what_to_show_values else ("ADJUSTED_LAST", "TRADES")
    )
    plan = build_fetch_plan(
        db=db,
        tickers=tickers,
        run_id=previous.run_id,
        include_benchmarks=False,
        force_refresh=previous.force_refresh,
        force_full_backfill=previous.force_full_backfill,
        what_to_show_values=resume_what_to_show,
        retry_failed_contracts=True,
    )
    options = FetchJobOptions(
        include_benchmarks=False,
        force_refresh=previous.force_refresh,
        force_full_backfill=previous.force_full_backfill,
    )
    fetch_run = create_queued_fetch_run(db, plan, options)
    return fetch_run, plan, options


def get_fetch_progress(db: Session, fetch_run_id: int) -> dict[str, Any]:
    fetch_run = _load_fetch_run(db, fetch_run_id)
    if fetch_run is None:
        raise ValueError(f"IB fetch run {fetch_run_id} was not found.")
    job = active_job_for_request_key(db, IB_FETCH_JOB_TYPE, _request_key(fetch_run_id))
    return fetch_progress(fetch_run, cancel_requested=bool(job and job.requested_cancel))


def fetch_progress(fetch_run: IBFetchRun, cancel_requested: bool = False) -> dict[str, Any]:
    items = list(fetch_run.items or [])
    terminal_items = [item for item in items if item.status in ITEM_TERMINAL_STATUSES]
    running_item = next(
        (item for item in items if item.status in {"PLANNED", "RUNNING"}),
        None,
    )
    total_items = len(items) or fetch_run.planned_request_count or 0
    completed_items = len(terminal_items)
    percentage = round((completed_items / total_items) * 100, 1) if total_items else 0.0

    return {
        "fetch_run_id": fetch_run.id,
        "run_id": fetch_run.run_id,
        "status": fetch_run.status,
        "message": fetch_run.message,
        "cancel_requested": cancel_requested,
        "started_at": fetch_run.started_at,
        "completed_at": fetch_run.completed_at,
        "current_ticker": running_item.ticker if running_item else None,
        "percentage": min(percentage, 100.0),
        "completed_items": completed_items,
        "total_items": total_items,
        "planned_request_count": fetch_run.planned_request_count,
        "decision_counts": getattr(fetch_run, "decision_counts_json", {}),
        "executed_request_count": fetch_run.executed_request_count,
        "success_count": fetch_run.success_count,
        "failure_count": fetch_run.failure_count,
        "skipped_count": fetch_run.skipped_count,
        "fetched_count": fetch_run.fetched_count,
        "inserted_count": fetch_run.inserted_count,
        "updated_count": fetch_run.updated_count,
        "revised_count": fetch_run.revised_count,
        "unchanged_count": fetch_run.unchanged_count,
        "tickers": _ticker_progress(items),
        "items": [
            {
                "ticker": item.ticker,
                "what_to_show": item.what_to_show,
                "status": item.status,
                "action": item.action,
                "fetched": item.fetched,
                "inserted": item.inserted,
                "updated": item.updated,
                "revised": item.revised,
                "unchanged": item.unchanged,
                "attempt_count": item.attempt_count,
                "error_message": item.error_message,
                "decision_metadata": getattr(item, "decision_metadata_json", {}),
            }
            for item in sorted(items, key=lambda item: (item.ticker, item.what_to_show))
        ],
    }


def execute_durable_fetch_job(db: Session, job: BackgroundJob) -> dict[str, Any]:
    payload = job.payload_json or {}
    fetch_run_id = int(payload["fetch_run_id"])
    plan = build_fetch_plan(
        db=db,
        tickers=[str(value) for value in payload.get("tickers", [])],
        run_id=payload.get("run_id"),
        include_benchmarks=bool(payload.get("include_benchmarks", True)),
        force_refresh=bool(payload.get("force_refresh", False)),
        force_full_backfill=bool(payload.get("force_full_backfill", False)),
        what_to_show_values=tuple(
            payload.get("what_to_show_values") or ("ADJUSTED_LAST", "TRADES")
        ),
    )
    execution_token = str(job.execution_token or "")

    def should_cancel() -> bool:
        heartbeat = getattr(job, "_heartbeat", None)
        if callable(heartbeat):
            heartbeat()
        return is_cancel_requested(db, job.id)

    def progress_callback(progress_db: Session, **progress: Any) -> None:
        record_job_progress(
            progress_db,
            job_id=job.id,
            execution_token=execution_token,
            **progress,
        )

    settings = get_settings()

    def memory_probe(item_db: Session, item_index: int, total: int, ticker: str) -> None:
        if item_index % settings.worker_memory_profile_interval_items:
            return
        diagnostics = runtime_memory_diagnostics(
            item_db,
            top_allocation_count=settings.worker_memory_top_allocations,
        )
        state = memory_status(
            process_memory_snapshot(),
            warning_mb=settings.worker_memory_warning_mb,
            critical_mb=settings.worker_memory_critical_mb,
        )
        diagnostics.update(
            item_index=item_index,
            total_items=total,
            ticker=ticker,
            memory_status=state,
        )
        logger.info("job.memory_checkpoint %s", diagnostics, extra={"job_id": job.id})
        operational_metrics.set_gauge(
            "swinglens_worker_memory_bytes",
            float(diagnostics["private_bytes"] or diagnostics["rss_bytes"]),
            worker_id=str(job.worker_id or "unknown"),
        )
        if state == "CRITICAL":
            raise WorkerMemoryCritical(
                f"Worker memory reached the critical budget after {ticker}; checkpoint preserved."
            )

    fetch_run = execute_fetch_plan(
        db=db,
        plan=plan,
        include_benchmarks=bool(payload.get("include_benchmarks", True)),
        force_refresh=bool(payload.get("force_refresh", False)),
        force_full_backfill=bool(payload.get("force_full_backfill", False)),
        fetch_run_id=fetch_run_id,
        should_cancel=should_cancel,
        progress_callback=progress_callback,
        execution_token=execution_token,
        memory_probe=memory_probe,
    )
    return fetch_progress(fetch_run)


def _request_key(fetch_run_id: int) -> str:
    return f"ib-fetch:{fetch_run_id}"


def _load_fetch_run(db: Session, fetch_run_id: int) -> IBFetchRun | None:
    return db.scalar(
        select(IBFetchRun)
        .where(IBFetchRun.id == fetch_run_id)
        .options(selectinload(IBFetchRun.items))
    )


def _ticker_progress(items: list[Any]) -> list[dict[str, Any]]:
    by_ticker: dict[str, list[Any]] = defaultdict(list)
    for item in items:
        by_ticker[item.ticker].append(item)
    return [
        {
            "ticker": ticker,
            "completed_items": sum(item.status in ITEM_TERMINAL_STATUSES for item in rows),
            "total_items": len(rows),
            "success_count": sum(item.status == "SUCCESS" for item in rows),
            "failure_count": sum(item.status == "FAILED" for item in rows),
            "skipped_count": sum(item.status == "SKIPPED" for item in rows),
        }
        for ticker, rows in sorted(by_ticker.items())
    ]


def _unique_tickers(values: list[str]) -> list[str]:
    return _unique_values([value.upper() for value in values])


def _unique_values(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
