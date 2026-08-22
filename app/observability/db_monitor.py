from __future__ import annotations

import atexit
import hashlib
import json
import os
import queue
import re
import socket
import sys
import threading
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from types import FrameType
from typing import Any

from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MONITOR_FILE = Path(__file__).resolve()
_HOSTNAME = socket.gethostname()
_SQL_COMMENT_RE = re.compile(r"/\*.*?\*/|--[^\r\n]*", re.DOTALL)
_STRING_LITERAL_RE = re.compile(r"'(?:''|[^'])*'")
_DOUBLE_QUOTED_TEXT_RE = re.compile(r'"(?:""|[^"])*"')
_URL_CREDENTIAL_RE = re.compile(r"(://[^:/\s]+:)[^@/\s]+(@)")
_NUMBER_LITERAL_RE = re.compile(r"(?<![\w$])-?\d+(?:\.\d+)?(?![\w$])")
_PLACEHOLDER_RE = re.compile(
    r"%\([^)]+\)s|%s|(?<!:):\w+|\$\d+|\?",
    re.IGNORECASE,
)
_WHITESPACE_RE = re.compile(r"\s+")
_OPERATION_RE = re.compile(r"^\s*([A-Za-z]+)")
_SAFE_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_LIBRARY_PATH_PARTS = {
    "site-packages",
    "sqlalchemy",
    "starlette",
    "fastapi",
    "anyio",
}
_UNKNOWN_CALLER: dict[str, Any] = {
    "source_file": "UNKNOWN",
    "line_number": None,
    "function": "UNKNOWN",
    "module": None,
}


@dataclass
class ExecutionScope:
    origin_type: str = "UNKNOWN"
    request_id: str | None = None
    http_method: str | None = None
    http_path: str | None = None
    route_name: str | None = None
    route_path: str | None = None
    job_id: int | str | None = None
    job_type: str | None = None
    run_id: int | str | None = None
    worker_id: str | None = None
    workflow_key: str | None = None
    ticker: str | None = None
    company: str | None = None
    asgi_scope: dict[str, Any] | None = field(default=None, repr=False)

    def fields(self) -> dict[str, Any]:
        if self.asgi_scope is not None:
            route = self.asgi_scope.get("route")
            if route is not None:
                self.route_name = getattr(route, "name", None) or self.route_name
                self.route_path = getattr(route, "path", None) or self.route_path
        return {
            key: value
            for key, value in {
                "origin_type": self.origin_type,
                "request_id": self.request_id,
                "http_method": self.http_method,
                "http_path": self.http_path,
                "route_name": self.route_name,
                "route_path": self.route_path,
                "job_id": self.job_id,
                "job_type": self.job_type,
                "run_id": self.run_id,
                "worker_id": self.worker_id,
                "workflow_key": self.workflow_key,
                "ticker": self.ticker,
                "company": self.company,
            }.items()
            if value is not None
        }


