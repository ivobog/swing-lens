from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import IBContract, IBFetchItem
from app.services.bar_cache_service import DEFAULT_WHAT_TO_SHOW
from app.services.ohlcv_coverage_service import (
    OhlcvCoverageItem,
    OhlcvCoverageSummary,
    summarize_ohlcv_coverage,
)
from app.services.technical_indicators import load_pine_defaults
from app.services.us_market_calendar import (
    is_latest_daily_bar_current,
    latest_completed_us_trading_day,
    next_us_trading_day,
    subtract_us_trading_sessions,
    us_trading_sessions_between,
)
from app.settings import Settings, get_settings


class FetchAction(StrEnum):
    SKIP = "SKIP"
    TOP_UP_RECENT = "TOP_UP_RECENT"
    REFRESH_RECENT = "REFRESH_RECENT"
    FULL_BACKFILL = "FULL_BACKFILL"
    FORCE_REFRESH = "FORCE_REFRESH"
    CONTRACT_RESOLUTION_REQUIRED = "CONTRACT_RESOLUTION_REQUIRED"
    UNSUPPORTED = "UNSUPPORTED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class FetchPlanItem:
    ticker: str
    contract_status: str
    what_to_show: str
    action: FetchAction
    duration: str | None
    bar_size: str
    current_bar_count: int
    first_bar_date: date | None
    latest_bar_date: date | None
    required_bars: int
    reason: str
    estimated_request_count: int
    data_role: str = "SECURITY"
    coverage_state: str = "UNKNOWN"
    existing_coverage_reused: bool = False
    full_backfill_completed: bool = False
    freshness_threshold_date: date | None = None
    freshness_lag_sessions: int | None = None
    missing_start_date: date | None = None
    missing_end_date: date | None = None
    request_start_date: date | None = None
    request_end_date: date | None = None
    decision_category: str = "UNKNOWN"
    dependency_roles: tuple[str, ...] = ()


@dataclass(frozen=True)
class FetchPlan:
    run_id: int | None
    requested_tickers: list[str]
    symbols_including_benchmarks: list[str]
    items: list[FetchPlanItem]
    estimated_request_count: int
    estimated_full_backfills: int
    estimated_top_ups: int
    estimated_refreshes: int
    estimated_skips: int
    warnings: list[str]
    decision_counts: dict[str, int] = field(default_factory=dict)


def build_fetch_plan(
    db: Session,
    tickers: list[str],
    run_id: int | None = None,
    include_benchmarks: bool = True,
    force_refresh: bool = False,
    force_full_backfill: bool = False,
    what_to_show_values: tuple[str, ...] = DEFAULT_WHAT_TO_SHOW,
    settings: Settings | None = None,
    retry_failed_contracts: bool = False,
) -> FetchPlan:
    settings = settings or get_settings()
    requested_tickers = _normalize_symbols(tickers)
    benchmark_symbols = (
        settings.ib_benchmark_symbols if include_benchmarks and settings.ib_fetch_benchmarks else ()
    )
    symbols = _normalize_symbols([*requested_tickers, *benchmark_symbols])
    coverage = summarize_ohlcv_coverage(
        db,
        requested_tickers,
        benchmarks=benchmark_symbols,
        settings=settings,
    )
    coverage_by_ticker = {item.ticker: item for item in coverage.items}
    benchmark_coverage = _benchmark_coverage_items(
        db=db,
        benchmark_symbols=benchmark_symbols,
        settings=settings,
    )
    coverage_by_ticker.update(benchmark_coverage)
    contract_statuses = _contract_statuses(db, symbols)
    contract_statuses = {
        ticker: _contract_status_for_plan(status, retry_failed_contracts)
        for ticker, status in contract_statuses.items()
    }
    completed_full_backfills = _completed_full_backfills(
        db,
        symbols,
        what_to_show_values,
        settings,
    )
    freshness_threshold = latest_completed_us_trading_day()
    sector_dependency_symbol = _sector_dependency_symbol()

    items = [
        _build_plan_item(
            coverage_item=coverage_by_ticker[symbol],
            contract_status=contract_statuses.get(symbol, "MISSING"),
            what_to_show=what_to_show,
            coverage=coverage,
            settings=settings,
            force_refresh=force_refresh,
            force_full_backfill=force_full_backfill,
            full_backfill_completed=(symbol, what_to_show) in completed_full_backfills,
            is_benchmark=symbol in benchmark_symbols,
            freshness_threshold=freshness_threshold,
            dependency_roles=_dependency_roles(
                symbol,
                requested_tickers=requested_tickers,
                benchmark_symbols=benchmark_symbols,
                sector_symbol=sector_dependency_symbol,
            ),
        )
        for symbol in symbols
        if symbol in coverage_by_ticker
        for what_to_show in what_to_show_values
    ]

    warnings = _plan_warnings(coverage, items)
    decision_counts = _decision_counts(items)
    return FetchPlan(
        run_id=run_id,
        requested_tickers=requested_tickers,
        symbols_including_benchmarks=symbols,
        items=items,
        estimated_request_count=sum(item.estimated_request_count for item in items),
        estimated_full_backfills=sum(
            item.action in {FetchAction.FULL_BACKFILL, FetchAction.FORCE_REFRESH} for item in items
        ),
        estimated_top_ups=sum(item.action == FetchAction.TOP_UP_RECENT for item in items),
        estimated_refreshes=sum(item.action == FetchAction.REFRESH_RECENT for item in items),
        estimated_skips=sum(item.action == FetchAction.SKIP for item in items),
        warnings=warnings,
        decision_counts=decision_counts,
    )


