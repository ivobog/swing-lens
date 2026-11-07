"""Session-local lower date bound over the unchanged canonical PIT reader."""

from contextlib import contextmanager

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.models.tables import PriceBar


@contextmanager
def bounded_cached_read(db, first):
    if not isinstance(db, Session):
        yield
        return

    def bound(state):
        if state.is_select and any(
            item.get("entity") is PriceBar
            for item in getattr(state.statement, "column_descriptions", ())
        ):
            state.statement = state.statement.where(PriceBar.bar_date >= first)

    with db.no_autoflush:
        previous = db.info.get("msi_min_session")
        db.info["msi_min_session"] = first
        event.listen(db, "do_orm_execute", bound)
        try:
            yield
        finally:
            event.remove(db, "do_orm_execute", bound)
            if previous is None:
                db.info.pop("msi_min_session", None)
            else:
                db.info["msi_min_session"] = previous


@contextmanager
def batch_cached_read(
    db, symbols, *, timeframe, cutoff, first=None, enabled=True, reuse_fingerprints=True
):
    """One PIT-visible row materialization per native panel, with no shared frames.

    The scope fixes receipt cutoff, session and window. Canonical projection and
    manifests still run in the existing reader; no preferred-basis policy changes.
    """
    if not enabled or not isinstance(db, Session):
        yield
        return
    statement = (
        select(PriceBar.__table__)
        .where(
            PriceBar.ticker.in_(symbols),
            PriceBar.what_to_show.in_(("ADJUSTED_LAST", "TRADES")),
            PriceBar.timeframe == timeframe,
            PriceBar.bar_date <= cutoff.latest_completed_session,
            PriceBar.created_at <= cutoff.cutoff_at,
            PriceBar.first_seen_at <= cutoff.cutoff_at,
        )
        .order_by(PriceBar.bar_date)
    )
    if first is not None:
        statement = statement.where(PriceBar.bar_date >= first)
    with db.no_autoflush:
        # Immutable Core rows preserve the same typed values without retaining
        # a cohort-wide ORM identity graph during concurrent panel calculations.
        rows = db.execute(statement).all()
    groups = {}
    for row in rows:
        groups.setdefault((row.ticker, row.what_to_show, row.timeframe), []).append(row)
    previous = db.info.get("msi_price_rows")
    previous_direct = db.info.get("msi_direct_fingerprints")
    db.info["msi_price_rows"] = (cutoff.cutoff_at, cutoff.latest_completed_session, groups)
    db.info["msi_direct_fingerprints"] = not reuse_fingerprints
    try:
        with db.no_autoflush:
            yield
    finally:
        if previous is None:
            db.info.pop("msi_price_rows", None)
        else:
            db.info["msi_price_rows"] = previous
        if previous_direct is None:
            db.info.pop("msi_direct_fingerprints", None)
        else:
            db.info["msi_direct_fingerprints"] = previous_direct
