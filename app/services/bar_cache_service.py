import time
from dataclasses import dataclass, field
from decimal import Decimal

from ib_insync import IB
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.tables import PriceBar
from app.services.ib_connection import create_ib_client
from app.services.ib_contract_resolver import resolve_us_stock_contract
from app.services.ib_data_fetcher import HistoricalBar, fetch_daily_bars
from app.settings import Settings, get_settings

DEFAULT_WHAT_TO_SHOW = ("ADJUSTED_LAST", "TRADES")
DEFAULT_BENCHMARKS = ("SPY", "QQQ")


@dataclass
class BarFetchItem:
    ticker: str
    what_to_show: str
    fetched: int = 0
    inserted: int = 0
    status: str = "PENDING"
    error_message: str | None = None


@dataclass
class BarFetchSummary:
    items: list[BarFetchItem] = field(default_factory=list)

    @property
    def fetched(self) -> int:
        return sum(item.fetched for item in self.items)

    @property
    def inserted(self) -> int:
        return sum(item.inserted for item in self.items)

    @property
    def failures(self) -> list[BarFetchItem]:
        return [item for item in self.items if item.status == "FAILED"]


def ensure_daily_bars(
    db: Session,
    tickers: list[str],
    include_benchmarks: bool = True,
    what_to_show_values: tuple[str, ...] = DEFAULT_WHAT_TO_SHOW,
    settings: Settings | None = None,
) -> BarFetchSummary:
    settings = settings or get_settings()
    symbols = _normalize_symbols(tickers, include_benchmarks)
    summary = BarFetchSummary()
    ib = create_ib_client()

    try:
        ib.connect(
            settings.ib_host,
            settings.ib_port,
            clientId=settings.ib_client_id,
            timeout=settings.ib_timeout_seconds,
            readonly=True,
        )
        for symbol in symbols:
            resolution = resolve_us_stock_contract(db, symbol, ib)
            if not resolution.contract:
                for what_to_show in what_to_show_values:
                    summary.items.append(
                        BarFetchItem(
                            ticker=symbol,
                            what_to_show=what_to_show,
                            status="FAILED",
                            error_message=resolution.error_message or "Contract resolution failed.",
                        )
                    )
                db.commit()
                continue

            for what_to_show in what_to_show_values:
                item = _fetch_and_cache_one(db, ib, resolution.contract, what_to_show, settings)
                summary.items.append(item)
                db.commit()
                time.sleep(settings.ib_request_delay_seconds)
    finally:
        if ib.isConnected():
            ib.disconnect()

    return summary


def cache_bars(db: Session, bars: list[HistoricalBar]) -> int:
    if not bars:
        return 0

    statement = insert(PriceBar).values(
        [
            {
                "ticker": bar.ticker,
                "bar_date": bar.bar_date,
                "timeframe": bar.timeframe,
                "open": _decimal_or_none(bar.open),
                "high": _decimal_or_none(bar.high),
                "low": _decimal_or_none(bar.low),
                "close": _decimal_or_none(bar.close),
                "volume": _decimal_or_none(bar.volume),
                "source": bar.source,
                "what_to_show": bar.what_to_show,
                "adjustment_type": bar.adjustment_type,
            }
            for bar in bars
        ]
    )
    statement = statement.on_conflict_do_nothing(
        constraint="uq_price_bars_ticker_date_timeframe_what_to_show"
    ).returning(PriceBar.id)
    result = db.execute(statement)
    return len(result.scalars().all())


def _fetch_and_cache_one(
    db: Session,
    ib: IB,
    contract,
    what_to_show: str,
    settings: Settings,
) -> BarFetchItem:
    ticker = contract.symbol.upper()
    item = BarFetchItem(ticker=ticker, what_to_show=what_to_show)
    try:
        bars = fetch_daily_bars(ib, contract, what_to_show, settings)
        item.fetched = len(bars)
        item.inserted = cache_bars(db, bars)
        item.status = "COMPLETED"
    except Exception as exc:
        item.status = "FAILED"
        item.error_message = str(exc)
    return item


def _normalize_symbols(tickers: list[str], include_benchmarks: bool) -> list[str]:
    symbols = {ticker.strip().upper() for ticker in tickers if ticker.strip()}
    if include_benchmarks:
        symbols.update(DEFAULT_BENCHMARKS)
    return sorted(symbols)


def _decimal_or_none(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))
