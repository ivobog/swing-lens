from dataclasses import dataclass
from datetime import date, datetime, time
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import IBContract, PriceBar, RawCompanyRow
from app.services.technical_indicators import load_pine_defaults
from app.services.us_market_calendar import is_latest_daily_bar_current
from app.settings import Settings, get_settings


class OhlcvCoverageStatus(StrEnum):
    READY = "ready"
    STALE = "stale"
    MISSING = "missing"
    INSUFFICIENT_HISTORY = "insufficient"
    MISSING_VOLUME = "missing_volume"
    CONTRACT_FAILED = "contract_failed"


@dataclass(frozen=True)
class BarSeriesCoverage:
    count: int = 0
    first_date: date | None = None
    latest_date: date | None = None


@dataclass(frozen=True)
class OhlcvCoverageItem:
    ticker: str
    adjusted_bars: int
    trades_bars: int
    has_price: bool
    has_volume: bool
    sufficient_history: bool
    status: str
    first_adjusted_date: date | None = None
    latest_adjusted_date: date | None = None
    first_trades_date: date | None = None
    latest_trades_date: date | None = None
    has_adjusted_price: bool = False
    has_trades_volume: bool = False
    latest_bar_current: bool = False
    reason: str = ""


@dataclass(frozen=True)
class OhlcvCoverageSummary:
    total_tickers: int
    ready_count: int
    insufficient_count: int
    missing_count: int
    benchmark_spy_ready: bool
    benchmark_qqq_ready: bool
    required_rows: int
    items: list[OhlcvCoverageItem]
    stale_count: int = 0
    missing_volume_count: int = 0
    failed_contract_count: int = 0
    benchmark_ready: dict[str, bool] | None = None


def summarize_run_ohlcv_coverage(db: Session, run_id: int) -> OhlcvCoverageSummary:
    tickers = _run_tickers(db, run_id)
    settings = get_settings()
    return summarize_ohlcv_coverage(db, tickers, settings=settings)


def summarize_ohlcv_coverage(
    db: Session,
    tickers: list[str],
    required_daily_bars: int | None = None,
    stale_after_days: int | None = None,
    benchmarks: tuple[str, ...] | None = None,
    settings: Settings | None = None,
    today: date | None = None,
) -> OhlcvCoverageSummary:
    settings = settings or get_settings()
    required_rows = required_daily_bars or _required_rows(settings=settings)
    stale_days = stale_after_days or settings.ib_daily_bar_stale_after_days
    benchmark_symbols = benchmarks if benchmarks is not None else settings.ib_benchmark_symbols
    normalized_tickers = _normalize_tickers(tickers)
    symbols = _normalize_tickers([*normalized_tickers, *benchmark_symbols])
    stats = _bar_stats(db, symbols)
    failed_contracts = _failed_contract_tickers(db, symbols)
    items = [
        _coverage_item(
            ticker,
            stats,
            required_rows=required_rows,
            stale_after_days=stale_days,
            today=today,
            contract_failed=ticker in failed_contracts,
        )
        for ticker in normalized_tickers
    ]
    benchmark_items = {
        ticker: _coverage_item(
            ticker,
            stats,
            required_rows=required_rows,
            stale_after_days=stale_days,
            today=today,
            contract_failed=ticker in failed_contracts,
        )
        for ticker in benchmark_symbols
    }
    benchmark_ready = {
        ticker: item.status == OhlcvCoverageStatus.READY
        for ticker, item in benchmark_items.items()
    }

    return OhlcvCoverageSummary(
        total_tickers=len(items),
        ready_count=sum(item.status == OhlcvCoverageStatus.READY for item in items),
        insufficient_count=sum(
            item.status == OhlcvCoverageStatus.INSUFFICIENT_HISTORY for item in items
        ),
        missing_count=sum(item.status == OhlcvCoverageStatus.MISSING for item in items),
        benchmark_spy_ready=benchmark_ready.get("SPY", False),
        benchmark_qqq_ready=benchmark_ready.get("QQQ", False),
        required_rows=required_rows,
        items=items,
        stale_count=sum(item.status == OhlcvCoverageStatus.STALE for item in items),
        missing_volume_count=sum(
            item.status == OhlcvCoverageStatus.MISSING_VOLUME for item in items
        ),
        failed_contract_count=sum(
            item.status == OhlcvCoverageStatus.CONTRACT_FAILED for item in items
        ),
        benchmark_ready=benchmark_ready,
    )


