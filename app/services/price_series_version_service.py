from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import PriceBar, PriceSeriesVersion
from app.services.operational_metrics import operational_metrics

SeriesIdentity = tuple[str, str, str]


def maintain_price_series_versions(
    db: Session,
    bars: Iterable[Any],
    changed_keys: set[SeriesIdentity],
    *,
    now: datetime | None = None,
) -> int:
    """Advance each changed series once within the caller's transaction."""

    identities: set[SeriesIdentity] = set()
    for bar in bars:
        identity = (str(bar.ticker).upper(), str(bar.timeframe), str(bar.what_to_show))
        if identity in changed_keys:
            identities.add(identity)
    if not identities:
        return 0

    changed_at = now or datetime.now(UTC)
    db.flush()
    advanced = 0
    for ticker, timeframe, what_to_show in sorted(identities):
        series = db.scalar(
            select(PriceSeriesVersion)
            .where(
                PriceSeriesVersion.ticker == ticker,
                PriceSeriesVersion.timeframe == timeframe,
                PriceSeriesVersion.what_to_show == what_to_show,
            )
            .with_for_update()
        )
        price_bars = list(
            db.scalars(
                select(PriceBar).where(
                    PriceBar.ticker == ticker,
                    PriceBar.timeframe == timeframe,
                    PriceBar.what_to_show == what_to_show,
                )
            )
        )
        if not price_bars:
            continue
        dates = [bar.bar_date for bar in price_bars]
        if series is None:
            series = PriceSeriesVersion(
                ticker=ticker,
                timeframe=timeframe,
                what_to_show=what_to_show,
                series_version=1,
            )
            db.add(series)
        else:
            series.series_version = (series.series_version or 0) + 1
        series.bar_count = len(price_bars)
        series.first_bar_date = min(dates)
        series.latest_bar_date = max(dates)
        series.last_changed_at = changed_at
        advanced += 1

    if advanced:
        operational_metrics.increment(
            "swinglens_price_series_version_advances_total",
            value=advanced,
        )
    return advanced


def load_series_versions(
    db: Session,
    ticker: str,
    *,
    timeframe: str = "1 day",
) -> dict[str, int]:
    rows = db.scalars(
        select(PriceSeriesVersion).where(
            PriceSeriesVersion.ticker == ticker.upper(),
            PriceSeriesVersion.timeframe == timeframe,
        )
    )
    return {row.what_to_show: int(row.series_version) for row in rows}
