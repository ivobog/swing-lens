"""Session-local lower date bound over the unchanged canonical PIT reader."""

from contextlib import contextmanager

from sqlalchemy import event
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
        event.listen(db, "do_orm_execute", bound)
        try:
            yield
        finally:
            event.remove(db, "do_orm_execute", bound)
