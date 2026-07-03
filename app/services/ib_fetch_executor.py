from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.tables import IBFetchItem, IBFetchRun
from app.services.bar_cache_service import cache_bars
from app.services.ib_api import IB
from app.services.ib_connection import create_ib_client
from app.services.ib_contract_resolver import resolve_us_stock_contract
from app.services.ib_data_fetcher import fetch_daily_bars
from app.services.ib_fetch_plan_service import FetchAction, FetchPlan, FetchPlanItem
from app.services.ib_rate_limiter import (
    IbHistoricalRateLimiter,
    rate_limit_config_from_settings,
)
from app.settings import Settings, get_settings

NON_FETCH_ACTIONS = {
    FetchAction.SKIP,
    FetchAction.UNSUPPORTED,
    FetchAction.FAILED,
}


def execute_fetch_plan(
    db: Session,
    plan: FetchPlan,
    ib_client_factory: Callable[[], IB] | None = None,
    rate_limiter: IbHistoricalRateLimiter | None = None,
    settings: Settings | None = None,
    include_benchmarks: bool = True,
    force_refresh: bool = False,
    force_full_backfill: bool = False,
) -> IBFetchRun:
    settings = settings or get_settings()
    rate_limiter = rate_limiter or IbHistoricalRateLimiter(
        rate_limit_config_from_settings(settings)
    )
    fetch_run = _create_fetch_run(
        db=db,
        plan=plan,
        include_benchmarks=include_benchmarks,
        force_refresh=force_refresh,
        force_full_backfill=force_full_backfill,
    )
    ib = ib_client_factory() if ib_client_factory else create_ib_client()

    try:
        ib.connect(
            settings.ib_host,
            settings.ib_port,
            clientId=settings.ib_client_id,
            timeout=settings.ib_timeout_seconds,
            readonly=True,
        )
        for plan_item in plan.items:
            fetch_item = _create_fetch_item(fetch_run, plan_item)
            db.add(fetch_item)
            db.flush()
            _execute_plan_item(
                db=db,
                ib=ib,
                fetch_item=fetch_item,
                plan_item=plan_item,
                rate_limiter=rate_limiter,
                settings=settings,
            )
            _refresh_run_totals(fetch_run)
            db.commit()
    except Exception as exc:
        fetch_run.status = "FAILED"
        fetch_run.completed_at = datetime.now(UTC)
        fetch_run.failure_count = max(fetch_run.failure_count or 0, 1)
        fetch_run.message = _safe_message(str(exc))
        db.flush()
        db.commit()
    finally:
        if ib.isConnected():
            ib.disconnect()

    if fetch_run.status == "RUNNING":
        fetch_run.completed_at = datetime.now(UTC)
        _refresh_run_totals(fetch_run)
        fetch_run.status = _final_run_status(fetch_run)
        fetch_run.message = _run_message(fetch_run)
        db.flush()
        db.commit()

    return fetch_run


def _create_fetch_run(
    db: Session,
    plan: FetchPlan,
    include_benchmarks: bool,
    force_refresh: bool,
    force_full_backfill: bool,
) -> IBFetchRun:
    fetch_run = IBFetchRun(
        run_id=plan.run_id,
        requested_tickers=plan.requested_tickers,
        symbols_including_benchmarks=plan.symbols_including_benchmarks,
        include_benchmarks=include_benchmarks,
        force_refresh=force_refresh,
        force_full_backfill=force_full_backfill,
        planned_request_count=plan.estimated_request_count,
        status="RUNNING",
    )
    db.add(fetch_run)
    db.flush()
    return fetch_run


def _create_fetch_item(fetch_run: IBFetchRun, plan_item: FetchPlanItem) -> IBFetchItem:
    return IBFetchItem(
        fetch_run=fetch_run,
        ticker=plan_item.ticker,
        what_to_show=plan_item.what_to_show,
        action=plan_item.action.value,
        duration=plan_item.duration,
        bar_size=plan_item.bar_size,
        status="PLANNED",
        reason=plan_item.reason,
        current_bar_count=plan_item.current_bar_count,
        fetched=0,
        inserted=0,
        updated=0,
        revised=0,
        unchanged=0,
        attempt_count=0,
    )


