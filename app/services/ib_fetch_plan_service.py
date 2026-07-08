from dataclasses import asdict, dataclass
from datetime import date
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import IBContract
from app.services.bar_cache_service import DEFAULT_WHAT_TO_SHOW
from app.services.ohlcv_coverage_service import (
    OhlcvCoverageItem,
    OhlcvCoverageSummary,
    summarize_ohlcv_coverage,
)
from app.services.us_market_calendar import is_latest_daily_bar_current
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


def build_fetch_plan(
    db: Session,
    tickers: list[str],
    run_id: int | None = None,
    include_benchmarks: bool = True,
    force_refresh: bool = False,
    force_full_backfill: bool = False,
    what_to_show_values: tuple[str, ...] = DEFAULT_WHAT_TO_SHOW,
    settings: Settings | None = None,
) -> FetchPlan:
    settings = settings or get_settings()
    requested_tickers = _normalize_symbols(tickers)
    benchmark_symbols = (
        settings.ib_benchmark_symbols
        if include_benchmarks and settings.ib_fetch_benchmarks
        else ()
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

    items = [
        _build_plan_item(
            coverage_item=coverage_by_ticker[symbol],
            contract_status=contract_statuses.get(symbol, "MISSING"),
            what_to_show=what_to_show,
            coverage=coverage,
            settings=settings,
            force_refresh=force_refresh,
            force_full_backfill=force_full_backfill,
        )
        for symbol in symbols
        if symbol in coverage_by_ticker
        for what_to_show in what_to_show_values
    ]

    warnings = _plan_warnings(coverage, items)
    return FetchPlan(
        run_id=run_id,
        requested_tickers=requested_tickers,
        symbols_including_benchmarks=symbols,
        items=items,
        estimated_request_count=sum(item.estimated_request_count for item in items),
        estimated_full_backfills=sum(
            item.action in {FetchAction.FULL_BACKFILL, FetchAction.FORCE_REFRESH}
            for item in items
        ),
        estimated_top_ups=sum(item.action == FetchAction.TOP_UP_RECENT for item in items),
        estimated_refreshes=sum(item.action == FetchAction.REFRESH_RECENT for item in items),
        estimated_skips=sum(item.action == FetchAction.SKIP for item in items),
        warnings=warnings,
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
) -> FetchPlanItem:
    current_bar_count = _bar_count_for_type(coverage_item, what_to_show)
    first_bar_date = _first_date_for_type(coverage_item, what_to_show)
    latest_bar_date = _latest_date_for_type(coverage_item, what_to_show)
    latest_current = _latest_date_current(
        latest_bar_date,
        settings.ib_daily_bar_stale_after_days,
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
    )

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
        estimated_request_count=0
        if action
        in {
            FetchAction.SKIP,
            FetchAction.CONTRACT_RESOLUTION_REQUIRED,
            FetchAction.UNSUPPORTED,
            FetchAction.FAILED,
        }
        else 1,
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
) -> tuple[FetchAction, str | None, str]:
    if contract_status == "FAILED":
        return (
            FetchAction.FAILED,
            None,
            "IB contract resolution previously failed.",
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

    if current_bar_count == 0:
        return (
            FetchAction.FULL_BACKFILL,
            settings.ib_full_backfill_duration,
            f"{ticker} has no cached {what_to_show} daily bars.",
        )

    if current_bar_count < required_bars:
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
            settings.ib_top_up_duration,
            f"{ticker} latest {what_to_show} bar is stale.",
        )

    return (
        FetchAction.SKIP,
        None,
        f"{ticker} has sufficient current {what_to_show} daily bars.",
    )


def _contract_statuses(db: Session, symbols: list[str]) -> dict[str, str]:
    if not symbols:
        return {}
    rows = db.execute(
        select(IBContract.ticker, IBContract.resolution_status).where(
            IBContract.ticker.in_(symbols)
        )
    ).all()
    return {str(ticker).upper(): str(status) for ticker, status in rows}


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