def fetch_plan_to_dict(plan: FetchPlan) -> dict[str, object]:
    payload = asdict(plan)
    payload["items"] = [
        {
            **asdict(item),
            "action": item.action.value,
        }
        for item in plan.items
    ]
    return payload


def _benchmark_coverage_items(
    db: Session,
    benchmark_symbols: tuple[str, ...],
    settings: Settings,
) -> dict[str, OhlcvCoverageItem]:
    if not benchmark_symbols:
        return {}
    summary = summarize_ohlcv_coverage(
        db,
        list(benchmark_symbols),
        benchmarks=(),
        settings=settings,
    )
    return {item.ticker: item for item in summary.items}


def _build_plan_item(
    coverage_item: OhlcvCoverageItem,
    contract_status: str,
    what_to_show: str,
    coverage: OhlcvCoverageSummary,
    settings: Settings,
    force_refresh: bool,
    force_full_backfill: bool,
    full_backfill_completed: bool = False,
    is_benchmark: bool = False,
    freshness_threshold: date | None = None,
    dependency_roles: tuple[str, ...] = (),
) -> FetchPlanItem:
    current_bar_count = _bar_count_for_type(coverage_item, what_to_show)
    first_bar_date = _first_date_for_type(coverage_item, what_to_show)
    latest_bar_date = _latest_date_for_type(coverage_item, what_to_show)
    latest_current = _latest_date_current(
        latest_bar_date,
        settings.ib_daily_bar_stale_after_days,
    )
    freshness_threshold = freshness_threshold or latest_completed_us_trading_day()
    top_up_duration, request_start_date = _incremental_request_window(
        latest_bar_date,
        freshness_threshold,
        revision_sessions=settings.ib_revision_window_sessions,
        fallback_duration=settings.ib_top_up_duration,
    )

    action, duration, reason = _plan_action(
        ticker=coverage_item.ticker,
        contract_status=contract_status,
        what_to_show=what_to_show,
        current_bar_count=current_bar_count,
        required_bars=coverage.required_rows,
        latest_current=latest_current,
        force_refresh=force_refresh,
        force_full_backfill=force_full_backfill,
        settings=settings,
        full_backfill_completed=full_backfill_completed,
        top_up_duration=top_up_duration,
    )

    missing_start_date = (
        next_us_trading_day(latest_bar_date)
        if latest_bar_date is not None and not latest_current
        else None
    )
    missing_end_date = freshness_threshold if missing_start_date else None
    if action not in {FetchAction.TOP_UP_RECENT}:
        request_start_date = _request_start_for_duration(duration, freshness_threshold)
    request_end_date = freshness_threshold if duration else None
    coverage_state = _coverage_state(
        current_bar_count=current_bar_count,
        required_bars=coverage.required_rows,
        latest_current=latest_current,
        full_backfill_completed=full_backfill_completed,
    )
    decision_category = _decision_category(action, latest_current=latest_current)

    return FetchPlanItem(
        ticker=coverage_item.ticker,
        contract_status=contract_status,
        what_to_show=what_to_show,
        action=action,
        duration=duration,
        bar_size=settings.ib_default_bar_size,
        current_bar_count=current_bar_count,
        first_bar_date=first_bar_date,
        latest_bar_date=latest_bar_date,
        required_bars=coverage.required_rows,
        reason=reason,
        estimated_request_count=_estimated_request_count(
            action=action,
            what_to_show=what_to_show,
            current_bar_count=current_bar_count,
            required_bars=coverage.required_rows,
            latest_current=latest_current,
            force_refresh=force_refresh,
            force_full_backfill=force_full_backfill,
            full_backfill_completed=full_backfill_completed,
        ),
        data_role=(
            "_".join(dependency_roles)
            if dependency_roles
            else ("BENCHMARK" if is_benchmark else "SECURITY")
        ),
        coverage_state=coverage_state,
        existing_coverage_reused=current_bar_count > 0,
        full_backfill_completed=full_backfill_completed,
        freshness_threshold_date=freshness_threshold,
        freshness_lag_sessions=(
            us_trading_sessions_between(latest_bar_date, freshness_threshold)
            if latest_bar_date is not None
            else None
        ),
        missing_start_date=missing_start_date,
        missing_end_date=missing_end_date,
        request_start_date=request_start_date,
        request_end_date=request_end_date,
        decision_category=decision_category,
        dependency_roles=dependency_roles,
    )