def _coverage_item(
    ticker: str,
    stats: dict[tuple[str, str], BarSeriesCoverage],
    required_rows: int,
    stale_after_days: int = 3,
    today: date | None = None,
    contract_failed: bool = False,
) -> OhlcvCoverageItem:
    normalized = ticker.upper()
    adjusted = stats.get((normalized, "ADJUSTED_LAST"), BarSeriesCoverage())
    trades = stats.get((normalized, "TRADES"), BarSeriesCoverage())
    adjusted_bars = adjusted.count
    trades_bars = trades.count
    price_bars = adjusted_bars or trades_bars
    has_price = price_bars > 0
    has_volume = trades_bars > 0
    sufficient_history = price_bars >= required_rows
    latest_price_date = adjusted.latest_date or trades.latest_date
    _ = stale_after_days
    latest_bar_current = is_latest_daily_bar_current(
        latest_price_date,
        now=_coverage_now(today),
    )

    if contract_failed:
        status = OhlcvCoverageStatus.CONTRACT_FAILED
        reason = "IB contract resolution failed."
    elif not has_price:
        status = OhlcvCoverageStatus.MISSING
        reason = "No adjusted or trades price bars are cached."
    elif not sufficient_history:
        status = OhlcvCoverageStatus.INSUFFICIENT_HISTORY
        reason = f"Only {price_bars} daily price bars are cached; {required_rows} are required."
    elif not has_volume:
        status = OhlcvCoverageStatus.MISSING_VOLUME
        reason = "TRADES bars are missing, so volume coverage is unavailable."
    elif not latest_bar_current:
        status = OhlcvCoverageStatus.STALE
        reason = "Latest cached price bar is older than the latest completed US trading day."
    else:
        status = OhlcvCoverageStatus.READY
        reason = "Adjusted price and trades volume coverage are ready."

    return OhlcvCoverageItem(
        ticker=normalized,
        adjusted_bars=adjusted_bars,
        trades_bars=trades_bars,
        has_price=has_price,
        has_volume=has_volume,
        sufficient_history=sufficient_history,
        status=status,
        first_adjusted_date=adjusted.first_date,
        latest_adjusted_date=adjusted.latest_date,
        first_trades_date=trades.first_date,
        latest_trades_date=trades.latest_date,
        has_adjusted_price=adjusted_bars > 0,
        has_trades_volume=has_volume,
        latest_bar_current=latest_bar_current,
        reason=reason,
    )


def _coverage_now(today: date | None) -> datetime | None:
    if today is None:
        return None
    return datetime.combine(today, time(23, 59), tzinfo=ZoneInfo("America/New_York"))


def _run_tickers(db: Session, run_id: int) -> list[str]:
    rows = db.scalars(
        select(RawCompanyRow.ticker)
        .where(RawCompanyRow.run_id == run_id)
        .order_by(RawCompanyRow.row_number)
    ).all()
    seen: set[str] = set()
    tickers: list[str] = []
    for row in rows:
        ticker = row.upper()
        if ticker not in seen:
            seen.add(ticker)
            tickers.append(ticker)
    return tickers


def _bar_stats(db: Session, tickers: list[str]) -> dict[tuple[str, str], BarSeriesCoverage]:
    if not tickers:
        return {}

    rows = db.execute(
        select(
            PriceBar.ticker,
            PriceBar.what_to_show,
            func.count(PriceBar.id),
            func.min(PriceBar.bar_date),
            func.max(PriceBar.bar_date),
        )
        .where(PriceBar.ticker.in_(tickers), PriceBar.timeframe == "1 day")
        .group_by(PriceBar.ticker, PriceBar.what_to_show)
    ).all()

    return {
        (str(ticker).upper(), str(what_to_show)): BarSeriesCoverage(
            count=int(count),
            first_date=first_date,
            latest_date=latest_date,
        )
        for ticker, what_to_show, count, first_date, latest_date in rows
    }


def _failed_contract_tickers(db: Session, tickers: list[str]) -> set[str]:
    if not tickers:
        return set()
    rows = db.scalars(
        select(IBContract.ticker).where(
            IBContract.ticker.in_(tickers),
            IBContract.resolution_status == "FAILED",
        )
    ).all()
    return {ticker.upper() for ticker in rows}


def _required_rows(
    params: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> int:
    params = params or load_pine_defaults()
    configured_minimum = settings.ib_required_daily_bars if settings else 0
    return max(
        configured_minimum,
        int(params["trend"]["smaSlowLen"]),
        int(params["trend"]["highLow52Len"]),
        int(params["market_rs"]["rocLongLen"]),
    )


def _normalize_tickers(tickers: list[str] | tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for ticker in tickers:
        symbol = ticker.strip().upper()
        if symbol and symbol not in seen:
            seen.add(symbol)
            normalized.append(symbol)
    return normalized