@dataclass
class SqlSummary:
    started_at: float = field(default_factory=time.perf_counter)
    query_count: int = 0
    operation_counts: Counter[str] = field(default_factory=Counter)
    total_sql_ms: float = 0.0
    maximum_sql_ms: float = 0.0
    fingerprint_calls: Counter[str] = field(default_factory=Counter)
    fingerprint_ms: defaultdict[str, float] = field(default_factory=lambda: defaultdict(float))
    fingerprint_sql: dict[str, str] = field(default_factory=dict)
    fingerprint_callers: dict[str, dict[str, Any]] = field(default_factory=dict)
    flush_count: int = 0
    flush_new: int = 0
    flush_dirty: int = 0
    flush_deleted: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add_sql(self, record: Mapping[str, Any]) -> None:
        fingerprint = str(record["query_fingerprint"])
        duration_ms = float(record["duration_ms"])
        operation = str(record["operation"])
        with self._lock:
            self.query_count += 1
            self.operation_counts[operation] += 1
            self.total_sql_ms += duration_ms
            self.maximum_sql_ms = max(self.maximum_sql_ms, duration_ms)
            self.fingerprint_calls[fingerprint] += 1
            self.fingerprint_ms[fingerprint] += duration_ms
            self.fingerprint_sql.setdefault(fingerprint, str(record["normalized_sql"]))
            caller = record.get("python_caller")
            if caller:
                self.fingerprint_callers.setdefault(fingerprint, dict(caller))

    def add_flush(self, *, new: int, dirty: int, deleted: int) -> None:
        with self._lock:
            self.flush_count += 1
            self.flush_new += new
            self.flush_dirty += dirty
            self.flush_deleted += deleted

    def snapshot(
        self,
        *,
        total_duration_ms: float | None = None,
        n_plus_one_threshold: int = 10,
    ) -> dict[str, Any]:
        with self._lock:
            top_fingerprint = None
            if self.fingerprint_ms:
                top_fingerprint = max(self.fingerprint_ms, key=self.fingerprint_ms.__getitem__)
            most_repeated = None
            if self.fingerprint_calls:
                most_repeated = max(
                    self.fingerprint_calls,
                    key=lambda fingerprint: (
                        self.fingerprint_calls[fingerprint],
                        self.fingerprint_ms[fingerprint],
                    ),
                )
            duplicate_count = sum(max(0, calls - 1) for calls in self.fingerprint_calls.values())
            top_fingerprints = sorted(
                self.fingerprint_calls,
                key=lambda fingerprint: self.fingerprint_ms[fingerprint],
                reverse=True,
            )[:10]
            repeated_fingerprints = sorted(
                (
                    fingerprint
                    for fingerprint, calls in self.fingerprint_calls.items()
                    if calls >= n_plus_one_threshold
                ),
                key=lambda fingerprint: self.fingerprint_calls[fingerprint],
                reverse=True,
            )[:10]
            total_duration_ms = (
                float(total_duration_ms)
                if total_duration_ms is not None
                else (time.perf_counter() - self.started_at) * 1000.0
            )
            sql_time_pct = (
                (self.total_sql_ms / total_duration_ms) * 100.0 if total_duration_ms > 0 else None
            )
            return {
                "total_duration_ms": round(total_duration_ms, 3),
                "sql_query_count": self.query_count,
                "sql_select_count": self.operation_counts["SELECT"],
                "sql_insert_count": self.operation_counts["INSERT"],
                "sql_update_count": self.operation_counts["UPDATE"],
                "sql_delete_count": self.operation_counts["DELETE"],
                "sql_other_count": self.query_count
                - sum(
                    self.operation_counts[name] for name in ("SELECT", "INSERT", "UPDATE", "DELETE")
                ),
                "total_sql_ms": round(self.total_sql_ms, 3),
                "maximum_sql_ms": round(self.maximum_sql_ms, 3),
                "sql_time_pct": round(sql_time_pct, 3) if sql_time_pct is not None else None,
                "unique_query_fingerprints": len(self.fingerprint_calls),
                "duplicate_query_count": duplicate_count,
                "top_query_fingerprint": top_fingerprint,
                "top_query_calls": self.fingerprint_calls[top_fingerprint]
                if top_fingerprint
                else 0,
                "top_query_total_ms": round(self.fingerprint_ms[top_fingerprint], 3)
                if top_fingerprint
                else 0.0,
                "top_query_sql": self.fingerprint_sql.get(top_fingerprint),
                "top_query_caller": self.fingerprint_callers.get(top_fingerprint),
                "most_repeated_query_fingerprint": most_repeated,
                "most_repeated_query_calls": self.fingerprint_calls[most_repeated]
                if most_repeated
                else 0,
                "most_repeated_query_total_ms": round(self.fingerprint_ms[most_repeated], 3)
                if most_repeated
                else 0.0,
                "top_expensive_queries": [
                    {
                        "query_fingerprint": fingerprint,
                        "calls": self.fingerprint_calls[fingerprint],
                        "total_ms": round(self.fingerprint_ms[fingerprint], 3),
                        "normalized_sql": self.fingerprint_sql[fingerprint],
                        "python_caller": self.fingerprint_callers.get(fingerprint),
                    }
                    for fingerprint in top_fingerprints
                ],
                "n_plus_one_candidates": [
                    {
                        "query_fingerprint": fingerprint,
                        "calls": self.fingerprint_calls[fingerprint],
                        "total_ms": round(self.fingerprint_ms[fingerprint], 3),
                        "normalized_sql": self.fingerprint_sql[fingerprint],
                        "python_caller": self.fingerprint_callers.get(fingerprint),
                    }
                    for fingerprint in repeated_fingerprints
                ],
                "orm_flush_count": self.flush_count,
                "orm_flush_new": self.flush_new,
                "orm_flush_dirty": self.flush_dirty,
                "orm_flush_deleted": self.flush_deleted,
            }


@dataclass(frozen=True)
class ScopeHandle:
    context_token: Token[ExecutionScope]
    summary_token: Token[SqlSummary]
    context: ExecutionScope
    summary: SqlSummary


_execution_scope: ContextVar[ExecutionScope | None] = ContextVar(
    "db_monitor_execution_scope", default=None
)
_sql_summary: ContextVar[SqlSummary | None] = ContextVar("db_monitor_sql_summary", default=None)


