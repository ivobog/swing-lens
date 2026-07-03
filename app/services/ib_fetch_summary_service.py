from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.tables import IBFetchItem, IBFetchRun
from app.services.bar_cache_service import BarFetchSummary


def create_ib_fetch_run(
    db: Session,
    run_id: int | None,
    tickers: list[str],
    include_benchmarks: bool,
    symbols_including_benchmarks: list[str] | None = None,
    planned_request_count: int = 0,
    force_refresh: bool = False,
    force_full_backfill: bool = False,
) -> IBFetchRun:
    fetch_run = IBFetchRun(
        run_id=run_id,
        requested_tickers=tickers,
        symbols_including_benchmarks=symbols_including_benchmarks or tickers,
        include_benchmarks=include_benchmarks,
        force_refresh=force_refresh,
        force_full_backfill=force_full_backfill,
        planned_request_count=planned_request_count,
        status="STARTED",
    )
    db.add(fetch_run)
    db.flush()
    return fetch_run


def complete_ib_fetch_run(
    db: Session,
    fetch_run: IBFetchRun,
    summary: BarFetchSummary,
) -> IBFetchRun:
    failure_count = len(summary.failures)
    skipped_count = sum(item.status == "SKIPPED" for item in summary.items)
    success_count = sum(item.status == "COMPLETED" for item in summary.items)
    status = "COMPLETED"
    if failure_count and failure_count < len(summary.items):
        status = "COMPLETED_WITH_WARNINGS"
    elif failure_count:
        status = "FAILED"

    fetch_run.status = status
    fetch_run.completed_at = datetime.now(UTC)
    fetch_run.planned_request_count = fetch_run.planned_request_count or len(summary.items)
    fetch_run.executed_request_count = success_count + failure_count
    fetch_run.skipped_count = skipped_count
    fetch_run.success_count = success_count
    fetch_run.fetched_count = summary.fetched
    fetch_run.inserted_count = summary.inserted
    fetch_run.updated_count = summary.updated
    fetch_run.revised_count = summary.revised
    fetch_run.unchanged_count = summary.unchanged
    fetch_run.failure_count = failure_count
    fetch_run.message = _summary_message(summary)

    fetch_run.items = [
        IBFetchItem(
            ticker=item.ticker,
            what_to_show=item.what_to_show,
            action="FETCH",
            bar_size="1 day",
            status=item.status,
            current_bar_count=0,
            fetched=item.fetched,
            inserted=item.inserted,
            updated=item.updated,
            revised=item.revised,
            unchanged=item.unchanged,
            attempt_count=1 if item.status in {"COMPLETED", "FAILED"} else 0,
            started_at=fetch_run.started_at,
            completed_at=fetch_run.completed_at,
            error_message=item.error_message,
        )
        for item in summary.items
    ]
    db.flush()
    return fetch_run


def fail_ib_fetch_run(
    db: Session,
    fetch_run: IBFetchRun,
    message: str,
) -> IBFetchRun:
    fetch_run.status = "FAILED"
    fetch_run.completed_at = datetime.now(UTC)
    fetch_run.failure_count = max(fetch_run.failure_count or 0, 1)
    fetch_run.executed_request_count = max(fetch_run.executed_request_count or 0, 1)
    fetch_run.message = _safe_message(message)
    db.flush()
    return fetch_run


def latest_ib_fetch_for_run(db: Session, run_id: int) -> IBFetchRun | None:
    return db.scalar(
        select(IBFetchRun)
        .where(IBFetchRun.run_id == run_id)
        .options(selectinload(IBFetchRun.items))
        .order_by(IBFetchRun.started_at.desc(), IBFetchRun.id.desc())
        .limit(1)
    )


def _summary_message(summary: BarFetchSummary) -> str:
    if not summary.items:
        return "No IB fetch items were returned."
    if summary.failures:
        return f"Fetched {summary.fetched} bars with {len(summary.failures)} failures."
    return f"Fetched {summary.fetched} bars successfully."


def _safe_message(message: str) -> str:
    return message.replace("\n", " ").strip()[:500]
