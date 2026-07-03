from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import PriceBar, RawCompanyRow
from app.services.technical_indicators import load_pine_defaults


@dataclass(frozen=True)
class OhlcvCoverageItem:
    ticker: str
    adjusted_bars: int
    trades_bars: int
    has_price: bool
    has_volume: bool
    sufficient_history: bool
    status: str


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


def summarize_run_ohlcv_coverage(db: Session, run_id: int) -> OhlcvCoverageSummary:
    tickers = _run_tickers(db, run_id)
    required_rows = _required_rows()
    counts = _bar_counts(db, [*tickers, "SPY", "QQQ"])
    items = [_coverage_item(ticker, counts, required_rows) for ticker in tickers]

    return OhlcvCoverageSummary(
        total_tickers=len(items),
        ready_count=sum(item.status == "ready" for item in items),
        insufficient_count=sum(item.status == "insufficient" for item in items),
        missing_count=sum(item.status == "missing" for item in items),
        benchmark_spy_ready=_coverage_item("SPY", counts, required_rows).sufficient_history,
        benchmark_qqq_ready=_coverage_item("QQQ", counts, required_rows).sufficient_history,
        required_rows=required_rows,
        items=items,
    )


def _coverage_item(
    ticker: str,
    counts: dict[tuple[str, str], int],
    required_rows: int,
) -> OhlcvCoverageItem:
    adjusted_bars = counts.get((ticker, "ADJUSTED_LAST"), 0)
    trades_bars = counts.get((ticker, "TRADES"), 0)
    price_bars = adjusted_bars or trades_bars
    has_price = price_bars > 0
    has_volume = trades_bars > 0
    sufficient_history = price_bars >= required_rows

    if not has_price:
        status = "missing"
    elif sufficient_history:
        status = "ready"
    else:
        status = "insufficient"

    return OhlcvCoverageItem(
        ticker=ticker,
        adjusted_bars=adjusted_bars,
        trades_bars=trades_bars,
        has_price=has_price,
        has_volume=has_volume,
        sufficient_history=sufficient_history,
        status=status,
    )


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


def _bar_counts(db: Session, tickers: list[str]) -> dict[tuple[str, str], int]:
    if not tickers:
        return {}

    rows = db.execute(
        select(PriceBar.ticker, PriceBar.what_to_show, func.count(PriceBar.id))
        .where(PriceBar.ticker.in_(tickers), PriceBar.timeframe == "1 day")
        .group_by(PriceBar.ticker, PriceBar.what_to_show)
    ).all()

    return {
        (str(ticker).upper(), str(what_to_show)): int(count)
        for ticker, what_to_show, count in rows
    }


def _required_rows(params: dict[str, Any] | None = None) -> int:
    params = params or load_pine_defaults()
    return max(
        int(params["trend"]["smaSlowLen"]),
        int(params["trend"]["highLow52Len"]),
        int(params["market_rs"]["rocLongLen"]),
    )