def _estimated_request_count(
    *,
    action: FetchAction,
    what_to_show: str,
    current_bar_count: int,
    required_bars: int,
    latest_current: bool,
    force_refresh: bool,
    force_full_backfill: bool,
    full_backfill_completed: bool = False,
) -> int:
    if action in {FetchAction.SKIP, FetchAction.UNSUPPORTED, FetchAction.FAILED}:
        return 0
    if action != FetchAction.CONTRACT_RESOLUTION_REQUIRED:
        return 1
    if what_to_show not in DEFAULT_WHAT_TO_SHOW:
        return 0
    return int(
        force_full_backfill
        or (current_bar_count < required_bars and not full_backfill_completed)
        or force_refresh
        or not latest_current
    )


def _plan_action(
    ticker: str,
    contract_status: str,
    what_to_show: str,
    current_bar_count: int,
    required_bars: int,
    latest_current: bool,
    force_refresh: bool,
    force_full_backfill: bool,
    settings: Settings,
    full_backfill_completed: bool = False,
    top_up_duration: str | None = None,
) -> tuple[FetchAction, str | None, str]:
    if contract_status == "FAILED":
        return (
            FetchAction.FAILED,
            None,
            "IB contract resolution previously failed.",
        )
    if contract_status == "AMBIGUOUS":
        return (
            FetchAction.FAILED,
            None,
            "IB contract resolution is ambiguous and requires manual instrument selection.",
        )

    if contract_status != "RESOLVED":
        return (
            FetchAction.CONTRACT_RESOLUTION_REQUIRED,
            None,
            "IB contract must be resolved before historical data can be requested.",
        )

    if force_full_backfill:
        return (
            FetchAction.FORCE_REFRESH,
            settings.ib_full_backfill_duration,
            "Force full refresh was requested.",
        )

    if what_to_show not in DEFAULT_WHAT_TO_SHOW:
        return (FetchAction.UNSUPPORTED, None, f"{what_to_show} is not supported.")

    if current_bar_count == 0 and not full_backfill_completed:
        return (
            FetchAction.FULL_BACKFILL,
            settings.ib_full_backfill_duration,
            f"{ticker} has no cached {what_to_show} daily bars.",
        )

    if current_bar_count < required_bars and not full_backfill_completed:
        return (
            FetchAction.FULL_BACKFILL,
            settings.ib_full_backfill_duration,
            (
                f"{ticker} has {current_bar_count} cached {what_to_show} bars; "
                f"{required_bars} are required."
            ),
        )

    if force_refresh:
        return (
            FetchAction.REFRESH_RECENT,
            settings.ib_refresh_duration,
            "Recent refresh was requested.",
        )

    if not latest_current:
        return (
            FetchAction.TOP_UP_RECENT,
            top_up_duration or settings.ib_top_up_duration,
            (
                f"{ticker} latest {what_to_show} bar is stale; request the missing "
                "sessions plus the configured revision window."
                if current_bar_count >= required_bars
                else (
                    f"{ticker} has limited listed {what_to_show} history after a "
                    "completed full backfill; request only the missing sessions and "
                    "revision window."
                )
            ),
        )

    return (
        FetchAction.SKIP,
        None,
        (
            f"{ticker} has sufficient current {what_to_show} daily bars."
            if current_bar_count >= required_bars
            else (
                f"{ticker} has current limited listed {what_to_show} history and a "
                "completed full backfill; no older bars are available to request."
            )
        ),
    )