class JsonlTelemetryWriter:
    """Bounded, nonblocking JSONL sink with daily/size rotation and retention."""

    def __init__(
        self,
        log_dir: Path,
        *,
        retention_days: int,
        queue_size: int,
        max_file_mb: int,
        max_files: int = 32,
        max_total_mb: int = 1024,
        flush_interval_seconds: float = 1.0,
    ) -> None:
        self.log_dir = Path(log_dir)
        self.retention_days = max(1, retention_days)
        self.max_file_bytes = max(1, max_file_mb) * 1024 * 1024
        self.max_files = max(2, max_files)
        self.max_total_bytes = max(1, max_total_mb) * 1024 * 1024
        self.flush_interval_seconds = max(0.05, flush_interval_seconds)
        self.queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=max(1, queue_size))
        self.records_written = 0
        self.records_dropped = 0
        self.write_errors = 0
        self.started_at: datetime | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._pending_drop_report = 0
        self._fatal_error = False

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self.started_at = datetime.now(UTC)
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="swinglens-db-monitor-writer",
                daemon=True,
            )
            try:
                self.queue.put_nowait(
                    {
                        "record_type": "monitor_status",
                        "timestamp": self.started_at.isoformat(),
                        "monitor_enabled": True,
                        "monitor_start_time": self.started_at.isoformat(),
                        "monitor_log_directory": str(self.log_dir.resolve()),
                        "records_written": 0,
                        "db_monitor_dropped_records": 0,
                    }
                )
            except queue.Full:
                self.records_dropped += 1
            self._thread.start()

    def enqueue(self, record: dict[str, Any]) -> bool:
        try:
            if self._fatal_error:
                with self._lock:
                    self.records_dropped += 1
                return False
            if self._thread is None or not self._thread.is_alive():
                self.start()
            self.queue.put_nowait(record)
            return True
        except queue.Full:
            with self._lock:
                self.records_dropped += 1
                self._pending_drop_report += 1
            return False
        except Exception:
            with self._lock:
                self.write_errors += 1
                self.records_dropped += 1
                self._pending_drop_report += 1
            return False

    def stop(self, timeout: float = 3.0) -> None:
        thread = self._thread
        if thread is not None and thread.is_alive() and not self._fatal_error:
            try:
                self.queue.put_nowait(
                    {
                        "record_type": "monitor_status",
                        "timestamp": datetime.now(UTC).isoformat(),
                        "monitor_enabled": True,
                        "monitor_start_time": (
                            self.started_at.isoformat() if self.started_at else None
                        ),
                        "monitor_log_directory": str(self.log_dir.resolve()),
                        "records_written": self.records_written,
                        "db_monitor_dropped_records": self.records_dropped,
                        "write_errors": self.write_errors,
                        "monitor_stopping": True,
                    }
                )
            except queue.Full:
                with self._lock:
                    self.records_dropped += 1
        self._stop.set()
        try:
            self.queue.put_nowait(None)
        except queue.Full:
            pass
        if thread is not None:
            thread.join(timeout=timeout)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "monitor_start_time": self.started_at.isoformat() if self.started_at else None,
                "monitor_log_directory": str(self.log_dir.resolve()),
                "records_written": self.records_written,
                "db_monitor_dropped_records": self.records_dropped,
                "write_errors": self.write_errors,
                "queue_depth": self.queue.qsize(),
                "queue_capacity": self.queue.maxsize,
                "writer_alive": bool(self._thread and self._thread.is_alive()),
                "fatal_error": self._fatal_error,
            }

    def _run(self) -> None:
        stream = None
        stream_path: Path | None = None
        next_flush = time.monotonic() + self.flush_interval_seconds
        next_retention = time.monotonic() + 60.0
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            self._apply_retention()
            while not self._stop.is_set() or not self.queue.empty():
                queue_item_received = False
                try:
                    item = self.queue.get(timeout=self.flush_interval_seconds)
                    queue_item_received = True
                except queue.Empty:
                    item = None
                if item is None:
                    if queue_item_received:
                        self.queue.task_done()
                    if stream is not None and time.monotonic() >= next_flush:
                        stream.flush()
                        next_flush = time.monotonic() + self.flush_interval_seconds
                    if time.monotonic() >= next_retention:
                        self._apply_retention(
                            protected_paths={stream_path} if stream_path is not None else None
                        )
                        next_retention = time.monotonic() + 60.0
                    if self._stop.is_set() and self.queue.empty():
                        break
                    continue
                try:
                    desired_path = self._path_for(item)
                    if stream_path != desired_path:
                        if stream is not None:
                            stream.flush()
                            stream.close()
                        desired_path.parent.mkdir(parents=True, exist_ok=True)
                        stream = desired_path.open("a", encoding="utf-8", buffering=64 * 1024)
                        stream_path = desired_path
                        self._apply_retention(protected_paths={stream_path})
                    dropped = self._take_drop_report()
                    if dropped:
                        self._write_line(
                            stream,
                            {
                                "record_type": "monitor_status",
                                "timestamp": datetime.now(UTC).isoformat(),
                                "db_monitor_dropped_records": dropped,
                                "db_monitor_dropped_records_total": self.records_dropped,
                                "records_written": self.records_written,
                            },
                        )
                    self._write_line(stream, item)
                    if stream.tell() >= self.max_file_bytes:
                        stream.flush()
                        stream.close()
                        stream = None
                        stream_path = None
                    if time.monotonic() >= next_flush:
                        stream.flush()
                        next_flush = time.monotonic() + self.flush_interval_seconds
                    if time.monotonic() >= next_retention:
                        self._apply_retention(
                            protected_paths={stream_path} if stream_path is not None else None
                        )
                        next_retention = time.monotonic() + 60.0
                except Exception:
                    with self._lock:
                        self.write_errors += 1
                        self.records_dropped += 1
                        self._pending_drop_report += 1
                    if stream is not None:
                        try:
                            stream.close()
                        except Exception:
                            pass
                    stream = None
                    stream_path = None
                finally:
                    self.queue.task_done()
        except Exception:
            with self._lock:
                self._fatal_error = True
                self.write_errors += 1
            while True:
                try:
                    self.queue.get_nowait()
                except queue.Empty:
                    break
                else:
                    with self._lock:
                        self.records_dropped += 1
                    self.queue.task_done()
        finally:
            if stream is not None:
                try:
                    stream.flush()
                    stream.close()
                except Exception:
                    pass

    def _path_for(self, record: Mapping[str, Any]) -> Path:
        timestamp = str(record.get("timestamp") or datetime.now(UTC).isoformat())
        day = timestamp[:10] if len(timestamp) >= 10 else datetime.now(UTC).date().isoformat()
        process_id = os.getpid()
        base = self.log_dir / f"sql-{day}-p{process_id}.jsonl"
        if not base.exists() or base.stat().st_size < self.max_file_bytes:
            return base
        for part in range(1, 10_000):
            candidate = self.log_dir / f"sql-{day}-p{process_id}.{part}.jsonl"
            if not candidate.exists() or candidate.stat().st_size < self.max_file_bytes:
                return candidate
        return self.log_dir / f"sql-{day}-p{process_id}.{uuid.uuid4().hex}.jsonl"

    def _write_line(self, stream: Any, record: Mapping[str, Any]) -> None:
        stream.write(json.dumps(record, separators=(",", ":"), ensure_ascii=False, default=str))
        stream.write("\n")
        with self._lock:
            self.records_written += 1

    def _take_drop_report(self) -> int:
        with self._lock:
            count = self._pending_drop_report
            self._pending_drop_report = 0
            return count

    def _apply_retention(self, *, protected_paths: set[Path] | None = None) -> None:
        protected = {path.resolve() for path in protected_paths or ()}
        cutoff = datetime.now(UTC) - timedelta(days=self.retention_days)
        for path in self.log_dir.glob("sql-*.jsonl"):
            if path.resolve() in protected:
                continue
            try:
                modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
                if modified < cutoff:
                    path.unlink()
            except OSError:
                continue
        retained: list[tuple[Path, int, float]] = []
        for path in self.log_dir.glob("sql-*.jsonl"):
            try:
                stat = path.stat()
                retained.append((path, stat.st_size, stat.st_mtime))
            except OSError:
                continue
        retained.sort(key=lambda item: (item[2], item[0].name), reverse=True)
        retained_bytes = 0
        for index, (path, size, _modified) in enumerate(retained):
            if path.resolve() in protected:
                retained_bytes += size
                continue
            if index < self.max_files and retained_bytes + size <= self.max_total_bytes:
                retained_bytes += size
                continue
            try:
                path.unlink()
            except OSError:
                continue


