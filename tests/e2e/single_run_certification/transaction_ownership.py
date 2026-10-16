"""Test-scoped SQLAlchemy ownership tracing for browser admission."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import traceback
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

_INSTALLED = False
_LOCK = threading.Lock()
_ORIGINAL_SESSION_INIT = None
_ORIGINAL_SESSION_CLOSE = None


def _backend_pid(connection) -> int | None:
    try:
        driver = connection.connection.driver_connection
        return int(driver.info.backend_pid)
    except (AttributeError, TypeError, ValueError):
        return None


def _task_identity() -> str | None:
    try:
        task = asyncio.current_task()
    except RuntimeError:
        return None
    return None if task is None else f"{type(task).__name__}:{id(task)}"


def _stack() -> list[str]:
    root = str(Path(__file__).resolve().parents[3]).lower()
    frames = traceback.extract_stack(limit=40)[:-2]
    return [
        f"{frame.filename}:{frame.lineno}:{frame.name}"
        for frame in frames
        if frame.filename.lower().startswith(root)
        and "transaction_ownership.py" not in frame.filename.lower()
    ][-16:]


def _scope() -> dict[str, object]:
    try:
        from app.observability.db_monitor import current_scope_fields

        return current_scope_fields()
    except Exception:
        return {}


def _phase_for(session: Session) -> str:
    return str(session.info.get("ownership_trace_phase") or "UNCLASSIFIED")


def _write(event_name: str, **fields: object) -> None:
    root = os.environ.get("CERTIFICATION_TRANSACTION_TRACE_DIR")
    if not root:
        return
    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "event": event_name,
        "process_id": os.getpid(),
        "process_role": os.environ.get("PROCESS_ROLE", "UNKNOWN"),
        "argv": sys.argv[:4],
        "thread_id": threading.get_ident(),
        "thread_name": threading.current_thread().name,
        "task": _task_identity(),
        **_scope(),
        **fields,
    }
    path = Path(root) / f"transaction-ownership-{os.getpid()}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(record, default=str, sort_keys=True) + "\n"
    with _LOCK, path.open("a", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def record_trace_event(event_name: str, **fields: object) -> None:
    """Append one bounded adapter-owned event to the process trace."""

    _write(event_name, **fields)


def install_transaction_ownership_trace(engine: Engine) -> bool:
    """Install bounded per-process tracing only when explicitly requested."""

    global _INSTALLED, _ORIGINAL_SESSION_CLOSE, _ORIGINAL_SESSION_INIT
    if _INSTALLED or not os.environ.get("CERTIFICATION_TRANSACTION_TRACE_DIR"):
        return _INSTALLED
    _INSTALLED = True

    _ORIGINAL_SESSION_INIT = Session.__init__
    _ORIGINAL_SESSION_CLOSE = Session.close

    def traced_session_init(session, *args, **kwargs):
        creation_stack = _stack()
        _ORIGINAL_SESSION_INIT(session, *args, **kwargs)
        session.info["ownership_trace_session_creation_stack"] = creation_stack
        _write(
            "session_created",
            session_id=id(session),
            session_creation_stack=creation_stack,
        )

    def traced_session_close(session):
        _write(
            "session_close_begin",
            session_id=id(session),
            session_in_transaction=session.in_transaction(),
            backend_pid=session.info.get("ownership_trace_backend_pid"),
            close_stack=_stack(),
        )
        try:
            return _ORIGINAL_SESSION_CLOSE(session)
        finally:
            _write(
                "session_close_end",
                session_id=id(session),
                session_in_transaction=session.in_transaction(),
                backend_pid=session.info.get("ownership_trace_backend_pid"),
            )

    Session.__init__ = traced_session_init
    Session.close = traced_session_close

    @event.listens_for(Session, "after_transaction_create")
    def after_transaction_create(session, transaction):
        timings = session.info.setdefault("ownership_trace_transaction_timings", {})
        timings[id(transaction)] = perf_counter()
        if transaction.parent is None:
            ordinal = int(session.info.get("ownership_trace_transaction_ordinal") or 0) + 1
            session.info["ownership_trace_transaction_ordinal"] = ordinal
            scope = _scope()
            if scope.get("http_path", "").endswith("/pipeline"):
                phase = {
                    1: "PREFLIGHT_CREATION",
                    2: "PRE_ENQUEUE_VERIFICATION",
                }.get(ordinal, f"PIPELINE_REQUEST_TRANSACTION_{ordinal}")
            else:
                phase = str(
                    session.info.pop("ownership_trace_next_phase", None)
                    or f"SESSION_TRANSACTION_{ordinal}"
                )
            session.info["ownership_trace_phase"] = phase
            session.info["ownership_trace_completion_action"] = None
            session.info["ownership_trace_stack"] = _stack()
            _write(
                "session_transaction_created",
                phase=phase,
                session_id=id(session),
                transaction_id=id(transaction),
                session_creation_site=session.info.get(
                    "ownership_trace_session_creation_stack",
                    session.info["ownership_trace_stack"],
                ),
            )
        else:
            aggregate = session.info.setdefault("ownership_trace_savepoints", {})
            aggregate["created"] = int(aggregate.get("created") or 0) + 1
            aggregate["nested_created"] = int(aggregate.get("nested_created") or 0) + int(
                bool(transaction.nested)
            )

    @event.listens_for(Session, "after_begin")
    def after_begin(session, transaction, connection):
        pid = _backend_pid(connection)
        session.info["ownership_trace_backend_pid"] = pid
        connection.info["ownership_trace_session_id"] = id(session)
        connection.info["ownership_trace_phase"] = _phase_for(session)
        if transaction.parent is None:
            connection.info["ownership_trace_sql"] = {
                "count": 0,
                "seconds": 0.0,
                "operations": {},
                "setup_snapshot_count": 0,
                "last_setup_snapshot_sql": None,
            }
            session.info["ownership_trace_sql"] = connection.info["ownership_trace_sql"]
        _write(
            "session_transaction_begin",
            phase=_phase_for(session),
            session_id=id(session),
            transaction_id=id(transaction),
            connection_id=id(connection),
            engine_id=id(connection.engine),
            backend_pid=pid,
            begin_stack=_stack(),
        )

    @event.listens_for(Session, "after_commit")
    def after_commit(session):
        session.info["ownership_trace_completion_action"] = "COMMIT"
        _write(
            "session_commit",
            phase=_phase_for(session),
            session_id=id(session),
            backend_pid=session.info.get("ownership_trace_backend_pid"),
            end_stack=_stack(),
        )

    @event.listens_for(Session, "after_rollback")
    def after_rollback(session):
        session.info["ownership_trace_completion_action"] = "ROLLBACK"
        _write(
            "session_rollback",
            phase=_phase_for(session),
            session_id=id(session),
            backend_pid=session.info.get("ownership_trace_backend_pid"),
            end_stack=_stack(),
        )

    @event.listens_for(Session, "after_transaction_end")
    def after_transaction_end(session, transaction):
        started = session.info.get("ownership_trace_transaction_timings", {}).pop(
            id(transaction), None
        )
        duration = perf_counter() - started if started is not None else None
        if transaction.parent is None:
            summary = session.info.pop("ownership_trace_sql", None) or {}
            sql_seconds = float(summary.get("seconds") or 0.0)
            flushes = session.info.pop("ownership_trace_flushes", {})
            savepoints = session.info.pop("ownership_trace_savepoints", {})
            _write(
                "session_transaction_end",
                phase=_phase_for(session),
                session_id=id(session),
                transaction_id=id(transaction),
                backend_pid=session.info.get("ownership_trace_backend_pid"),
                active=session.in_transaction(),
                completion_action=session.info.get("ownership_trace_completion_action"),
                duration_seconds=round(duration, 6) if duration is not None else None,
                sql_count=int(summary.get("count") or 0),
                sql_seconds=round(sql_seconds, 6),
                application_seconds=(
                    round(max(0.0, duration - sql_seconds), 6) if duration is not None else None
                ),
                flush_count=int(flushes.get("count") or 0),
                flush_seconds=round(float(flushes.get("seconds") or 0.0), 6),
                savepoint_created_count=int(savepoints.get("created") or 0),
                savepoint_nested_created_count=int(savepoints.get("nested_created") or 0),
                savepoint_ended_count=int(savepoints.get("ended") or 0),
                savepoint_seconds=round(float(savepoints.get("seconds") or 0.0), 6),
            )
        else:
            aggregate = session.info.setdefault("ownership_trace_savepoints", {})
            aggregate["ended"] = int(aggregate.get("ended") or 0) + 1
            aggregate["seconds"] = float(aggregate.get("seconds") or 0.0) + float(duration or 0.0)

    @event.listens_for(Session, "before_flush")
    def before_flush(session, _flush_context, _instances):
        session.info["ownership_trace_flush_started"] = perf_counter()

    @event.listens_for(Session, "after_flush")
    def after_flush(session, _flush_context):
        started = session.info.pop("ownership_trace_flush_started", None)
        aggregate = session.info.setdefault("ownership_trace_flushes", {})
        aggregate["count"] = int(aggregate.get("count") or 0) + 1
        aggregate["seconds"] = float(aggregate.get("seconds") or 0.0) + (
            perf_counter() - started if started is not None else 0.0
        )

    @event.listens_for(engine.pool, "checkout")
    def checkout(dbapi_connection, _connection_record, connection_proxy):
        pid = getattr(getattr(dbapi_connection, "info", None), "backend_pid", None)
        _write(
            "connection_checkout",
            driver_connection_id=id(dbapi_connection),
            connection_proxy_id=id(connection_proxy),
            backend_pid=pid,
        )

    @event.listens_for(engine.pool, "checkin")
    def checkin(dbapi_connection, connection_record):
        pid = getattr(getattr(dbapi_connection, "info", None), "backend_pid", None)
        sql_summary = connection_record.info.pop("ownership_trace_sql", None)
        if sql_summary:
            _write(
                "transaction_sql_summary",
                phase=connection_record.info.get("ownership_trace_phase"),
                session_id=connection_record.info.get("ownership_trace_session_id"),
                backend_pid=pid,
                sql_count=sql_summary["count"],
                sql_seconds=round(sql_summary["seconds"], 6),
                sql_operations=sql_summary["operations"],
                setup_snapshot_query_count=sql_summary["setup_snapshot_count"],
                last_setup_snapshot_sql=sql_summary["last_setup_snapshot_sql"],
            )
        _write(
            "connection_checkin",
            phase=connection_record.info.get("ownership_trace_phase"),
            session_id=connection_record.info.get("ownership_trace_session_id"),
            driver_connection_id=id(dbapi_connection),
            backend_pid=pid,
        )

    @event.listens_for(engine, "before_cursor_execute")
    def before_cursor_execute(connection, _cursor, statement, _params, context, _many):
        normalized = " ".join(str(statement).split())
        operation = normalized.split(" ", 1)[0].upper()
        context._ownership_trace_started_at = perf_counter()
        context._ownership_trace_operation = operation
        summary = connection.info.get("ownership_trace_sql")
        if summary is None:
            return
        summary["count"] += 1
        summary["operations"][operation] = summary["operations"].get(operation, 0) + 1
        if "setup_signal_snapshots" in normalized.lower():
            summary["setup_snapshot_count"] += 1
            details = {
                "sql_operation": operation,
                "sql_relation": "setup_signal_snapshots",
                "sql_preview": normalized[:800],
                "query_stack": _stack(),
            }
            summary["last_setup_snapshot_sql"] = details
            if summary["setup_snapshot_count"] == 1:
                _write(
                    "setup_snapshot_sql_first",
                    phase=connection.info.get("ownership_trace_phase"),
                    session_id=connection.info.get("ownership_trace_session_id"),
                    connection_id=id(connection),
                    engine_id=id(connection.engine),
                    backend_pid=_backend_pid(connection),
                    **details,
                )

    @event.listens_for(engine, "after_cursor_execute")
    def after_cursor_execute(connection, _cursor, _statement, _params, context, _many):
        summary = connection.info.get("ownership_trace_sql")
        started_at = getattr(context, "_ownership_trace_started_at", None)
        if summary is not None and started_at is not None:
            summary["seconds"] += perf_counter() - started_at

    _write(
        "trace_installed",
        engine_id=id(engine),
        trace_directory=os.environ["CERTIFICATION_TRANSACTION_TRACE_DIR"],
    )
    return True


def record_admission_boundary(db: Session, *, phase: str, **fields: object) -> None:
    """Record the test adapter's pre-enqueue stop point."""

    _write(
        "admission_boundary",
        phase=phase,
        session_id=id(db),
        session_in_transaction=db.in_transaction(),
        backend_pid=db.info.get("ownership_trace_backend_pid"),
        boundary_stack=_stack(),
        **fields,
    )
