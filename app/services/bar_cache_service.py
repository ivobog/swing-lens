import hashlib
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from app.models.tables import PriceBar
from app.services.ib_api import IB
from app.services.ib_connection import create_ib_client
from app.services.ib_contract_resolver import resolve_us_stock_contract
from app.services.ib_data_fetcher import HistoricalBar, fetch_daily_bars
from app.settings import Settings, get_settings

DEFAULT_WHAT_TO_SHOW = ("ADJUSTED_LAST", "TRADES")


@dataclass
class BarFetchItem:
    ticker: str
    what_to_show: str
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    revised: int = 0
    unchanged: int = 0
    status: str = "PENDING"
    error_message: str | None = None


@dataclass(frozen=True)
class BarUpsertSummary:
    inserted: int = 0
    updated: int = 0
    revised: int = 0
    unchanged: int = 0


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
    def updated(self) -> int:
        return sum(item.updated for item in self.items)

    @property
    def revised(self) -> int:
        return sum(item.revised for item in self.items)

    @property
    def unchanged(self) -> int:
        return sum(item.unchanged for item in self.items)

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
    symbols = _normalize_symbols(
        tickers,
        include_benchmarks and settings.ib_fetch_benchmarks,
        settings.ib_benchmark_symbols,
    )
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


def cache_bars(db: Session, bars: list[HistoricalBar]) -> BarUpsertSummary:
    if not bars:
        return BarUpsertSummary()

    now = datetime.now(UTC)
    existing = _existing_bars_by_key(db, bars)
    inserted = 0
    updated = 0
    revised = 0
    unchanged = 0

    for bar in bars:
        key = _bar_key(bar.ticker, bar.bar_date, bar.timeframe, bar.what_to_show)
        current = existing.get(key)
        new_hash = bar_data_hash(bar)

        if current is None:
            db.add(_to_price_bar(bar, new_hash, now))
            inserted += 1
            continue

        current_hash = current.data_hash or price_bar_data_hash(current)
        if current_hash == new_hash:
            current.last_seen_at = now
            if current.data_hash is None:
                current.data_hash = new_hash
            unchanged += 1
            continue

        current.open = _decimal_or_none(bar.open)
        current.high = _decimal_or_none(bar.high)
        current.low = _decimal_or_none(bar.low)
        current.close = _decimal_or_none(bar.close)
        current.volume = _decimal_or_none(bar.volume)
        current.source = bar.source
        current.adjustment_type = bar.adjustment_type
        current.last_seen_at = now
        current.revised_at = now
        current.revision_count = (current.revision_count or 0) + 1
        current.data_hash = new_hash
        updated += 1
        revised += 1

    return BarUpsertSummary(
        inserted=inserted,
        updated=updated,
        revised=revised,
        unchanged=unchanged,
    )


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
        bars = fetch_daily_bars(
            ib,
            contract,
            what_to_show,
            settings,
            duration=settings.ib_full_backfill_duration,
            bar_size=settings.ib_default_bar_size,
        )
        item.fetched = len(bars)
        upsert_summary = cache_bars(db, bars)
        item.inserted = upsert_summary.inserted
        item.updated = upsert_summary.updated
        item.revised = upsert_summary.revised
        item.unchanged = upsert_summary.unchanged
        item.status = "COMPLETED"
    except Exception as exc:
        item.status = "FAILED"
        item.error_message = str(exc)
    return item


def _normalize_symbols(
    tickers: list[str],
    include_benchmarks: bool,
    benchmarks: tuple[str, ...],
) -> list[str]:
    symbols = {ticker.strip().upper() for ticker in tickers if ticker.strip()}
    if include_benchmarks:
        symbols.update(benchmarks)
    return sorted(symbols)


def _decimal_or_none(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _existing_bars_by_key(
    db: Session,
    bars: list[HistoricalBar],
) -> dict[tuple[str, object, str, str], PriceBar]:
    keys = {
        _bar_key(bar.ticker, bar.bar_date, bar.timeframe, bar.what_to_show)
        for bar in bars
    }
    if not keys:
        return {}

    rows = db.scalars(
        select(PriceBar).where(
            tuple_(
                PriceBar.ticker,
                PriceBar.bar_date,
                PriceBar.timeframe,
                PriceBar.what_to_show,
            ).in_(keys)
        )
    ).all()
    return {
        _bar_key(row.ticker, row.bar_date, row.timeframe, row.what_to_show): row
        for row in rows
    }


def _bar_key(
    ticker: str,
    bar_date: object,
    timeframe: str,
    what_to_show: str,
) -> tuple[str, object, str, str]:
    return (ticker.upper(), bar_date, timeframe, what_to_show)


def _to_price_bar(bar: HistoricalBar, data_hash: str, now: datetime) -> PriceBar:
    return PriceBar(
        ticker=bar.ticker.upper(),
        bar_date=bar.bar_date,
        timeframe=bar.timeframe,
        open=_decimal_or_none(bar.open),
        high=_decimal_or_none(bar.high),
        low=_decimal_or_none(bar.low),
        close=_decimal_or_none(bar.close),
        volume=_decimal_or_none(bar.volume),
        source=bar.source,
        what_to_show=bar.what_to_show,
        adjustment_type=bar.adjustment_type,
        first_seen_at=now,
        last_seen_at=now,
        revision_count=0,
        data_hash=data_hash,
    )


def bar_data_hash(bar: HistoricalBar) -> str:
    return _hash_bar_values(
        open_value=bar.open,
        high_value=bar.high,
        low_value=bar.low,
        close_value=bar.close,
        volume_value=bar.volume,
        source=bar.source,
        what_to_show=bar.what_to_show,
        adjustment_type=bar.adjustment_type,
    )


def price_bar_data_hash(row: PriceBar) -> str:
    return _hash_bar_values(
        open_value=row.open,
        high_value=row.high,
        low_value=row.low,
        close_value=row.close,
        volume_value=row.volume,
        source=row.source,
        what_to_show=row.what_to_show,
        adjustment_type=row.adjustment_type,
    )


def _hash_bar_values(
    *,
    open_value: object,
    high_value: object,
    low_value: object,
    close_value: object,
    volume_value: object,
    source: str,
    what_to_show: str,
    adjustment_type: str | None,
) -> str:
    payload = "|".join(
        [
            _hash_decimal(open_value),
            _hash_decimal(high_value),
            _hash_decimal(low_value),
            _hash_decimal(close_value),
            _hash_decimal(volume_value),
            source or "",
            what_to_show or "",
            adjustment_type or "",
        ]
    )
    return hashlib.md5(payload.encode("utf-8"), usedforsecurity=False).hexdigest()


def _hash_decimal(value: object) -> str:
    if value is None:
        return ""
    decimal = Decimal(str(value)).normalize()
    return format(decimal, "f")
