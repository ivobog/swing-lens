from __future__ import annotations

import atexit
import hashlib
import hmac
import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from types import FrameType
from typing import Any

from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.orm import Session
from sqlalchemy.pool import QueuePool
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
_IN_PLACEHOLDERS_RE = re.compile(
    r"\bIN\s*\(\s*\?(?:\s*,\s*\?)*\s*\)",
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
_PARAMETER_DIGEST_KEY = os.urandom(32)
_PROCESS_STARTED_AT = datetime.now(UTC).isoformat()

TELEMETRY_P0 = "P0"
TELEMETRY_P1 = "P1"
TELEMETRY_P2 = "P2"
_TELEMETRY_PRIORITIES = (TELEMETRY_P0, TELEMETRY_P1, TELEMETRY_P2)
_P0_RECORD_TYPES = {
    "request_summary",
    "job_summary",
    "monitor_health",
    "monitor_status",
    "telemetry_drop_summary",
    "long_transaction",
    "database_health_error",
}


def telemetry_priority(record: Mapping[str, Any]) -> str:
    """Classify evidence by durability value without inspecting parameter values."""
    explicit = str(record.get("telemetry_priority") or "").upper()
    if explicit in _TELEMETRY_PRIORITIES:
        return explicit
    record_type = str(record.get("record_type") or "")
    if record_type in _P0_RECORD_TYPES or "ERROR" in record_type.upper():
        return TELEMETRY_P0
    if "AUDIT" in record_type.upper() or "DEPLOYMENT" in record_type.upper():
        return TELEMETRY_P0
    if record_type == "sql":
        if record.get("success") is False or float(record.get("duration_ms") or 0) >= 1000.0:
            return TELEMETRY_P0
        if record.get("slow_query") or record.get("application_stack"):
            return TELEMETRY_P1
        return TELEMETRY_P2
    if record_type == "pool_checkout":
        return (
            TELEMETRY_P0
            if record.get("pool_timeout") or record.get("pool_overflow")
            else TELEMETRY_P1
        )
    if record_type == "database_health":
        return (
            TELEMETRY_P0
            if record.get("wait_event_type") == "Lock" or record.get("blocking_pids")
            else TELEMETRY_P1
        )
    if record_type == "orm_flush":
        return TELEMETRY_P2
    return TELEMETRY_P1


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
    attempt: int | None = None
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
                "attempt": self.attempt,
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
    pool_checkout_count: int = 0
    pool_timeout_count: int = 0
    pool_overflow_count: int = 0
    pool_wait_total_ms: float = 0.0
    pool_wait_max_ms: float = 0.0
    transaction_count: int = 0
    transaction_total_ms: float = 0.0
    transaction_max_ms: float = 0.0
    phase_ms: defaultdict[str, float] = field(default_factory=lambda: defaultdict(float))
    phase_counts: Counter[str] = field(default_factory=Counter)
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

    def add_pool_checkout(self, *, wait_ms: float, overflow: bool, timed_out: bool) -> None:
        with self._lock:
            self.pool_checkout_count += 0 if timed_out else 1
            self.pool_timeout_count += int(timed_out)
            self.pool_overflow_count += int(overflow)
            self.pool_wait_total_ms += wait_ms
            self.pool_wait_max_ms = max(self.pool_wait_max_ms, wait_ms)

    def add_transaction(self, duration_ms: float) -> None:
        with self._lock:
            self.transaction_count += 1
            self.transaction_total_ms += duration_ms
            self.transaction_max_ms = max(self.transaction_max_ms, duration_ms)

    def add_phase(self, name: str, duration_ms: float) -> None:
        with self._lock:
            self.phase_counts[name] += 1
            self.phase_ms[name] += duration_ms

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
                "pool_checkout_count": self.pool_checkout_count,
                "pool_timeout_count": self.pool_timeout_count,
                "pool_overflow_count": self.pool_overflow_count,
                "pool_wait_total_ms": round(self.pool_wait_total_ms, 3),
                "pool_wait_mean_ms": round(self.pool_wait_total_ms / self.pool_checkout_count, 3)
                if self.pool_checkout_count
                else 0.0,
                "pool_wait_max_ms": round(self.pool_wait_max_ms, 3),
                "transaction_count": self.transaction_count,
                "transaction_total_ms": round(self.transaction_total_ms, 3),
                "transaction_max_ms": round(self.transaction_max_ms, 3),
                "job_phases": {
                    name: {
                        "count": self.phase_counts[name],
                        "total_ms": round(duration, 3),
                    }
                    for name, duration in sorted(self.phase_ms.items())
                },
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


def resolve_process_role(configured_role: str | None = None) -> str:
    configured = str(configured_role or "auto").strip().lower()
    if configured and configured != "auto":
        return re.sub(r"[^a-z0-9_-]+", "-", configured).strip("-") or "unknown"
    argv = " ".join(sys.argv).lower().replace("\\", "/")
    if "pytest" in argv or os.getenv("PYTEST_CURRENT_TEST"):
        return "test"
    if "app.worker_supervisor" in argv or "/app/worker_supervisor.py" in argv:
        return "supervisor"
    if "app.worker" in argv or "/app/worker.py" in argv:
        return "worker"
    if "app.serve" in argv or "/app/serve.py" in argv or "uvicorn" in argv:
        return "web"
    if (
        argv.startswith("scripts/")
        or "/scripts/" in argv
        or "diagnostic" in argv
        or "profile" in argv
    ):
        return "diagnostic"
    return "cli"


@lru_cache(maxsize=1)
def _git_metadata() -> dict[str, Any]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=no"],
                cwd=_PROJECT_ROOT,
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            ).stdout.strip()
        )
        return {"git_commit": commit or None, "git_dirty": dirty}
    except Exception:
        return {"git_commit": os.getenv("GIT_COMMIT"), "git_dirty": None}


