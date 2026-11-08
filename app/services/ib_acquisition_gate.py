"""Cross-process historical slot and pacing ledger.

Reservations never expire while the owning OS process can still issue requests.
An execution-token fence alone cannot stop an in-flight socket. Only disconnect
or proven process death permits a handoff. All SQL transactions are short.
"""

import os
import socket
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import wraps
from inspect import signature
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.process_identity import process_started_at

_current = ContextVar("ib_historical_slot", default=None)
_request_reserved = ContextVar("ib_historical_request_reserved", default=False)


def mark_connection(connected):
    slot = _current.get()
    if slot is not None:
        slot.release_safe = not connected
        slot.connection_managed = True


class AcquisitionCancelled(RuntimeError):
    pass


class ReviewedScopeExpired(ValueError):
    pass


def validate_reviewed_plan(plan):
    from app.services.ib_fetch_plan_service import latest_completed_us_trading_day

    latest = latest_completed_us_trading_day()
    for item in plan.items:
        expiry = getattr(item, "reviewed_session_expiry", None)
        if expiry is not None and expiry != latest:
            raise ReviewedScopeExpired(
                f"REVIEW_SCOPE_EXPIRED: frozen session {expiry}; completed session {latest}; "
                "a new manual admission is required"
            )


class HistoricalSlot:
    def __init__(
        self,
        engine,
        settings,
        *,
        job_id=None,
        execution_token=None,
        check=None,
        on_wait=None,
        on_acquired=None,
    ):
        self.engine, self.settings = engine, settings
        self.job_id, self.execution_token, self.check = job_id, execution_token, check
        self.owner = uuid4().hex
        self.host, self.pid = socket.gethostname(), os.getpid()
        self.started = process_started_at()
        self.release_safe = True
        self.connection_managed = False
        self.on_wait, self.on_acquired, self.waited = on_wait, on_acquired, False

    def checkpoint(self):
        if self.check and self.check():
            raise AcquisitionCancelled("Historical acquisition cancellation requested")
        if self.job_id is not None:
            from app.services.background_job_service import JobLeaseLost

            with self.engine.begin() as connection:
                valid = connection.scalar(
                    text(
                        "SELECT count(*) FROM background_jobs WHERE id=:id AND status='RUNNING' "
                        "AND execution_token=:token AND lease_expires_at>CURRENT_TIMESTAMP"
                    ),
                    {"id": self.job_id, "token": self.execution_token},
                )
            if not valid:
                raise JobLeaseLost("Historical acquisition ownership lost")

    def _owner_dead(self, row):
        if row["hostname"] != self.host:
            return False
        # Access denied / ambiguous inspection is NOT proof of death.
        import psutil

        try:
            process = psutil.Process(row["process_id"])
            actual = datetime.fromtimestamp(process.create_time(), UTC)
            return abs((actual - row["process_started_at"]).total_seconds()) > 2
        except psutil.NoSuchProcess:
            return True
        except (psutil.AccessDenied, OSError):
            return False

    def acquire_historical_slot(self):
        deadline = time.monotonic() + self.settings.ib_acquisition_wait_seconds
        while True:
            self.checkpoint()
            with self.engine.begin() as connection:
                row = (
                    connection.execute(
                        text("SELECT * FROM ib_historical_gate WHERE id=1 FOR UPDATE")
                    )
                    .mappings()
                    .one()
                )
                if not row["owner"] or self._owner_dead(row):
                    connection.execute(
                        text(
                            "UPDATE ib_historical_gate SET owner=:owner,hostname=:host,"
                            "process_id=:pid,"
                            "process_started_at=:started,job_id=:job,execution_token=:token,"
                            "acquired_at=CURRENT_TIMESTAMP WHERE id=1"
                        ),
                        dict(
                            owner=self.owner,
                            host=self.host,
                            pid=self.pid,
                            started=self.started,
                            job=self.job_id,
                            token=self.execution_token,
                        ),
                    )
                    return
            if time.monotonic() >= deadline:
                raise TimeoutError("IB_ACQUISITION_WAIT_EXCEEDED: check native job status")
            if not self.waited:
                self.waited = True
                if self.on_wait:
                    self.on_wait()
            time.sleep(0.25)

    def pace(self, weight=1):
        import json

        # Use the stricter configured historical budget for all clients. Handoffs
        # retain the ledger, including a reservation preceding a worker death.
        limit = min(
            self.settings.ib_requests_per_minute,
            self.settings.ib_intelligence_historical_requests_per_minute,
        )
        if weight > limit:
            raise ValueError("Historical request weight exceeds the configured request budget")
        spacing = max(
            self.settings.ib_min_seconds_between_requests,
            self.settings.ib_intelligence_historical_min_spacing_seconds,
        )
        while True:
            self.checkpoint()
            with self.engine.begin() as connection:
                row = (
                    connection.execute(
                        text(
                            "SELECT owner,requests_json,"
                            "extract(epoch from clock_timestamp()) AS now "
                            "FROM ib_historical_gate WHERE id=1 FOR UPDATE"
                        )
                    )
                    .mappings()
                    .one()
                )
                if row["owner"] != self.owner:
                    raise RuntimeError("Historical slot ownership lost")
                now = float(row["now"])
                recent = [float(value) for value in row["requests_json"] if float(value) > now - 60]
                wait = max(0, recent[-1] + spacing - now) if recent else 0
                if len(recent) + weight > limit:
                    wait = max(wait, recent[-(limit - weight + 1)] + 60 - now)
                if wait <= 0:
                    connection.execute(
                        text(
                            "UPDATE ib_historical_gate SET requests_json=CAST(:requests AS json) "
                            "WHERE id=1"
                        ),
                        {"requests": json.dumps(recent + [now] * weight)},
                    )
                    return
            time.sleep(min(0.25, wait))

    def release_historical_slot(self):
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE ib_historical_gate SET owner=NULL,job_id=NULL,execution_token=NULL "
                    "WHERE id=1 AND owner=:owner"
                ),
                {"owner": self.owner},
            )