def _completed_full_backfills(
    db: Session,
    symbols: list[str],
    what_to_show_values: tuple[str, ...],
    settings: Settings,
) -> set[tuple[str, str]]:
    if not symbols or not what_to_show_values:
        return set()
    rows = db.execute(
        select(IBFetchItem.ticker, IBFetchItem.what_to_show)
        .where(
            IBFetchItem.ticker.in_(symbols),
            IBFetchItem.what_to_show.in_(what_to_show_values),
            IBFetchItem.status == "SUCCESS",
            IBFetchItem.action.in_(
                (FetchAction.FULL_BACKFILL.value, FetchAction.FORCE_REFRESH.value)
            ),
            IBFetchItem.duration == settings.ib_full_backfill_duration,
            IBFetchItem.bar_size == settings.ib_default_bar_size,
        )
        .distinct()
    ).all()
    return {(str(ticker).upper(), str(what_to_show)) for ticker, what_to_show in rows}


def _incremental_request_window(
    latest: date | None,
    freshness_threshold: date,
    *,
    revision_sessions: int,
    fallback_duration: str,
) -> tuple[str, date | None]:
    if latest is None:
        return fallback_duration, _request_start_for_duration(
            fallback_duration,
            freshness_threshold,
        )
    request_start = subtract_us_trading_sessions(latest, max(0, revision_sessions - 1))
    calendar_days = max(1, (freshness_threshold - request_start).days + 1)
    if calendar_days > 365:
        return fallback_duration, _request_start_for_duration(
            fallback_duration,
            freshness_threshold,
        )
    return f"{calendar_days} D", request_start


def _request_start_for_duration(duration: str | None, end: date) -> date | None:
    if not duration:
        return None
    parts = duration.strip().upper().split()
    if len(parts) != 2 or not parts[0].isdigit():
        return None
    amount = int(parts[0])
    unit = parts[1]
    days = {
        "D": amount,
        "W": amount * 7,
        "M": amount * 31,
        "Y": amount * 366,
    }.get(unit)
    return end - timedelta(days=days - 1) if days else None


def _coverage_state(
    *,
    current_bar_count: int,
    required_bars: int,
    latest_current: bool,
    full_backfill_completed: bool,
) -> str:
    if current_bar_count == 0:
        return "EMPTY_AFTER_BACKFILL" if full_backfill_completed else "MISSING"
    if current_bar_count < required_bars:
        return "LIMITED_HISTORY" if full_backfill_completed else "INSUFFICIENT_UNVERIFIED"
    return "CURRENT" if latest_current else "STALE"


def _decision_category(action: FetchAction, *, latest_current: bool) -> str:
    if action == FetchAction.SKIP:
        return "SKIPPED_FRESH" if latest_current else "SKIPPED"
    if action == FetchAction.FULL_BACKFILL:
        return "REQUESTED_FULL_BACKFILL"
    if action == FetchAction.TOP_UP_RECENT:
        return "REQUESTED_INCREMENTAL"
    if action in {FetchAction.REFRESH_RECENT, FetchAction.FORCE_REFRESH}:
        return "REQUESTED_FORCED_REFRESH"
    if action == FetchAction.CONTRACT_RESOLUTION_REQUIRED:
        return "CONTRACT_RESOLUTION_REQUIRED"
    return action.value