def _execute_plan_item(
    db: Session,
    ib: IB,
    fetch_item: IBFetchItem,
    plan_item: FetchPlanItem,
    rate_limiter: IbHistoricalRateLimiter,
    settings: Settings,
) -> None:
    fetch_item.started_at = datetime.now(UTC)

    if plan_item.action == FetchAction.SKIP:
        _mark_skipped(fetch_item, plan_item.reason)
        return
    if plan_item.action == FetchAction.UNSUPPORTED:
        _mark_failed(fetch_item, plan_item.reason)
        return
    if plan_item.action == FetchAction.FAILED:
        _mark_failed(fetch_item, plan_item.reason)
        return

    resolution = resolve_us_stock_contract(db, plan_item.ticker, ib)
    if not resolution.contract:
        _mark_failed(fetch_item, resolution.error_message or "Contract resolution failed.")
        return

    action, duration, reason = _execution_action(plan_item, settings)
    fetch_item.action = action.value
    fetch_item.duration = duration
    fetch_item.reason = reason
    if action == FetchAction.SKIP:
        _mark_skipped(fetch_item, reason)
        return

    for attempt in range(1, settings.ib_max_retries + 1):
        fetch_item.attempt_count = attempt
        try:
            rate_limiter.wait_before_request()
            bars = fetch_daily_bars(
                ib,
                resolution.contract,
                plan_item.what_to_show,
                settings=settings,
                duration=duration,
                bar_size=plan_item.bar_size,
            )
            upsert = cache_bars(db, bars)
            fetch_item.fetched = len(bars)
            fetch_item.inserted = upsert.inserted
            fetch_item.updated = upsert.updated
            fetch_item.revised = upsert.revised
            fetch_item.unchanged = upsert.unchanged
            fetch_item.status = "SUCCESS"
            fetch_item.completed_at = datetime.now(UTC)
            return
        except Exception as exc:
            fetch_item.error_message = _safe_message(str(exc))
            if attempt >= settings.ib_max_retries:
                _mark_failed(fetch_item, str(exc))
                return
            rate_limiter.backoff_after_error(exc, attempt)


def _execution_action(
    plan_item: FetchPlanItem,
    settings: Settings,
) -> tuple[FetchAction, str | None, str]:
    if plan_item.action in {
        FetchAction.FULL_BACKFILL,
        FetchAction.TOP_UP_RECENT,
        FetchAction.REFRESH_RECENT,
        FetchAction.FORCE_REFRESH,
    }:
        return plan_item.action, plan_item.duration, plan_item.reason

    if plan_item.current_bar_count == 0 or plan_item.current_bar_count < plan_item.required_bars:
        return (
            FetchAction.FULL_BACKFILL,
            settings.ib_full_backfill_duration,
            f"{plan_item.ticker} needs a full backfill after contract resolution.",
        )

    if not _latest_date_current(plan_item.latest_bar_date, settings.ib_daily_bar_stale_after_days):
        return (
            FetchAction.TOP_UP_RECENT,
            settings.ib_top_up_duration,
            f"{plan_item.ticker} needs a top-up after contract resolution.",
        )

    return (
        FetchAction.SKIP,
        None,
        f"{plan_item.ticker} has current cached bars after contract resolution.",
    )


def _latest_date_current(latest: date | None, stale_after_days: int) -> bool:
    if latest is None:
        return False
    return latest >= date.today() - timedelta(days=stale_after_days)


def _mark_skipped(fetch_item: IBFetchItem, reason: str) -> None:
    fetch_item.status = "SKIPPED"
    fetch_item.reason = reason
    fetch_item.completed_at = datetime.now(UTC)


def _mark_failed(fetch_item: IBFetchItem, message: str) -> None:
    fetch_item.status = "FAILED"
    fetch_item.error_message = _safe_message(message)
    fetch_item.completed_at = datetime.now(UTC)


def _refresh_run_totals(fetch_run: IBFetchRun) -> None:
    items = fetch_run.items or []
    fetch_run.executed_request_count = sum(item.status in {"SUCCESS", "FAILED"} for item in items)
    fetch_run.skipped_count = sum(item.status == "SKIPPED" for item in items)
    fetch_run.success_count = sum(item.status == "SUCCESS" for item in items)
    fetch_run.failure_count = sum(item.status == "FAILED" for item in items)
    fetch_run.fetched_count = sum(item.fetched or 0 for item in items)
    fetch_run.inserted_count = sum(item.inserted or 0 for item in items)
    fetch_run.updated_count = sum(item.updated or 0 for item in items)
    fetch_run.revised_count = sum(item.revised or 0 for item in items)
    fetch_run.unchanged_count = sum(item.unchanged or 0 for item in items)


def _final_run_status(fetch_run: IBFetchRun) -> str:
    if fetch_run.failure_count and fetch_run.success_count:
        return "COMPLETED_WITH_WARNINGS"
    if fetch_run.failure_count and not fetch_run.success_count:
        return "FAILED"
    return "COMPLETED"


def _run_message(fetch_run: IBFetchRun) -> str:
    if fetch_run.failure_count:
        return (
            f"Executed {fetch_run.executed_request_count} IB requests with "
            f"{fetch_run.failure_count} failures."
        )
    return (
        f"Executed {fetch_run.executed_request_count} IB requests; "
        f"skipped {fetch_run.skipped_count} items."
    )


def _safe_message(message: str) -> str:
    return message.replace("\n", " ").strip()[:500]
