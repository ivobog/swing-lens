"""Finite, bounded native panel work. No background executor or TTL evidence cache."""

import logging
from contextvars import ContextVar
from functools import wraps
from threading import BoundedSemaphore
from time import monotonic
from uuid import uuid4

from fastapi import HTTPException

_slots = BoundedSemaphore(6)
_panel_slots = {
    key: BoundedSemaphore(2 if key == "history" else 1)
    for key in ("coverage", "rotation", "technical", "chart", "history")
}
_deadline = ContextVar("sector_read_deadline", default=None)
logger = logging.getLogger(__name__)


def checkpoint():
    deadline = _deadline.get()
    if deadline is not None and monotonic() >= deadline:
        raise HTTPException(
            504,
            detail={
                "code": "SECTOR_READ_DEADLINE",
                "message": "Cached read timed out; retry this panel.",
            },
        )


def panel_budget(function):
    @wraps(function)
    def read(*args, **kwargs):
        name = function.__name__
        key = (
            "technical"
            if "technicals" in name
            else "coverage"
            if "coverage" in name
            else "chart"
            if "price_series" in name
            else "rotation"
            if "rotation_preview" in name
            else "history"
        )
        panel_slot = _panel_slots[key]
        if not panel_slot.acquire(blocking=False):
            raise HTTPException(
                503, detail={"code": "SECTOR_PANEL_ALREADY_RUNNING"}, headers={"Retry-After": "2"}
            )
        if not _slots.acquire(blocking=False):
            panel_slot.release()
            raise HTTPException(
                503, detail={"code": "SECTOR_READ_CAPACITY"}, headers={"Retry-After": "2"}
            )
        started, correlation = monotonic(), uuid4().hex
        seconds = 90 if key == "technical" else 45 if key == "history" else 60
        token = _deadline.set(started + seconds)
        status = 200
        try:
            from sqlalchemy import text
            from sqlalchemy.orm import Session

            db = kwargs.get("db")
            if db is None:
                db = next((value for value in args if isinstance(value, Session)), None)
            if isinstance(db, Session) and db.get_bind().dialect.name == "postgresql":
                db.execute(text("SET LOCAL statement_timeout='5000ms'"))
            result = function(*args, **kwargs)
            checkpoint()
            return result
        except HTTPException as exc:
            status = exc.status_code
            raise
        except Exception:
            status = 500
            raise
        finally:
            _deadline.reset(token)
            _slots.release()
            panel_slot.release()
            from app.observability.logging import log_event

            log_event(
                logger,
                "sector.panel.finished",
                route=function.__name__,
                status=status,
                duration_ms=round((monotonic() - started) * 1000, 2),
                correlation_id=correlation,
            )

    return read
