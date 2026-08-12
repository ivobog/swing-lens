from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from time import perf_counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import IBFetchItem, IBFetchRun
from app.services.bar_cache_service import cache_bars
from app.services.ib_api import IB
from app.services.ib_connection import create_ib_client
from app.services.ib_contract_resolver import resolve_us_stock_contract
from app.services.ib_data_fetcher import fetch_daily_bars
from app.services.ib_fetch_plan_service import (
    FetchAction,
    FetchPlan,
    FetchPlanItem,
    _decision_category,
    _incremental_request_window,
    _plan_action,
)
from app.services.ib_rate_limiter import (
    IbHistoricalRateLimiter,
    rate_limit_config_from_settings,
)
from app.services.operational_metrics import operational_metrics
from app.services.us_market_calendar import is_latest_daily_bar_current
from app.settings import Settings, get_settings

NON_FETCH_ACTIONS = {
    FetchAction.SKIP,
    FetchAction.UNSUPPORTED,
    FetchAction.FAILED,
}


@dataclass(frozen=True)
class TickerReadyEvent:
    ticker: str
    statuses: tuple[str, ...]
    failed: bool
    completed_at: datetime


def execute_fetch_plan(
    db: Session,
    plan: FetchPlan,
    ib_client_factory: Callable[[], IB] | None = None,
    rate_limiter: IbHistoricalRateLimiter | None = None,
    settings: Settings | None = None,
    include_benchmarks: bool = True,
    force_refresh: bool = False,
    force_full_backfill: bool = False,
    fetch_run_id: int | None = None,
    should_cancel: Callable[[], bool] | None = None,
    on_ticker_ready: Callable[[TickerReadyEvent], None] | None = None,
) -> IBFetchRun:
    settings = settings or get_settings()
    rate_limiter = rate_limiter or IbHistoricalRateLimiter(
        rate_limit_config_from_settings(settings)
    )
    fetch_run = _start_fetch_run(
        db=db,
        plan=plan,
        include_benchmarks=include_benchmarks,
        force_refresh=force_refresh,
        force_full_backfill=force_full_backfill,
        fetch_run_id=fetch_run_id,
    )
    for decision, count in plan.decision_counts.items():
        operational_metrics.increment(
            "swinglens_ib_fetch_decisions_total",
            value=count,
            decision=decision,
        )
    ib = ib_client_factory() if ib_client_factory else create_ib_client()
    completed_by_ticker: dict[str, list[IBFetchItem]] = defaultdict(list)
    expected_by_ticker: dict[str, int] = defaultdict(int)
    notified_tickers: set[str] = set()
    performance: dict[str, float] = {
        "ib_pacing_wait_ms": 0.0,
        "ib_network_ms": 0.0,
        "bar_cache_write_ms": 0.0,
    }
    for plan_item in plan.items:
        expected_by_ticker[plan_item.ticker.upper()] += 1
    execution_items = _benchmark_first_items(plan.items, settings.ib_benchmark_symbols)

    try:
        if hasattr(ib, "RequestTimeout"):
            ib.RequestTimeout = settings.ib_timeout_seconds
        ib.connect(
            settings.ib_host,
            settings.ib_port,
            clientId=settings.ib_client_id,
            timeout=settings.ib_timeout_seconds,
            readonly=True,
        )
        for plan_item in execution_items:
            if should_cancel and should_cancel():
                _mark_run_cancelled(fetch_run)
                db.flush()
                db.commit()
                break
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
                force_refresh=force_refresh,
                force_full_backfill=force_full_backfill,
                performance=performance,
                should_cancel=should_cancel,
            )
            cancel_after_item = bool(should_cancel and should_cancel())
            _refresh_run_totals(fetch_run)
            if cancel_after_item:
                _mark_run_cancelled(fetch_run)
            db.commit()
            _record_ticker_completion(
                fetch_item=fetch_item,
                completed_by_ticker=completed_by_ticker,
                expected_by_ticker=expected_by_ticker,
                notified_tickers=notified_tickers,
                on_ticker_ready=on_ticker_ready,
            )
            if cancel_after_item:
                break
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

    fetch_run._performance = {key: round(value, 3) for key, value in performance.items()}
    for name, value in fetch_run._performance.items():
        operational_metrics.increment(f"swinglens_{name}_total", value=value)
    return fetch_run