@contextmanager
def historical_slot(engine, settings, **kwargs):
    existing: HistoricalSlot | None = _current.get()
    if existing is not None:
        yield existing
        return
    slot = HistoricalSlot(engine, settings, **kwargs)
    slot.acquire_historical_slot()
    token = _current.set(slot)
    try:
        if slot.waited and slot.on_acquired:
            slot.on_acquired()
        yield slot
    finally:
        _current.reset(token)
        if slot.release_safe:
            slot.release_historical_slot()


def coordinate_fetch(function):
    """Wrap the common native executor, including FULL_PIPELINE inline fetches."""
    spec = signature(function)

    @wraps(function)
    def execute(*args, **kwargs):
        bound = spec.bind(*args, **kwargs)
        db = bound.arguments["db"]
        if not isinstance(db, Session) or db.get_bind().dialect.name != "postgresql":
            return function(*args, **kwargs)
        from app.settings import get_settings

        settings = bound.arguments.get("settings") or get_settings()
        check = bound.arguments.get("should_cancel")
        job_id = bound.arguments.get("execution_job_id")
        execution_token = bound.arguments.get("execution_token")
        from app.services.domain_write_fence import current_domain_write_ownership
        ownership = current_domain_write_ownership()
        if ownership is not None:
            job_id = job_id if job_id is not None else ownership.job_id
            execution_token = execution_token or ownership.execution_token
        progress = bound.arguments.get("progress_callback")
        if progress:
            progress(
                db,
                stage="WAITING_FOR_IB_ACQUISITION",
                current_item=None,
                processed=0,
                total=len(bound.arguments["plan"].items),
            )
        db.commit()

        def cancelled():
            from app.services.background_job_service import JobLeaseLost
            from app.services.domain_write_fence import control_plane_transaction

            # This callback owns only cancellation reads. The active domain
            # binding may be an already-committed item Session; its identity is
            # still authoritative even when it has no open transaction.
            with control_plane_transaction(db):
                if db.new or db.dirty or db.deleted:
                    raise JobLeaseLost("Cancellation read cannot commit pending domain writes")
                result = bool(check and check())
                if db.new or db.dirty or db.deleted:
                    raise JobLeaseLost("Cancellation callback introduced pending domain writes")
                return result

        bound.arguments["should_cancel"] = cancelled
        try:
            with historical_slot(
                db.get_bind(),
                settings,
                job_id=job_id,
                execution_token=execution_token,
                check=cancelled,
            ):
                if progress:
                    progress(
                        db,
                        stage="FETCHING_MARKET_DATA",
                        current_item=None,
                        processed=0,
                        total=len(bound.arguments["plan"].items),
                    )
                    db.commit()
                validate_reviewed_plan(bound.arguments["plan"])
                return function(*bound.args, **bound.kwargs)
        except (ReviewedScopeExpired, TimeoutError) as exc:
            from app.models.tables import IBFetchRun

            fetch_id = bound.arguments.get("fetch_run_id")
            if fetch_id:
                run = db.get(IBFetchRun, fetch_id)
                run.status, run.completed_at, run.message = "FAILED", datetime.now(UTC), str(exc)
                db.commit()
            raise
        except AcquisitionCancelled as exc:
            from app.models.tables import IBFetchRun
            from app.services.background_worker import CancelRequested

            fetch_id = bound.arguments.get("fetch_run_id")
            if fetch_id:
                run = db.get(IBFetchRun, fetch_id)
                run.status, run.completed_at = "CANCELLED", datetime.now(UTC)
                db.commit()
            raise CancelRequested(str(exc)) from exc

    return execute


@contextmanager
def historical_request(settings, *, check=None, ib=None, coordinated=True, weight=1, engine=None):
    """Also cover standalone cache, capability and intelligence requests."""
    if not coordinated:
        yield
        return
    existing: HistoricalSlot | None = _current.get()
    if existing is not None:
        if not _request_reserved.get():
            existing.pace(weight)
        token = _request_reserved.set(True)
        try:
            yield
        except BaseException:
            if ib is not None and not existing.connection_managed:
                existing.release_safe = False
                ib.disconnect()
                existing.release_safe = not ib.isConnected()
            raise
        finally:
            _request_reserved.reset(token)
        return
    if engine is None:
        from app.db import engine
    from app.services.domain_write_fence import current_domain_write_ownership

    ownership = current_domain_write_ownership()
    identity = {}
    if ownership is not None:
        identity = dict(job_id=ownership.job_id, execution_token=ownership.execution_token)

        def progress(stage):
            from app.services.background_job_service import record_job_progress
            from app.services.domain_write_fence import detached_control_plane_scope

            with detached_control_plane_scope(), Session(engine) as db:
                record_job_progress(
                    db,
                    job_id=ownership.job_id,
                    execution_token=ownership.execution_token,
                    stage=stage,
                )
                db.commit()

        identity.update(
            on_wait=lambda: progress("WAITING_FOR_IB_ACQUISITION"),
            on_acquired=lambda: progress("FETCHING_MARKET_DATA"),
        )
    with historical_slot(engine, settings, check=check, **identity):
        slot: HistoricalSlot | None = _current.get()
        if slot is None:
            raise RuntimeError("Historical acquisition context is missing")
        slot.pace(weight)
        try:
            yield
        except BaseException:
            if ib is not None:
                slot.release_safe = False
                ib.disconnect()
                slot.release_safe = not ib.isConnected()
            raise
