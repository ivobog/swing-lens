from datetime import date, datetime
from decimal import Decimal

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import PriceBar, PriceBarRevision


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
    rows = list(db.scalars(statement).all())
    if as_of is not None:
        rows = project_price_bar_rows_as_of(db, rows, as_of=as_of)

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


def project_price_bar_rows_as_of(
    db: Session,
    rows: list[PriceBar] | tuple[PriceBar, ...],
    *,
    as_of: datetime,
) -> list[PriceBar]:
    """Project current cache rows back to the exact values known at ``as_of``.

    A post-cutoff revision must not make the pre-revision row disappear.  The
    first revision after the boundary contains the prior values and data hash;
    earlier revision records provide the then-current revision metadata.
    Returned revised rows are detached transient objects and cannot be flushed
    back accidentally.
    """

    materialized = list(rows)
    revised_ids = [
        int(row.id)
        for row in materialized
        if row.id is not None and row.revised_at is not None and row.revised_at > as_of
    ]
    if not revised_ids:
        return materialized
    revisions = list(
        db.scalars(
            select(PriceBarRevision)
            .where(PriceBarRevision.price_bar_id.in_(revised_ids))
            .order_by(
                PriceBarRevision.price_bar_id,
                PriceBarRevision.revision_number,
                PriceBarRevision.id,
            )
        )
    )
    by_bar: dict[int, list[PriceBarRevision]] = {}
    for revision in revisions:
        by_bar.setdefault(int(revision.price_bar_id), []).append(revision)

    projected: list[PriceBar] = []
    for row in materialized:
        history = by_bar.get(int(row.id or 0), [])
        first_after = next((item for item in history if item.observed_at > as_of), None)
        if first_after is None:
            # A mutable current row cannot stand in for its historical value.
            # Without the first post-boundary revision record, provenance is
            # incomplete and the only safe behavior is conservative exclusion.
            if row.revised_at is None or row.revised_at <= as_of:
                projected.append(row)
            continue
        prior_revisions = [item for item in history if item.observed_at <= as_of]
        values = dict(first_after.previous_values_json or {})
        projected.append(
            PriceBar(
                id=row.id,
                ticker=row.ticker,
                bar_date=row.bar_date,
                timeframe=row.timeframe,
                open=_decimal_or_none(values.get("open")),
                high=_decimal_or_none(values.get("high")),
                low=_decimal_or_none(values.get("low")),
                close=_decimal_or_none(values.get("close")),
                volume=_decimal_or_none(values.get("volume")),
                source=values.get("source") or row.source,
                what_to_show=values.get("what_to_show") or row.what_to_show,
                adjustment_type=values.get("adjustment_type"),
                created_at=row.created_at,
                first_seen_at=row.first_seen_at,
                last_seen_at=row.last_seen_at,
                revised_at=(prior_revisions[-1].observed_at if prior_revisions else None),
                revision_count=max(0, int(first_after.revision_number) - 1),
                data_hash=first_after.previous_data_hash,
            )
        )
    return projected


def _decimal_or_none(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


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
    adjusted_complete = not adjusted.empty and (
        trades.empty or set(adjusted["date"]) >= set(trades["date"])
    )
    price = adjusted if adjusted_complete else trades
    volume = trades if not trades.empty else None
    price.attrs["price_basis"] = "ADJUSTED_LAST" if adjusted_complete else "TRADES"
    if volume is not None:
        volume.attrs["volume_basis"] = "TRADES"
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