def _decision_counts(items: list[FetchPlanItem]) -> dict[str, int]:
    return {
        "requested": sum(item.estimated_request_count for item in items),
        "reused": sum(item.existing_coverage_reused for item in items),
        "incremental": sum(item.action == FetchAction.TOP_UP_RECENT for item in items),
        "full_backfill": sum(item.action == FetchAction.FULL_BACKFILL for item in items),
        "skipped_fresh": sum(item.decision_category == "SKIPPED_FRESH" for item in items),
        "forced_refresh": sum(
            item.action in {FetchAction.REFRESH_RECENT, FetchAction.FORCE_REFRESH} for item in items
        ),
        "benchmark_requests": sum(
            ("BENCHMARK" in item.dependency_roles or item.data_role == "BENCHMARK")
            and item.estimated_request_count
            for item in items
        ),
        "adjusted_requests": sum(
            item.what_to_show == "ADJUSTED_LAST" and item.estimated_request_count for item in items
        ),
        "trades_requests": sum(
            item.what_to_show == "TRADES" and item.estimated_request_count for item in items
        ),
    }


def _sector_dependency_symbol() -> str | None:
    market_rs = load_pine_defaults().get("market_rs", {})
    if not market_rs.get("useSectorBenchmark", False):
        return None
    return str(market_rs.get("sectorSymbol") or "").strip().upper() or None


def _dependency_roles(
    symbol: str,
    *,
    requested_tickers: list[str],
    benchmark_symbols: tuple[str, ...],
    sector_symbol: str | None,
) -> tuple[str, ...]:
    roles: list[str] = []
    if symbol in requested_tickers:
        roles.append("REQUESTED")
    if symbol in benchmark_symbols:
        roles.append("BENCHMARK")
    if symbol == sector_symbol:
        roles.append("SECTOR")
    return tuple(roles or ["SECURITY"])


def _contract_statuses(db: Session, symbols: list[str]) -> dict[str, str]:
    if not symbols:
        return {}
    rows = db.execute(
        select(IBContract.ticker, IBContract.resolution_status).where(
            IBContract.ticker.in_(symbols)
        )
    ).all()
    return {str(ticker).upper(): str(status) for ticker, status in rows}


def _contract_status_for_plan(status: str, retry_failed_contracts: bool) -> str:
    if retry_failed_contracts and status == "FAILED":
        return "MISSING"
    return status


def _bar_count_for_type(item: OhlcvCoverageItem, what_to_show: str) -> int:
    if what_to_show == "ADJUSTED_LAST":
        return item.adjusted_bars
    if what_to_show == "TRADES":
        return item.trades_bars
    return 0


def _first_date_for_type(item: OhlcvCoverageItem, what_to_show: str) -> date | None:
    if what_to_show == "ADJUSTED_LAST":
        return item.first_adjusted_date
    if what_to_show == "TRADES":
        return item.first_trades_date
    return None


def _latest_date_for_type(item: OhlcvCoverageItem, what_to_show: str) -> date | None:
    if what_to_show == "ADJUSTED_LAST":
        return item.latest_adjusted_date
    if what_to_show == "TRADES":
        return item.latest_trades_date
    return None


def _latest_date_current(latest: date | None, stale_after_days: int) -> bool:
    _ = stale_after_days
    return is_latest_daily_bar_current(latest)


def _plan_warnings(coverage: OhlcvCoverageSummary, items: list[FetchPlanItem]) -> list[str]:
    warnings: list[str] = []
    unresolved = sum(item.action == FetchAction.CONTRACT_RESOLUTION_REQUIRED for item in items)
    failed = sum(item.action == FetchAction.FAILED for item in items)
    if unresolved:
        warnings.append(f"{unresolved} plan items require IB contract resolution before fetch.")
    if failed:
        warnings.append(f"{failed} plan items have failed IB contract resolution.")
    benchmark_ready = coverage.benchmark_ready or {}
    if "SPY" in benchmark_ready and not coverage.benchmark_spy_ready:
        warnings.append("SPY benchmark coverage is not ready.")
    if "QQQ" in benchmark_ready and not coverage.benchmark_qqq_ready:
        warnings.append("QQQ benchmark coverage is not ready.")
    return warnings


def _normalize_symbols(tickers: list[str] | tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    symbols: list[str] = []
    for ticker in tickers:
        symbol = ticker.strip().upper()
        if symbol and symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    return symbols
