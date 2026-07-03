from dataclasses import dataclass
from datetime import date, datetime

from app.services.ib_api import IB, Contract
from app.settings import Settings, get_settings


@dataclass(frozen=True)
class HistoricalBar:
    ticker: str
    bar_date: date
    timeframe: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    source: str
    what_to_show: str
    adjustment_type: str | None


def fetch_daily_bars(
    ib: IB,
    contract: Contract,
    what_to_show: str,
    settings: Settings | None = None,
    duration: str | None = None,
    bar_size: str | None = None,
) -> list[HistoricalBar]:
    settings = settings or get_settings()
    request_duration = duration or settings.ib_full_backfill_duration
    request_bar_size = bar_size or settings.ib_default_bar_size
    bars = ib.reqHistoricalData(
        contract,
        endDateTime="",
        durationStr=request_duration,
        barSizeSetting=request_bar_size,
        whatToShow=what_to_show,
        useRTH=settings.ib_use_rth,
        formatDate=1,
        keepUpToDate=False,
    )

    ticker = contract.symbol.upper()
    adjustment_type = "adjusted" if what_to_show == "ADJUSTED_LAST" else None
    return [
        HistoricalBar(
            ticker=ticker,
            bar_date=_bar_date(bar.date),
            timeframe=request_bar_size,
            open=_optional_float(bar.open),
            high=_optional_float(bar.high),
            low=_optional_float(bar.low),
            close=_optional_float(bar.close),
            volume=_optional_float(bar.volume),
            source="IB",
            what_to_show=what_to_show,
            adjustment_type=adjustment_type,
        )
        for bar in bars
    ]


def _bar_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y%m%d").date()


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return number
