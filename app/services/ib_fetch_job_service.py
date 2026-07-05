from __future__ import annotations

from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from threading import Event, Lock
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db import SessionLocal
from app.models.tables import IBFetchRun
from app.services.ib_fetch_executor import execute_fetch_plan
from app.services.ib_fetch_plan_service import FetchPlan, build_fetch_plan

FETCH_TERMINAL_STATUSES = {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED"}
ITEM_TERMINAL_STATUSES = {"SUCCESS", "FAILED", "SKIPPED"}


@dataclass(frozen=True)
class FetchJobOptions:
    include_benchmarks: bool = True
    force_refresh: bool = False
    force_full_backfill: bool = False


_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ib-fetch")
_lock = Lock()
_cancel_events: dict[int, Event] = {}
_futures: dict[int, Future] = {}


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
        planned_request_count=plan.estimated_request_count,
        status="QUEUED",
        message="IB fetch is queued.",
    )
    db.add(fetch_run)
    db.flush()
    return fetch_run


def submit_fetch_job(
    fetch_run_id: int,
    plan: FetchPlan,
    options: FetchJobOptions,
) -> None:
    cancel_event = Event()
    with _lock:
        _cancel_events[fetch_run_id] = cancel_event
        future = _executor.submit(_run_fetch_job, fetch_run_id, plan, options, cancel_event)
        _futures[fetch_run_id] = future
        future.add_done_callback(lambda _future: _forget_job(fetch_run_id))


def cancel_fetch_job(db: Session, fetch_run_id: int) -> dict[str, Any]:
    fetch_run = _load_fetch_run(db, fetch_run_id)
    if fetch_run is None:
        raise ValueError(f"IB fetch run {fetch_run_id} was not found.")

    with _lock:
        cancel_event = _cancel_events.get(fetch_run_id)
        future = _futures.get(fetch_run_id)
        if cancel_event:
            cancel_event.set()
        cancelled_before_start = bool(future and future.cancel())
        future_exists = future is not None

    if fetch_run.status in FETCH_TERMINAL_STATUSES:
        return fetch_progress(fetch_run)

    if cancelled_before_start or (fetch_run.status == "QUEUED" and not future_exists):
        fetch_run.status = "CANCELLED"
        fetch_run.message = "IB fetch was cancelled before it started."
    else:
        fetch_run.message = "Cancellation requested; the current IB request will finish first."
    db.flush()
    return fetch_progress(fetch_run, cancel_requested=True)


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
    return fetch_progress(fetch_run, cancel_requested=_is_cancel_requested(fetch_run_id))


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
            }
            for item in sorted(items, key=lambda item: (item.ticker, item.what_to_show))
        ],
    }


def _run_fetch_job(
    fetch_run_id: int,
    plan: FetchPlan,
    options: FetchJobOptions,
    cancel_event: Event,
) -> None:
    db = SessionLocal()
    try:
        execute_fetch_plan(
            db=db,
            plan=plan,
            include_benchmarks=options.include_benchmarks,
            force_refresh=options.force_refresh,
            force_full_backfill=options.force_full_backfill,
            fetch_run_id=fetch_run_id,
            should_cancel=cancel_event.is_set,
        )
    finally:
        db.close()


def _forget_job(fetch_run_id: int) -> None:
    with _lock:
        _cancel_events.pop(fetch_run_id, None)
        _futures.pop(fetch_run_id, None)


def _is_cancel_requested(fetch_run_id: int) -> bool:
    with _lock:
        event = _cancel_events.get(fetch_run_id)
        return bool(event and event.is_set())


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
