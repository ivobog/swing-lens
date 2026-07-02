import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import PriceBar


def load_price_bars_frame(
    db: Session,
    ticker: str,
    what_to_show: str,
    timeframe: str = "1 day",
) -> pd.DataFrame:
    rows = db.scalars(
        select(PriceBar)
        .where(
            PriceBar.ticker == ticker.upper(),
            PriceBar.what_to_show == what_to_show,
            PriceBar.timeframe == timeframe,
        )
        .order_by(PriceBar.bar_date)
    ).all()

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
) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    adjusted = load_price_bars_frame(db, ticker, "ADJUSTED_LAST", timeframe)
    trades = load_price_bars_frame(db, ticker, "TRADES", timeframe)
    price = adjusted if not adjusted.empty else trades
    volume = trades if not trades.empty else None
    return price, volume