def _start_fetch_run(
    db: Session,
    plan: FetchPlan,
    include_benchmarks: bool,
    force_refresh: bool,
    force_full_backfill: bool,
    fetch_run_id: int | None,
) -> IBFetchRun:
    if fetch_run_id is not None:
        fetch_run = db.scalar(select(IBFetchRun).where(IBFetchRun.id == fetch_run_id))
        if fetch_run is None:
            raise ValueError(f"IB fetch run {fetch_run_id} was not found.")
        fetch_run.run_id = plan.run_id
        fetch_run.requested_tickers = plan.requested_tickers
        fetch_run.symbols_including_benchmarks = plan.symbols_including_benchmarks
        fetch_run.include_benchmarks = include_benchmarks
        fetch_run.force_refresh = force_refresh
        fetch_run.force_full_backfill = force_full_backfill
        fetch_run.decision_counts_json = plan.decision_counts
        fetch_run.planned_request_count = plan.estimated_request_count
        fetch_run.status = "RUNNING"
        fetch_run.message = None
        db.flush()
        return fetch_run

    fetch_run = IBFetchRun(
        run_id=plan.run_id,
        requested_tickers=plan.requested_tickers,
        symbols_including_benchmarks=plan.symbols_including_benchmarks,
        include_benchmarks=include_benchmarks,
        force_refresh=force_refresh,
        force_full_backfill=force_full_backfill,
        decision_counts_json=plan.decision_counts,
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
        decision_metadata_json=_decision_metadata(plan_item),
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
    force_refresh: bool,
    force_full_backfill: bool,
    performance: dict[str, float] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> None:
    performance = performance if performance is not None else {}
    fetch_item.started_at = datetime.now(UTC)
    fetch_item.status = "RUNNING"
    db.flush()
    db.commit()

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

    action, duration, reason = _execution_action(
        plan_item,
        settings,
        force_refresh=force_refresh,
        force_full_backfill=force_full_backfill,
    )
    fetch_item.action = action.value
    fetch_item.duration = duration
    fetch_item.reason = reason
    fetch_item.decision_metadata_json = _decision_metadata(
        plan_item,
        action=action,
        duration=duration,
    )
    if action == FetchAction.SKIP:
        _mark_skipped(fetch_item, reason)
        return
    if action in {FetchAction.UNSUPPORTED, FetchAction.FAILED}:
        _mark_failed(fetch_item, reason)
        return

    for attempt in range(1, settings.ib_max_retries + 1):
        fetch_item.attempt_count = attempt
        try:
            pacing_started = perf_counter()
            if isinstance(rate_limiter, IbHistoricalRateLimiter):
                pacing_ready = rate_limiter.wait_before_request(should_cancel)
            else:
                rate_limiter.wait_before_request()
                pacing_ready = True
            _add_duration(performance, "ib_pacing_wait_ms", pacing_started)
            if not pacing_ready:
                _mark_skipped(fetch_item, "Cancellation requested during IB pacing wait.")
                return
            network_started = perf_counter()
            try:
                bars = fetch_daily_bars(
                    ib,
                    resolution.contract,
                    plan_item.what_to_show,
                    settings=settings,
                    duration=duration,
                    bar_size=plan_item.bar_size,
                )
            finally:
                _add_duration(performance, "ib_network_ms", network_started)
            cache_started = perf_counter()
            try:
                upsert = cache_bars(
                    db,
                    bars,
                    fetch_run_id=fetch_item.fetch_run_id
                    or getattr(fetch_item.fetch_run, "id", None),
                    fetch_item_id=fetch_item.id,
                )
            finally:
                _add_duration(performance, "bar_cache_write_ms", cache_started)
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
            if should_cancel and should_cancel():
                _mark_skipped(fetch_item, "Cancellation requested before IB retry.")
                return
            if isinstance(rate_limiter, IbHistoricalRateLimiter):
                backoff_completed = rate_limiter.backoff_after_error(
                    exc,
                    attempt,
                    should_cancel,
                )
            else:
                rate_limiter.backoff_after_error(exc, attempt)
                backoff_completed = True
            if not backoff_completed:
                _mark_skipped(fetch_item, "Cancellation requested during IB retry backoff.")
                return


def _add_duration(performance: dict[str, float], name: str, started_at: float) -> None:
    performance[name] = performance.get(name, 0.0) + max(0.0, (perf_counter() - started_at) * 1000)


def _execution_action(
    plan_item: FetchPlanItem,
    settings: Settings,
    *,
    force_refresh: bool,
    force_full_backfill: bool,
) -> tuple[FetchAction, str | None, str]:
    if plan_item.action in {
        FetchAction.FULL_BACKFILL,
        FetchAction.TOP_UP_RECENT,
        FetchAction.REFRESH_RECENT,
        FetchAction.FORCE_REFRESH,
    }:
        return plan_item.action, plan_item.duration, plan_item.reason

    latest_current = _latest_date_current(
        plan_item.latest_bar_date,
        settings.ib_daily_bar_stale_after_days,
    )
    top_up_duration, _ = _incremental_request_window(
        plan_item.latest_bar_date,
        plan_item.freshness_threshold_date or date.today(),
        revision_sessions=settings.ib_revision_window_sessions,
        fallback_duration=settings.ib_top_up_duration,
    )
    action, duration, reason = _plan_action(
        ticker=plan_item.ticker,
        contract_status="RESOLVED",
        what_to_show=plan_item.what_to_show,
        current_bar_count=plan_item.current_bar_count,
        required_bars=plan_item.required_bars,
        latest_current=latest_current,
        force_refresh=force_refresh,
        force_full_backfill=force_full_backfill,
        settings=settings,
        full_backfill_completed=plan_item.full_backfill_completed,
        top_up_duration=top_up_duration,
    )
    if plan_item.action == FetchAction.CONTRACT_RESOLUTION_REQUIRED:
        reason = f"{reason.rstrip('.')} after contract resolution."
    return action, duration, reason


def _latest_date_current(latest: date | None, stale_after_days: int) -> bool:
    _ = stale_after_days
    return is_latest_daily_bar_current(latest)


def _mark_skipped(fetch_item: IBFetchItem, reason: str) -> None:
    fetch_item.status = "SKIPPED"
    fetch_item.reason = reason
    fetch_item.completed_at = datetime.now(UTC)


def _mark_failed(fetch_item: IBFetchItem, message: str) -> None:
    fetch_item.status = "FAILED"
    fetch_item.error_message = _safe_message(message)
    fetch_item.completed_at = datetime.now(UTC)


def _mark_run_cancelled(fetch_run: IBFetchRun) -> None:
    fetch_run.status = "CANCELLED"
    fetch_run.completed_at = datetime.now(UTC)
    _refresh_run_totals(fetch_run)
    fetch_run.message = "IB fetch was cancelled."


def _refresh_run_totals(fetch_run: IBFetchRun) -> None:
    items = fetch_run.items or []
    fetch_run.executed_request_count = sum((item.attempt_count or 0) > 0 for item in items)
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
        return "PARTIAL"
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


def _decision_metadata(
    plan_item: FetchPlanItem,
    *,
    action: FetchAction | None = None,
    duration: str | None = None,
) -> dict[str, object]:
    effective_action = action or plan_item.action
    latest_current = _latest_date_current(plan_item.latest_bar_date, stale_after_days=0)
    return {
        "data_role": plan_item.data_role,
        "dependency_roles": list(plan_item.dependency_roles),
        "coverage_state": plan_item.coverage_state,
        "existing_coverage_reused": plan_item.existing_coverage_reused,
        "full_backfill_completed": plan_item.full_backfill_completed,
        "freshness_threshold_date": _iso_date(plan_item.freshness_threshold_date),
        "freshness_lag_sessions": plan_item.freshness_lag_sessions,
        "missing_start_date": _iso_date(plan_item.missing_start_date),
        "missing_end_date": _iso_date(plan_item.missing_end_date),
        "request_start_date": _iso_date(plan_item.request_start_date),
        "request_end_date": _iso_date(plan_item.request_end_date),
        "decision_category": _decision_category(
            effective_action,
            latest_current=latest_current,
        ),
        "action": effective_action.value,
        "duration": duration if action is not None else plan_item.duration,
        "bar_size": plan_item.bar_size,
    }


def _iso_date(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _benchmark_first_items(
    items: list[FetchPlanItem],
    benchmark_symbols: tuple[str, ...],
) -> list[FetchPlanItem]:
    benchmarks = {symbol.strip().upper() for symbol in benchmark_symbols if symbol.strip()}
    return sorted(
        items,
        key=lambda item: item.ticker.upper() not in benchmarks,
    )


def _record_ticker_completion(
    *,
    fetch_item: IBFetchItem,
    completed_by_ticker: dict[str, list[IBFetchItem]],
    expected_by_ticker: dict[str, int],
    notified_tickers: set[str],
    on_ticker_ready: Callable[[TickerReadyEvent], None] | None,
) -> None:
    if on_ticker_ready is None:
        return
    ticker = fetch_item.ticker.upper()
    completed_by_ticker[ticker].append(fetch_item)
    if ticker in notified_tickers:
        return
    if len(completed_by_ticker[ticker]) < expected_by_ticker[ticker]:
        return
    notified_tickers.add(ticker)
    statuses = tuple(item.status for item in completed_by_ticker[ticker])
    event = TickerReadyEvent(
        ticker=ticker,
        statuses=statuses,
        failed=any(status == "FAILED" for status in statuses),
        completed_at=datetime.now(UTC),
    )
    try:
        on_ticker_ready(event)
        operational_metrics.increment("swinglens_technical_overlap_tickers_total")
    except Exception as exc:
        operational_metrics.increment(
            "swinglens_pipeline_optimized_fallback_total",
            component="technical_overlap_callback",
            reason=type(exc).__name__,
        )
