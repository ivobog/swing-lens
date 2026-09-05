from datetime import date, datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import PriceBar


def load_price_bars_frame(
    db: Session,
    ticker: str,
    what_to_show: str,
    timeframe: str = "1 day",
    *,
    max_session: date | None = None,
    as_of: datetime | None = None,
) -> pd.DataFrame:
    statement = (
        select(PriceBar)
        .where(
            PriceBar.ticker == ticker.upper(),
            PriceBar.what_to_show == what_to_show,
            PriceBar.timeframe == timeframe,
        )
        .order_by(PriceBar.bar_date)
    )
    if max_session is not None:
        statement = statement.where(PriceBar.bar_date <= max_session)
    if as_of is not None:
        statement = statement.where(PriceBar.created_at <= as_of).where(
            PriceBar.first_seen_at <= as_of
        )
        # Current rows revised after the boundary cannot safely stand in for
        # their historical value without revision reconstruction.
        statement = statement.where(
            (PriceBar.revised_at.is_(None)) | (PriceBar.revised_at <= as_of)
        )
    rows = db.scalars(statement).all()

    return pd.DataFrame(
        [
            {
                "date": row.bar_date,
                "open": float(row.open) if row.open is not None else None,
                "high": float(row.high) if row.high is not None else None,
                "low": float(row.low) if row.low is not None else None,
                "close": float(row.close) if row.close is not None else None,
                "volume": float(row.volume) if row.volume is not None else None,
            }
            for row in rows
        ],
        columns=["date", "open", "high", "low", "close", "volume"],
    )


def load_preferred_ohlcv_frames(
    db: Session,
    ticker: str,
    timeframe: str = "1 day",
    *,
    max_session: date | None = None,
    as_of: datetime | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    adjusted = load_price_bars_frame(
        db, ticker, "ADJUSTED_LAST", timeframe, max_session=max_session, as_of=as_of
    )
    trades = load_price_bars_frame(
        db, ticker, "TRADES", timeframe, max_session=max_session, as_of=as_of
    )
    price = adjusted if not adjusted.empty else trades
    volume = trades if not trades.empty else None
    return price, volume


def load_price_bar_rows(
    db: Session,
    ticker: str,
    *,
    start_date: date,
    end_date: date,
    what_to_show: str,
    timeframe: str = "1 day",
) -> list[PriceBar]:
    return list(
        db.scalars(
            select(PriceBar)
            .where(
                PriceBar.ticker == ticker.upper(),
                PriceBar.what_to_show == what_to_show,
                PriceBar.timeframe == timeframe,
                PriceBar.bar_date >= start_date,
                PriceBar.bar_date <= end_date,
            )
            .order_by(PriceBar.bar_date)
        )
    )


def load_preferred_price_bar_rows(
    db: Session,
    ticker: str,
    *,
    start_date: date,
    end_date: date,
    timeframe: str = "1 day",
) -> list[PriceBar]:
    adjusted = load_price_bar_rows(
        db,
        ticker,
        start_date=start_date,
        end_date=end_date,
        what_to_show="ADJUSTED_LAST",
        timeframe=timeframe,
    )
    if adjusted:
        return adjusted
    return load_price_bar_rows(
        db,
        ticker,
        start_date=start_date,
        end_date=end_date,
        what_to_show="TRADES",
        timeframe=timeframe,
    )