def process_metadata(settings: Any, process_role: str) -> dict[str, Any]:
    return {
        "process_role": process_role,
        "process_id": os.getpid(),
        "hostname": _HOSTNAME,
        "process_started_at": _PROCESS_STARTED_AT,
        "application_version": getattr(settings, "application_version", None),
        "deployment_id": getattr(settings, "deployment_id", None),
        "feature_rebuild_impl_version": getattr(
            settings, "ceri_feature_rebuild_impl_version", None
        ),
        **_git_metadata(),
    }


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
        critical_queue_size: int = 2048,
        high_queue_size: int = 4096,
        p2_pressure_ratio: float = 0.75,
        aggregate_p2_on_pressure: bool = True,
        flush_interval_seconds: float = 1.0,
        health_interval_seconds: float = 60.0,
        base_metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self.log_dir = Path(log_dir)
        self.retention_days = max(1, retention_days)
        self.max_file_bytes = max(1, max_file_mb) * 1024 * 1024
        self.max_files = max(2, max_files)
        self.max_total_bytes = max(1, max_total_mb) * 1024 * 1024
        self.flush_interval_seconds = max(0.05, flush_interval_seconds)
        self.health_interval_seconds = max(5.0, health_interval_seconds)
        self.base_metadata = dict(base_metadata or {})
        # P0/P1 queues are physically separate from routine SQL so a heartbeat flood
        # cannot consume the capacity reserved for summaries and incident evidence.
        self.p0_queue: queue.Queue[dict[str, Any]] = queue.Queue(
            maxsize=max(1, critical_queue_size)
        )
        self.p1_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=max(1, high_queue_size))
        self.queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=max(1, queue_size))
        self.p2_pressure_ratio = min(1.0, max(0.1, float(p2_pressure_ratio)))
        self.aggregate_p2_on_pressure = bool(aggregate_p2_on_pressure)
        self.records_written = 0
        self.records_dropped = 0
        self.write_errors = 0
        self.started_at: datetime | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._pending_drop_report = 0
        self._fatal_error = False
        self._writer_latency_total_ms = 0.0
        self._writer_latency_max_ms = 0.0
        self._writer_latency_samples = 0
        self._queue_wakeup = threading.Event()
        self._priority_counts: Counter[str] = Counter()
        self._queue_high_water: Counter[str] = Counter()
        self._p2_aggregates: dict[tuple[Any, ...], dict[str, Any]] = {}
        self._p2_aggregate_limit = 2048

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
            self._thread.start()
            started_at = self.started_at
        self.enqueue(
            {
                "record_type": "monitor_status",
                "timestamp": started_at.isoformat(),
                "monitor_enabled": True,
                "monitor_start_time": started_at.isoformat(),
                "monitor_log_directory": str(self.log_dir.resolve()),
                "records_written": 0,
                "db_monitor_dropped_records": 0,
                **self.base_metadata,
            }
        )

    def enqueue(self, record: dict[str, Any]) -> bool:
        priority = telemetry_priority(record)
        try:
            with self._lock:
                self._priority_counts[f"{priority}_created"] += 1
            if self._fatal_error:
                with self._lock:
                    self.records_dropped += 1
                    self._priority_counts[f"{priority}_dropped"] += 1
                return False
            if self._thread is None or not self._thread.is_alive():
                self.start()
            queued = dict(record)
            queued["telemetry_priority"] = priority
            queued["_telemetry_enqueued_ns"] = time.perf_counter_ns()
            target = self._queue_for(priority)
            if priority == TELEMETRY_P2 and self._p2_under_pressure():
                return self._shed_p2(queued)
            if priority == TELEMETRY_P0:
                target.put(queued, timeout=0.05)
            else:
                target.put_nowait(queued)
            self._record_queue_high_water(priority, target.qsize())
            self._queue_wakeup.set()
            return True
        except queue.Full:
            if priority == TELEMETRY_P2 and self.aggregate_p2_on_pressure:
                return self._shed_p2(queued)
            with self._lock:
                self.records_dropped += 1
                self._pending_drop_report += 1
                self._priority_counts[f"{priority}_dropped"] += 1
            return False
        except Exception:
            with self._lock:
                self.write_errors += 1
                self.records_dropped += 1
                self._pending_drop_report += 1
                self._priority_counts[f"{priority}_dropped"] += 1
            return False

    def stop(self, timeout: float = 3.0) -> None:
        thread = self._thread
        if thread is not None and thread.is_alive() and not self._fatal_error:
            self.enqueue(
                {
                    "record_type": "monitor_status",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "monitor_enabled": True,
                    "monitor_start_time": self.started_at.isoformat() if self.started_at else None,
                    "monitor_log_directory": str(self.log_dir.resolve()),
                    "records_written": self.records_written,
                    "db_monitor_dropped_records": self.records_dropped,
                    "write_errors": self.write_errors,
                    "monitor_stopping": True,
                    **self.base_metadata,
                }
            )
        self._stop.set()
        self._queue_wakeup.set()
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
                "queue_total_depth": self._queue_depth(),
                "queue_total_capacity": self._queue_capacity(),
                **self._priority_snapshot(),
                "writer_alive": bool(self._thread and self._thread.is_alive()),
                "fatal_error": self._fatal_error,
                "writer_latency_mean_ms": round(
                    self._writer_latency_total_ms / self._writer_latency_samples, 3
                )
                if self._writer_latency_samples
                else 0.0,
                "writer_latency_max_ms": round(self._writer_latency_max_ms, 3),
            }

    def _run(self) -> None:
        stream = None
        stream_path: Path | None = None
        next_flush = time.monotonic() + self.flush_interval_seconds
        next_retention = time.monotonic() + 60.0
        next_health = time.monotonic() + self.health_interval_seconds
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            self._apply_retention()
            while not self._stop.is_set() or not self._queues_empty() or self._has_aggregates():
                source_queue, item = self._next_item()
                if item is None:
                    if stream is not None and time.monotonic() >= next_flush:
                        self._write_p2_aggregates(stream)
                        stream.flush()
                        next_flush = time.monotonic() + self.flush_interval_seconds
                    if time.monotonic() >= next_retention:
                        self._apply_retention(
                            protected_paths={stream_path} if stream_path is not None else None
                        )
                        next_retention = time.monotonic() + 60.0
                    if stream is not None and time.monotonic() >= next_health:
                        self._write_line(stream, self._health_record())
                        next_health = time.monotonic() + self.health_interval_seconds
                    if self._stop.is_set() and self._queues_empty() and not self._has_aggregates():
                        break
                    continue
                try:
                    enqueued_ns = item.pop("_telemetry_enqueued_ns", None)
                    if enqueued_ns is not None:
                        latency_ms = max(
                            0.0, (time.perf_counter_ns() - int(enqueued_ns)) / 1_000_000
                        )
                        with self._lock:
                            self._writer_latency_total_ms += latency_ms
                            self._writer_latency_max_ms = max(
                                self._writer_latency_max_ms, latency_ms
                            )
                            self._writer_latency_samples += 1
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
                                **self.base_metadata,
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
                    if time.monotonic() >= next_health:
                        self._write_p2_aggregates(stream)
                        self._write_line(stream, self._health_record())
                        next_health = time.monotonic() + self.health_interval_seconds
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
                    if source_queue is not None:
                        source_queue.task_done()
        except Exception:
            with self._lock:
                self._fatal_error = True
                self.write_errors += 1
            while True:
                source_queue, item = self._next_item(wait=False)
                if item is None:
                    break
                priority = telemetry_priority(item)
                with self._lock:
                    self.records_dropped += 1
                    self._priority_counts[f"{priority}_dropped"] += 1
                if source_queue is not None:
                    source_queue.task_done()
        finally:
            if stream is not None:
                try:
                    self._write_p2_aggregates(stream)
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
        public_record = {key: value for key, value in record.items() if not key.startswith("_")}
        stream.write(
            json.dumps(public_record, separators=(",", ":"), ensure_ascii=False, default=str)
        )
        stream.write("\n")
        with self._lock:
            self.records_written += 1
            if not record.get("_telemetry_internal_no_count"):
                priority = telemetry_priority(record)
                self._priority_counts[f"{priority}_written"] += 1

    def _health_record(self) -> dict[str, Any]:
        files = []
        for pattern in ("sql-*.jsonl", "sql-*.jsonl.gz"):
            for path in self.log_dir.glob(pattern):
                try:
                    stat = path.stat()
                    files.append((path, stat.st_size, stat.st_mtime))
                except OSError:
                    continue
        oldest = min((item[2] for item in files), default=None)
        newest = max((item[2] for item in files), default=None)
        oldest_queue_age_ms = self._oldest_queue_age_ms()
        with self._lock:
            latency_mean = (
                self._writer_latency_total_ms / self._writer_latency_samples
                if self._writer_latency_samples
                else 0.0
            )
            return {
                "record_type": "monitor_health",
                "timestamp": datetime.now(UTC).isoformat(),
                "records_written": self.records_written,
                "records_dropped": self.records_dropped,
                "writer_errors": self.write_errors,
                "telemetry_queue_depth": self.queue.qsize(),
                "telemetry_queue_capacity": self.queue.maxsize,
                "telemetry_queue_total_depth": self._queue_depth(),
                "telemetry_queue_total_capacity": self._queue_capacity(),
                **self._priority_snapshot(),
                "writer_latency_mean_ms": round(latency_mean, 3),
                "writer_latency_max_ms": round(self._writer_latency_max_ms, 3),
                "oldest_queue_age_ms": oldest_queue_age_ms,
                "current_files": len(files),
                "current_bytes": sum(item[1] for item in files),
                "oldest_retained_timestamp": datetime.fromtimestamp(oldest, UTC).isoformat()
                if oldest is not None
                else None,
                "newest_retained_timestamp": datetime.fromtimestamp(newest, UTC).isoformat()
                if newest is not None
                else None,
                "retention_days": self.retention_days,
                "max_files": self.max_files,
                "max_total_bytes": self.max_total_bytes,
                **self.base_metadata,
            }

    def _oldest_queue_age_ms(self) -> float:
        try:
            candidates: list[int] = []
            for queued_items in (self.p0_queue, self.p1_queue, self.queue):
                with queued_items.mutex:
                    candidates.extend(
                        int(item["_telemetry_enqueued_ns"])
                        for item in queued_items.queue
                        if isinstance(item, Mapping) and item.get("_telemetry_enqueued_ns")
                    )
            oldest = min(candidates, default=None)
            return (
                round(max(0.0, (time.perf_counter_ns() - int(oldest)) / 1_000_000), 3)
                if oldest is not None
                else 0.0
            )
        except Exception:
            return 0.0

    def _queue_for(self, priority: str) -> queue.Queue[dict[str, Any]]:
        if priority == TELEMETRY_P0:
            return self.p0_queue
        if priority == TELEMETRY_P1:
            return self.p1_queue
        return self.queue

    def _queue_depth(self) -> int:
        return self.p0_queue.qsize() + self.p1_queue.qsize() + self.queue.qsize()

    def _queue_capacity(self) -> int:
        return self.p0_queue.maxsize + self.p1_queue.maxsize + self.queue.maxsize

    def _queues_empty(self) -> bool:
        return self.p0_queue.empty() and self.p1_queue.empty() and self.queue.empty()

    def _next_item(
        self, *, wait: bool = True
    ) -> tuple[queue.Queue[dict[str, Any]] | None, dict[str, Any] | None]:
        for candidate in (self.p0_queue, self.p1_queue, self.queue):
            try:
                return candidate, candidate.get_nowait()
            except queue.Empty:
                continue
        if not wait:
            return None, None
        self._queue_wakeup.wait(self.flush_interval_seconds)
        self._queue_wakeup.clear()
        for candidate in (self.p0_queue, self.p1_queue, self.queue):
            try:
                return candidate, candidate.get_nowait()
            except queue.Empty:
                continue
        return None, None

    def _record_queue_high_water(self, priority: str, depth: int) -> None:
        with self._lock:
            self._queue_high_water[priority] = max(self._queue_high_water[priority], depth)

    def _p2_under_pressure(self) -> bool:
        return self.queue.qsize() / self.queue.maxsize >= self.p2_pressure_ratio

    def _shed_p2(self, record: Mapping[str, Any]) -> bool:
        if self.aggregate_p2_on_pressure and record.get("record_type") == "sql":
            caller = record.get("python_caller") or {}
            bucket = int(time.time() // 10) * 10
            process = record.get("process") or {}
            key = (
                record.get("query_fingerprint"),
                record.get("process_role") or process.get("role"),
                caller.get("source_file"),
                caller.get("function"),
                bucket,
            )
            with self._lock:
                aggregate = self._p2_aggregates.get(key)
                if aggregate is None and len(self._p2_aggregates) < self._p2_aggregate_limit:
                    duration_ms = float(record.get("duration_ms") or 0)
                    aggregate = {
                        "record_type": "sql_aggregate",
                        "telemetry_priority": TELEMETRY_P2,
                        "timestamp": record.get("timestamp") or datetime.now(UTC).isoformat(),
                        "bucket_start_epoch": bucket,
                        "bucket_seconds": 10,
                        "query_fingerprint": record.get("query_fingerprint"),
                        "normalized_sql": record.get("normalized_sql"),
                        "operation": record.get("operation"),
                        "process_role": key[1],
                        "python_caller": caller,
                        "calls": 0,
                        "total_ms": 0.0,
                        "min_ms": duration_ms,
                        "max_ms": duration_ms,
                        "_telemetry_internal_no_count": True,
                    }
                    self._p2_aggregates[key] = aggregate
                if aggregate is not None:
                    duration_ms = float(record.get("duration_ms") or 0)
                    aggregate["calls"] += 1
                    aggregate["total_ms"] += duration_ms
                    aggregate["min_ms"] = min(float(aggregate["min_ms"]), duration_ms)
                    aggregate["max_ms"] = max(float(aggregate["max_ms"]), duration_ms)
                    self._priority_counts[f"{TELEMETRY_P2}_aggregated"] += 1
                    return True
                self._priority_counts[f"{TELEMETRY_P2}_sampled"] += 1
                return True
        with self._lock:
            self._priority_counts[f"{TELEMETRY_P2}_sampled"] += 1
        return True

    def _has_aggregates(self) -> bool:
        with self._lock:
            return bool(self._p2_aggregates)

    def _write_p2_aggregates(self, stream: Any) -> None:
        with self._lock:
            aggregates = list(self._p2_aggregates.values())
            self._p2_aggregates.clear()
        for aggregate in aggregates:
            calls = max(1, int(aggregate["calls"]))
            aggregate["total_ms"] = round(float(aggregate["total_ms"]), 3)
            aggregate["mean_ms"] = round(float(aggregate["total_ms"]) / calls, 3)
            aggregate["min_ms"] = round(float(aggregate["min_ms"]), 3)
            aggregate["max_ms"] = round(float(aggregate["max_ms"]), 3)
            self._write_line(stream, aggregate)

    def _priority_snapshot(self) -> dict[str, int]:
        snapshot: dict[str, int] = {}
        for priority in _TELEMETRY_PRIORITIES:
            lowered = priority.lower()
            for action in ("created", "written", "dropped", "sampled", "aggregated"):
                snapshot[f"{lowered}_{action}"] = self._priority_counts[
                    f"{priority}_{action}"
                ]
            priority_queue = self._queue_for(priority)
            snapshot[f"{lowered}_queue_depth"] = priority_queue.qsize()
            snapshot[f"{lowered}_queue_capacity"] = priority_queue.maxsize
            snapshot[f"{lowered}_queue_high_watermark"] = self._queue_high_water[priority]
        return snapshot

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
        self.parameter_digest_enabled = bool(
            getattr(settings, "db_monitor_parameter_digest_enabled", True)
        )
        self.long_transaction_ms = float(
            getattr(settings, "db_monitor_long_transaction_ms", 5000.0)
        )
        self.pool_wait_event_ms = float(getattr(settings, "db_monitor_pool_wait_event_ms", 5.0))
        self.process_role = resolve_process_role(
            getattr(settings, "db_monitor_process_role", "auto")
        )
        self.base_metadata = process_metadata(settings, self.process_role)
        root_log_dir = Path(
            getattr(settings, "db_monitor_test_log_dir", "logs/db-monitor-test")
            if self.process_role == "test"
            else settings.db_monitor_log_dir
        )
        role_log_dir = root_log_dir / self.process_role
        self.writer = writer or JsonlTelemetryWriter(
            role_log_dir,
            retention_days=int(settings.db_monitor_retention_days),
            queue_size=int(settings.db_monitor_queue_size),
            max_file_mb=int(settings.db_monitor_max_file_mb),
            max_files=int(getattr(settings, "db_monitor_max_files", 512)),
            max_total_mb=int(getattr(settings, "db_monitor_max_total_mb", 8192)),
            critical_queue_size=int(
                getattr(settings, "db_monitor_critical_queue_size", 2048)
            ),
            high_queue_size=int(getattr(settings, "db_monitor_high_queue_size", 4096)),
            p2_pressure_ratio=float(
                getattr(settings, "db_monitor_p2_pressure_ratio", 0.75)
            ),
            aggregate_p2_on_pressure=bool(
                getattr(settings, "db_monitor_aggregate_p2_on_pressure", True)
            ),
            health_interval_seconds=float(
                getattr(settings, "db_monitor_health_interval_seconds", 60.0)
            ),
            base_metadata=self.base_metadata,
        )
        self._installed_engines: set[int] = set()

    def install(self, engine: Engine) -> None:
        if not self.enabled or id(engine) in self._installed_engines:
            return
        try:
            event.listen(engine, "before_cursor_execute", self.before_cursor_execute)
            event.listen(engine, "after_cursor_execute", self.after_cursor_execute)
            event.listen(engine, "handle_error", self.handle_error)
            event.listen(engine, "begin", self.transaction_begin)
            event.listen(engine, "commit", self.transaction_end)
            event.listen(engine, "rollback", self.transaction_end)
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
            context._swinglens_db_monitor_caller = application_caller() or _UNKNOWN_CALLER
            context._swinglens_db_monitor_parameter_shape = parameter_shape(parameters, executemany)
            context._swinglens_db_monitor_parameter_digest = (
                parameter_digest(parameters, executemany) if self.parameter_digest_enabled else None
            )
            context._swinglens_db_monitor_start_ns = time.perf_counter_ns()
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
            enriched = {**self.base_metadata, **record}
            enriched.setdefault(
                "process",
                {
                    "id": os.getpid(),
                    "hostname": _HOSTNAME,
                    "role": self.process_role,
                },
            )
            return self.writer.enqueue(enriched)
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
            "parameter_digest": getattr(context, "_swinglens_db_monitor_parameter_digest", None),
            "python_caller": caller,
            "thread": {
                "id": threading.get_ident(),
                "name": threading.current_thread().name,
            },
            "process": {
                "id": os.getpid(),
                "hostname": _HOSTNAME,
                "role": self.process_role,
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

    def transaction_begin(self, connection: Any) -> None:
        try:
            connection.info.setdefault("swinglens_transaction_stack", []).append(
                {
                    "started_ns": time.perf_counter_ns(),
                    "transaction_start": datetime.now(UTC).isoformat(),
                    "scope": current_scope_fields(),
                    "summary": _sql_summary.get(),
                }
            )
        except Exception:
            return

    def transaction_end(self, connection: Any) -> None:
        try:
            stack = connection.info.get("swinglens_transaction_stack") or []
            if not stack:
                return
            transaction = stack.pop()
            duration_ms = max(
                0.0,
                (time.perf_counter_ns() - int(transaction["started_ns"])) / 1_000_000,
            )
            summary = transaction.get("summary")
            if summary is not None:
                summary.add_transaction(duration_ms)
            if duration_ms >= self.long_transaction_ms:
                self.emit(
                    {
                        "record_type": "long_transaction",
                        "timestamp": datetime.now(UTC).isoformat(),
                        "transaction_start": transaction["transaction_start"],
                        "transaction_end": datetime.now(UTC).isoformat(),
                        "duration_ms": round(duration_ms, 3),
                        **dict(transaction.get("scope") or {}),
                    }
                )
        except Exception:
            return


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
    attempt: int | None = None,
    ticker: str | None = None,
    company: str | None = None,
    job_status_getter: Callable[[], str | None] | None = None,
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
            attempt=attempt,
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
        final_job_status = None
        if job_status_getter is not None:
            try:
                final_job_status = job_status_getter()
            except Exception:
                final_job_status = None
        finish_scope(
            handle,
            record_type="job_summary",
            total_duration_ms=(time.perf_counter() - started) * 1000.0,
            extra={
                "outcome": outcome,
                "job_started_at": started_at,
                "job_finished_at": datetime.now(UTC).isoformat(),
                "job_status_at_scope_end": final_job_status,
            },
        )


@contextmanager
def job_phase(name: str, **extra: Any) -> Iterator[None]:
    started = time.perf_counter()
    try:
        yield
    finally:
        duration_ms = max(0.0, (time.perf_counter() - started) * 1000.0)
        summary = _sql_summary.get()
        if summary is not None:
            summary.add_phase(name, duration_ms)
        emit_monitor_record(
            {
                "record_type": "job_phase",
                "timestamp": datetime.now(UTC).isoformat(),
                "phase": name,
                "duration_ms": round(duration_ms, 3),
                **current_scope_fields(),
                **extra,
            }
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
    value = _WHITESPACE_RE.sub(" ", value).strip()
    return _IN_PLACEHOLDERS_RE.sub("IN (?*)", value)


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


def parameter_digest(parameters: Any, executemany: bool) -> str | None:
    """Keyed, process-local equality token; parameter values and key are never persisted."""
    if parameters is None or executemany:
        return None
    try:
        encoded = json.dumps(
            _digestable_parameter_value(parameters),
            separators=(",", ":"),
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8", errors="replace")
        if len(encoded) > 64 * 1024:
            return None
        return hmac.new(_PARAMETER_DIGEST_KEY, encoded, hashlib.sha256).hexdigest()
    except Exception:
        return None


def _digestable_parameter_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _digestable_parameter_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_digestable_parameter_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return [type(value).__name__, value]
    if isinstance(value, (datetime, date)):
        return [type(value).__name__, value.isoformat()]
    if isinstance(value, bytes):
        return ["bytes", hashlib.sha256(value).hexdigest()]
    return [type(value).__name__, str(value)]


def record_pool_checkout(*, wait_ms: float, overflow: bool, timed_out: bool) -> None:
    summary = _sql_summary.get()
    if summary is not None:
        summary.add_pool_checkout(
            wait_ms=wait_ms,
            overflow=overflow,
            timed_out=timed_out,
        )
    monitor = get_database_monitor()
    threshold = monitor.pool_wait_event_ms if monitor is not None else 5.0
    if timed_out or wait_ms >= threshold:
        emit_monitor_record(
            {
                "record_type": "pool_checkout",
                "timestamp": datetime.now(UTC).isoformat(),
                "pool_wait_ms": round(wait_ms, 3),
                "pool_timeout": timed_out,
                "pool_overflow": overflow,
                **current_scope_fields(),
            }
        )


class MonitoredQueuePool(QueuePool):
    """QueuePool with passive acquisition timing; sizing and timeout semantics are unchanged."""

    def _do_get(self) -> Any:
        started = time.perf_counter_ns()
        try:
            connection = super()._do_get()
        except SQLAlchemyTimeoutError:
            record_pool_checkout(
                wait_ms=(time.perf_counter_ns() - started) / 1_000_000,
                overflow=False,
                timed_out=True,
            )
            raise
        wait_ms = (time.perf_counter_ns() - started) / 1_000_000
        record_pool_checkout(
            wait_ms=wait_ms,
            overflow=self.overflow() > 0,
            timed_out=False,
        )
        return connection


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
        self.idle_transaction_threshold_ms = float(
            getattr(settings, "db_monitor_idle_transaction_threshold_ms", 5000.0)
        )
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
                           EXTRACT(EPOCH FROM (
                               clock_timestamp() - COALESCE(xact_start, query_start)
                           )) * 1000
                               AS duration_ms,
                           EXTRACT(EPOCH FROM (clock_timestamp() - xact_start)) * 1000
                               AS transaction_duration_ms,
                           state, wait_event_type, wait_event, application_name,
                           pg_blocking_pids(pid) AS blocking_pids,
                           query
                    FROM pg_stat_activity
                    WHERE datname = current_database()
                      AND pid <> pg_backend_pid()
                      AND (
                          (state = 'active' AND query_start IS NOT NULL
                           AND clock_timestamp() - query_start
                               >= (:threshold_ms * interval '1 millisecond'))
                          OR wait_event_type = 'Lock'
                          OR cardinality(pg_blocking_pids(pid)) > 0
                          OR (state = 'idle in transaction' AND xact_start IS NOT NULL
                              AND clock_timestamp() - xact_start
                                  >= (:idle_transaction_threshold_ms * interval '1 millisecond'))
                      )
                    ORDER BY COALESCE(xact_start, query_start)
                    """
                ),
                {
                    "threshold_ms": self.threshold_ms,
                    "idle_transaction_threshold_ms": self.idle_transaction_threshold_ms,
                },
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
                    "transaction_duration_ms": round(float(row["transaction_duration_ms"] or 0), 3),
                    "wait_event_type": row["wait_event_type"],
                    "wait_event": row["wait_event"],
                    "application_name": row["application_name"],
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
