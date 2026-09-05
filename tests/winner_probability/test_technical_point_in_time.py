from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models.tables import PriceBar
from app.services.technical_score_service import load_winner_point_in_time_technical_frames


def test_winner_technical_series_excludes_entry_day_and_later_observations() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[PriceBar.__table__])
    with Session(engine) as db:
        for bar_id, bar_day, first_seen in (
            (1, date(2026, 8, 19), datetime(2026, 8, 19, 21, 0, tzinfo=UTC)),
            (2, date(2026, 8, 20), datetime(2026, 8, 20, 15, 0, tzinfo=UTC)),
        ):
            db.add(
                PriceBar(
                    id=bar_id,
                    ticker="MSFT",
                    bar_date=bar_day,
                    timeframe="1 day",
                    open=Decimal("100"),
                    high=Decimal("101"),
                    low=Decimal("99"),
                    close=Decimal("100"),
                    volume=Decimal("1000"),
                    source="fixture",
                    what_to_show="TRADES",
                    created_at=first_seen,
                    first_seen_at=first_seen,
                    last_seen_at=first_seen,
                )
            )
        db.commit()

        price, _ = load_winner_point_in_time_technical_frames(
            db,
            "MSFT",
            decision_at=datetime(2026, 8, 20, 15, 28, tzinfo=UTC),
        )

    assert list(price["date"]) == [date(2026, 8, 19)]