class DatabaseMonitor:
    def __init__(self, settings: Any, *, writer: JsonlTelemetryWriter | None = None) -> None:
        self.enabled = bool(settings.db_monitor_enabled)
        self.slow_query_ms = float(settings.db_monitor_slow_query_ms)
        self.full_trace_ms = float(settings.db_monitor_full_trace_ms)
        self.full_stack_for_all_sql = bool(settings.db_monitor_full_stack_for_all_sql)
        self.max_stack_frames = int(settings.db_monitor_max_stack_frames)
        self.n_plus_one_threshold = int(settings.db_monitor_n_plus_one_threshold)
        self.writer = writer or JsonlTelemetryWriter(
            Path(settings.db_monitor_log_dir),
            retention_days=int(settings.db_monitor_retention_days),
            queue_size=int(settings.db_monitor_queue_size),
            max_file_mb=int(settings.db_monitor_max_file_mb),
            max_files=int(settings.db_monitor_max_files),
            max_total_mb=int(settings.db_monitor_max_total_mb),
        )
        self._installed_engines: set[int] = set()

    def install(self, engine: Engine) -> None:
        if not self.enabled or id(engine) in self._installed_engines:
            return
        try:
            event.listen(engine, "before_cursor_execute", self.before_cursor_execute)
            event.listen(engine, "after_cursor_execute", self.after_cursor_execute)
            event.listen(engine, "handle_error", self.handle_error)
        except Exception:
            return
        self._installed_engines.add(id(engine))
        try:
            self.writer.start()
        except Exception:
            # Instrumentation must never become a database availability dependency.
            pass

    def before_cursor_execute(
        self,
        _conn: Any,
        _cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        try:
            if not self.enabled or _monitoring_excluded(context):
                return
            context._swinglens_db_monitor_start_ns = time.perf_counter_ns()
            context._swinglens_db_monitor_caller = application_caller() or _UNKNOWN_CALLER
            context._swinglens_db_monitor_parameter_shape = parameter_shape(parameters, executemany)
            context._swinglens_db_monitor_recorded = False
        except Exception:
            return

    def after_cursor_execute(
        self,
        _conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        try:
            if not self.enabled or _monitoring_excluded(context):
                return
            self._capture(
                cursor=cursor,
                statement=statement,
                parameters=parameters,
                context=context,
                executemany=executemany,
                success=True,
            )
        except Exception:
            return

    def handle_error(self, exception_context: Any) -> None:
        try:
            context = exception_context.execution_context
            if not self.enabled or context is None or _monitoring_excluded(context):
                return
            self._capture(
                cursor=getattr(exception_context, "cursor", None),
                statement=exception_context.statement or "",
                parameters=exception_context.parameters,
                context=context,
                executemany=bool(getattr(context, "executemany", False)),
                success=False,
                error=exception_context.original_exception,
            )
        except Exception:
            return

    def emit(self, record: dict[str, Any]) -> bool:
        if not self.enabled:
            return False
        try:
            return self.writer.enqueue(record)
        except Exception:
            return False

    def status(self) -> dict[str, Any]:
        return {"monitor_enabled": self.enabled, **self.writer.status()}

    def _capture(
        self,
        *,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
        success: bool,
        error: BaseException | None = None,
    ) -> None:
        if getattr(context, "_swinglens_db_monitor_recorded", False):
            return
        context._swinglens_db_monitor_recorded = True
        started_ns = getattr(context, "_swinglens_db_monitor_start_ns", time.perf_counter_ns())
        duration_ms = max(0.0, (time.perf_counter_ns() - started_ns) / 1_000_000)
        normalized = normalize_sql(statement)
        fingerprint = query_fingerprint(normalized)
        operation = sql_operation(normalized)
        caller = (
            getattr(context, "_swinglens_db_monitor_caller", None)
            or application_caller()
            or _UNKNOWN_CALLER
        )
        record: dict[str, Any] = {
            "record_type": "sql",
            "timestamp": datetime.now(UTC).isoformat(),
            "duration_ms": round(duration_ms, 3),
            "slow_query": duration_ms >= self.slow_query_ms,
            "operation": operation,
            "normalized_sql": normalized,
            "query_fingerprint": fingerprint,
            "rowcount": meaningful_rowcount(cursor, operation),
            "executemany": bool(executemany),
            "success": success,
            "parameter_shape": getattr(
                context,
                "_swinglens_db_monitor_parameter_shape",
                parameter_shape(parameters, executemany),
            ),
            "python_caller": caller,
            "thread": {
                "id": threading.get_ident(),
                "name": threading.current_thread().name,
            },
            "process": {
                "id": os.getpid(),
                "hostname": _HOSTNAME,
            },
            **current_scope_fields(),
        }
        if error is not None:
            record["error_type"] = type(error).__name__
            record["error_summary"] = safe_error_summary(error)
        if self.full_stack_for_all_sql or duration_ms >= self.full_trace_ms:
            record["application_stack"] = application_stack(self.max_stack_frames)
        summary = _sql_summary.get()
        if summary is not None:
            summary.add_sql(record)
        self.emit(record)


_global_monitor: DatabaseMonitor | None = None
_global_monitor_lock = threading.Lock()
_shutdown_registered = False


def configure_database_monitor(engine: Engine, settings: Any) -> DatabaseMonitor:
    global _global_monitor, _shutdown_registered
    with _global_monitor_lock:
        if _global_monitor is None:
            _global_monitor = DatabaseMonitor(settings)
        if not _shutdown_registered:
            atexit.register(shutdown_database_monitor)
            _shutdown_registered = True
        monitor = _global_monitor
    monitor.install(engine)
    return monitor


def get_database_monitor() -> DatabaseMonitor | None:
    return _global_monitor


def emit_monitor_record(record: dict[str, Any]) -> bool:
    monitor = get_database_monitor()
    return bool(monitor and monitor.emit(record))


def current_scope_fields() -> dict[str, Any]:
    scope = _execution_scope.get()
    return scope.fields() if scope is not None else {"origin_type": "UNKNOWN"}


def current_sql_summary_snapshot() -> dict[str, Any]:
    summary = _sql_summary.get()
    monitor = get_database_monitor()
    threshold = monitor.n_plus_one_threshold if monitor is not None else 10
    return (
        summary.snapshot(n_plus_one_threshold=threshold)
        if summary is not None
        else SqlSummary().snapshot(
            total_duration_ms=0,
            n_plus_one_threshold=threshold,
        )
    )


def begin_scope(scope: ExecutionScope) -> ScopeHandle:
    summary = SqlSummary()
    return ScopeHandle(
        context_token=_execution_scope.set(scope),
        summary_token=_sql_summary.set(summary),
        context=scope,
        summary=summary,
    )


def finish_scope(
    handle: ScopeHandle,
    *,
    record_type: str,
    total_duration_ms: float,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        monitor = get_database_monitor()
        threshold = monitor.n_plus_one_threshold if monitor is not None else 10
        summary = {
            "record_type": record_type,
            "timestamp": datetime.now(UTC).isoformat(),
            **handle.context.fields(),
            **handle.summary.snapshot(
                total_duration_ms=total_duration_ms,
                n_plus_one_threshold=threshold,
            ),
            **dict(extra or {}),
        }
        emit_monitor_record(summary)
        return summary
    finally:
        _sql_summary.reset(handle.summary_token)
        _execution_scope.reset(handle.context_token)


@contextmanager
def background_job_scope(
    *,
    job_id: int | str | None,
    job_type: str,
    run_id: int | str | None = None,
    worker_id: str | None = None,
    workflow_key: str | None = None,
    ticker: str | None = None,
    company: str | None = None,
) -> Iterator[SqlSummary]:
    monitor = get_database_monitor()
    if monitor is None or not monitor.enabled:
        yield SqlSummary()
        return
    started = time.perf_counter()
    started_at = datetime.now(UTC).isoformat()
    handle = begin_scope(
        ExecutionScope(
            origin_type="BACKGROUND_JOB",
            job_id=job_id,
            job_type=job_type,
            run_id=run_id,
            worker_id=worker_id,
            workflow_key=workflow_key,
            ticker=ticker,
            company=company,
        )
    )
    outcome = "SUCCESS"
    try:
        yield handle.summary
    except BaseException:
        outcome = "FAILURE"
        raise
    finally:
        finish_scope(
            handle,
            record_type="job_summary",
            total_duration_ms=(time.perf_counter() - started) * 1000.0,
            extra={
                "outcome": outcome,
                "job_started_at": started_at,
                "job_finished_at": datetime.now(UTC).isoformat(),
            },
        )


def sanitized_request_id(value: str | None) -> str:
    if value and _SAFE_REQUEST_ID_RE.fullmatch(value):
        return value
    return uuid.uuid4().hex


class DatabaseMonitorMiddleware:
    """Pure ASGI correlation that includes streaming and response background work."""

    def __init__(self, app: ASGIApp, *, enabled: bool = True) -> None:
        self.app = app
        self.enabled = enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.enabled:
            await self.app(scope, receive, send)
            return
        header_value = next(
            (
                value.decode("latin-1")
                for key, value in scope.get("headers", [])
                if key.lower() == b"x-request-id"
            ),
            None,
        )
        request_id = sanitized_request_id(header_value)
        method = str(scope.get("method") or "")
        path = str(scope.get("path") or "")
        started = time.perf_counter()
        started_at = datetime.now(UTC).isoformat()
        handle = begin_scope(
            ExecutionScope(
                origin_type="HTTP",
                request_id=request_id,
                http_method=method,
                http_path=path,
                asgi_scope=scope,
            )
        )
        status_code = 500
        outcome = "FAILURE"

        async def correlated_send(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = list(message.get("headers", []))
                headers = [(key, value) for key, value in headers if key.lower() != b"x-request-id"]
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, correlated_send)
            outcome = "SUCCESS"
        finally:
            finish_scope(
                handle,
                record_type="request_summary",
                total_duration_ms=(time.perf_counter() - started) * 1000.0,
                extra={
                    "status_code": status_code,
                    "outcome": outcome,
                    "request_started_at": started_at,
                    "request_finished_at": datetime.now(UTC).isoformat(),
                },
            )


def normalize_sql(statement: str) -> str:
    value = _SQL_COMMENT_RE.sub(" ", statement or "")
    value = _STRING_LITERAL_RE.sub("?", value)
    value = _PLACEHOLDER_RE.sub("?", value)
    value = _NUMBER_LITERAL_RE.sub("?", value)
    return _WHITESPACE_RE.sub(" ", value).strip()


def query_fingerprint(normalized_sql: str) -> str:
    return hashlib.sha256(normalized_sql.encode("utf-8", errors="replace")).hexdigest()


def sql_operation(normalized_sql: str) -> str:
    match = _OPERATION_RE.match(normalized_sql)
    if not match:
        return "UNKNOWN"
    operation = match.group(1).upper()
    if operation == "WITH":
        upper = normalized_sql.upper()
        for candidate in ("SELECT", "INSERT", "UPDATE", "DELETE", "MERGE"):
            if re.search(rf"\b{candidate}\b", upper):
                return candidate
    return operation


def parameter_shape(parameters: Any, executemany: bool) -> dict[str, Any]:
    """Return cardinality and Python type names without retaining values."""
    try:
        batches = parameters if executemany and isinstance(parameters, (tuple, list)) else None
        sample = batches[0] if batches else parameters
        if isinstance(sample, Mapping):
            types = sorted(type(value).__name__ for value in sample.values())
            count = len(sample)
            shape = "mapping"
        elif isinstance(sample, (tuple, list)):
            types = [type(value).__name__ for value in sample]
            count = len(sample)
            shape = "sequence"
        elif sample is None:
            types = []
            count = 0
            shape = "none"
        else:
            types = [type(sample).__name__]
            count = 1
            shape = "scalar"
        return {
            "shape": shape,
            "parameter_count": count,
            "parameter_types": types[:50],
            "batch_size": len(batches) if batches is not None else 1,
        }
    except Exception:
        return {"shape": "unknown", "parameter_count": None, "parameter_types": []}


def meaningful_rowcount(cursor: Any, operation: str) -> int | None:
    if cursor is None:
        return None
    try:
        value = int(cursor.rowcount)
    except (AttributeError, TypeError, ValueError):
        return None
    if value < 0 or (operation == "SELECT" and value == -1):
        return None
    return value


def safe_error_summary(error: BaseException) -> str:
    if isinstance(error, DBAPIError):
        error = error.orig
    message = str(error).replace("\r", " ").replace("\n", " ")
    message = _STRING_LITERAL_RE.sub("?", message)
    message = _DOUBLE_QUOTED_TEXT_RE.sub("?", message)
    message = _URL_CREDENTIAL_RE.sub(r"\1?\2", message)
    message = _NUMBER_LITERAL_RE.sub("?", message)
    return _WHITESPACE_RE.sub(" ", message).strip()[:500] or type(error).__name__


def application_caller() -> dict[str, Any] | None:
    for frame in _calling_frames():
        if _useful_application_frame(frame):
            return _frame_record(frame)
    return None


def application_stack(limit: int = 20) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    for frame in _calling_frames():
        if _useful_application_frame(frame):
            frames.append(_frame_record(frame))
    frames.reverse()
    return frames[-max(1, limit) :]


def _calling_frames() -> Iterator[FrameType]:
    frame = sys._getframe(2)
    while frame is not None:
        yield frame
        frame = frame.f_back


def _frame_record(frame: FrameType) -> dict[str, Any]:
    _, source_file = _source_file_info(frame.f_code.co_filename)
    module = str(frame.f_globals.get("__name__") or "")
    owner = None
    if "self" in frame.f_locals:
        owner = type(frame.f_locals["self"]).__name__
    function = f"{owner}.{frame.f_code.co_name}" if owner else frame.f_code.co_name
    return {
        "source_file": source_file,
        "line_number": frame.f_lineno,
        "function": function,
        "module": module,
    }


def _useful_application_frame(frame: FrameType) -> bool:
    useful, _ = _source_file_info(frame.f_code.co_filename)
    return useful


@lru_cache(maxsize=4096)
def _source_file_info(filename: str) -> tuple[bool, str]:
    path = Path(os.path.abspath(filename))
    if path == _MONITOR_FILE:
        return False, path.as_posix()
    lowered_parts = {part.lower() for part in path.parts}
    if lowered_parts & _LIBRARY_PATH_PARTS:
        return False, path.as_posix()
    try:
        relative = path.relative_to(_PROJECT_ROOT)
    except ValueError:
        return False, path.as_posix()
    useful = relative.parts[:1] not in {(".venv",), ("tests",)} and relative.suffix == ".py"
    return useful, relative.as_posix()


def _monitoring_excluded(context: Any) -> bool:
    options = getattr(context, "execution_options", {}) or {}
    return bool(options.get("db_monitor_excluded"))


def record_orm_flush(session: Session, _flush_context: Any) -> None:
    try:
        monitor = get_database_monitor()
        summary = _sql_summary.get()
        if monitor is None or not monitor.enabled or summary is None:
            return
        counts = {
            "new": len(session.new),
            "dirty": len(session.dirty),
            "deleted": len(session.deleted),
        }
        summary.add_flush(**counts)
        monitor.emit(
            {
                "record_type": "orm_flush",
                "timestamp": datetime.now(UTC).isoformat(),
                **counts,
                **current_scope_fields(),
                "python_caller": application_caller(),
            }
        )
    except Exception:
        return


class DatabaseHealthSampler:
    """Optional pg_stat_activity sampler using a separate, uninstrumented engine."""

    def __init__(self, settings: Any) -> None:
        self.enabled = bool(settings.db_monitor_activity_sampler_enabled)
        self.database_url = str(settings.database_url)
        self.interval_seconds = float(settings.db_monitor_activity_sample_interval_seconds)
        self.threshold_ms = float(settings.db_monitor_activity_threshold_ms)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._engine: Engine | None = None

    def start(self) -> None:
        if not self.enabled or not self.database_url.startswith("postgresql"):
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="swinglens-db-health-sampler",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None

    def sample_once(self) -> list[dict[str, Any]]:
        from sqlalchemy import create_engine

        if self._engine is None:
            self._engine = create_engine(
                self.database_url,
                pool_pre_ping=True,
                pool_size=1,
                max_overflow=0,
                connect_args={
                    "connect_timeout": 3,
                    "application_name": "swinglens-db-monitor",
                },
            )
        with self._engine.connect().execution_options(db_monitor_excluded=True) as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT pid,
                           EXTRACT(EPOCH FROM (clock_timestamp() - query_start)) * 1000
                               AS duration_ms,
                           state, wait_event_type, wait_event,
                           pg_blocking_pids(pid) AS blocking_pids,
                           query
                    FROM pg_stat_activity
                    WHERE datname = current_database()
                      AND pid <> pg_backend_pid()
                      AND state <> 'idle'
                      AND query_start IS NOT NULL
                      AND clock_timestamp() - query_start
                          >= (:threshold_ms * interval '1 millisecond')
                    ORDER BY query_start
                    """
                ),
                {"threshold_ms": self.threshold_ms},
            ).mappings()
            records = []
            for row in rows:
                normalized = normalize_sql(str(row["query"] or ""))
                record = {
                    "record_type": "database_health",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "pid": row["pid"],
                    "duration_ms": round(float(row["duration_ms"] or 0), 3),
                    "state": row["state"],
                    "wait_event_type": row["wait_event_type"],
                    "wait_event": row["wait_event"],
                    "blocking_pids": list(row["blocking_pids"] or []),
                    "normalized_sql": normalized,
                    "query_fingerprint": query_fingerprint(normalized),
                }
                emit_monitor_record(record)
                records.append(record)
            return records

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.sample_once()
            except Exception as exc:
                emit_monitor_record(
                    {
                        "record_type": "database_health_error",
                        "timestamp": datetime.now(UTC).isoformat(),
                        "error_type": type(exc).__name__,
                        "error_summary": safe_error_summary(exc),
                    }
                )


def install_session_flush_monitor() -> None:
    if not event.contains(Session, "after_flush", record_orm_flush):
        event.listen(Session, "after_flush", record_orm_flush)


def shutdown_database_monitor() -> None:
    monitor = get_database_monitor()
    if monitor is not None and monitor.enabled:
        monitor.writer.stop()


def python_runtime_metadata() -> dict[str, Any]:
    return {"python_version": sys.version.split()[0], "process_id": os.getpid()}
