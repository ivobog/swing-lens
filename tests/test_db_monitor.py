from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import StaticPool

from app.main import create_app
from app.models.tables import BackgroundJob
from app.observability import db_monitor as monitor_module
from app.observability.db_monitor import (
    DatabaseMonitor,
    ExecutionScope,
    JsonlTelemetryWriter,
    MonitoredQueuePool,
    begin_scope,
    current_scope_fields,
    finish_scope,
    normalize_sql,
    parameter_digest,
    resolve_process_role,
    safe_error_summary,
)
from app.services.background_job_service import JobStatus
from app.services.background_worker import execute_job
from app.settings import Settings


class MemoryWriter:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def start(self) -> None:
        return None

    def enqueue(self, record: dict[str, Any]) -> bool:
        self.records.append(record)
        return True

    def status(self) -> dict[str, Any]:
        return {"records_written": len(self.records)}


class FailingWriter(MemoryWriter):
    def enqueue(self, record: dict[str, Any]) -> bool:
        raise OSError("diagnostic disk unavailable")


def _monitor(
    writer: MemoryWriter | None = None,
    *,
    full_trace_ms: float = 250,
    full_stack_for_all: bool = False,
) -> DatabaseMonitor:
    settings = SimpleNamespace(
        db_monitor_enabled=True,
        db_monitor_slow_query_ms=0 if full_trace_ms == 0 else 100,
        db_monitor_full_trace_ms=full_trace_ms,
        db_monitor_full_stack_for_all_sql=full_stack_for_all,
        db_monitor_max_stack_frames=20,
        db_monitor_n_plus_one_threshold=10,
        db_monitor_log_dir=Path("unused"),
        db_monitor_retention_days=8,
        db_monitor_queue_size=100,
        db_monitor_max_file_mb=1,
    )
    return DatabaseMonitor(settings, writer=writer or MemoryWriter())


def _sqlite_engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def test_select_insert_update_and_failure_are_captured_without_parameters() -> None:
    writer = MemoryWriter()
    monitor = _monitor(writer)
    engine = _sqlite_engine()
    monitor.install(engine)

    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE sample (id INTEGER, secret TEXT)"))
        connection.execute(
            text("INSERT INTO sample (id, secret) VALUES (:id, :secret)"),
            {"id": 7, "secret": "private-customer-token"},
        )
        connection.execute(
            text("UPDATE sample SET secret = :secret WHERE id = :id"),
            {"id": 7, "secret": "replacement-secret"},
        )
        connection.execute(
            text("SELECT secret FROM sample WHERE id = :id"),
            {"id": 7},
        ).all()
        connection.execute(text("DELETE FROM sample WHERE id = :id"), {"id": 7})
        with pytest.raises(OperationalError):
            connection.execute(
                text("SELECT * FROM missing_table WHERE secret = :secret"),
                {"secret": "never-log-this"},
            )

    sql_records = [row for row in writer.records if row["record_type"] == "sql"]
    operations = {row["operation"] for row in sql_records}
    assert {"CREATE", "SELECT", "INSERT", "UPDATE", "DELETE"} <= operations
    failed = next(row for row in sql_records if row["success"] is False)
    assert failed["error_type"] == "OperationalError"
    assert all(row["duration_ms"] >= 0 for row in sql_records)
    assert all(row["query_fingerprint"] for row in sql_records)
    assert all(row["python_caller"] for row in sql_records)
    serialized = json.dumps(sql_records)
    assert "private-customer-token" not in serialized
    assert "replacement-secret" not in serialized
    assert "never-log-this" not in serialized


def test_nested_execution_contexts_keep_their_own_duration_and_statement() -> None:
    writer = MemoryWriter()
    monitor = _monitor(writer)
    first = SimpleNamespace(execution_options={})
    second = SimpleNamespace(execution_options={})
    cursor = SimpleNamespace(rowcount=1)

    monitor.before_cursor_execute(None, cursor, "SELECT 1", (), first, False)
    monitor.before_cursor_execute(None, cursor, "UPDATE x SET y = 2", (), second, False)
    monitor.after_cursor_execute(None, cursor, "UPDATE x SET y = 2", (), second, False)
    monitor.after_cursor_execute(None, cursor, "SELECT 1", (), first, False)

    records = [row for row in writer.records if row["record_type"] == "sql"]
    assert [row["operation"] for row in records] == ["UPDATE", "SELECT"]
    assert all(row["duration_ms"] >= 0 for row in records)


def test_http_context_correlates_query_and_is_cleared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = MemoryWriter()
    monitor = _monitor(writer)
    engine = _sqlite_engine()
    monitor.install(engine)
    monkeypatch.setattr(monitor_module, "_global_monitor", monitor)
    app = create_app(Settings(database_url="sqlite+pysqlite:///:memory:", job_worker_enabled=False))

    @app.get("/monitor-test", name="monitor_test_route")
    def monitor_test_route(background_tasks: BackgroundTasks) -> dict[str, int]:
        def background_query() -> None:
            with engine.connect() as connection:
                connection.scalar(text("SELECT 43"))

        background_tasks.add_task(background_query)
        with engine.connect() as connection:
            return {"value": int(connection.scalar(text("SELECT 42")))}

    with TestClient(app) as client:
        response = client.get("/monitor-test", headers={"X-Request-ID": "request-test-1"})

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "request-test-1"
    query = next(row for row in writer.records if row.get("operation") == "SELECT")
    assert query["origin_type"] == "HTTP"
    assert query["request_id"] == "request-test-1"
    assert query["http_path"] == "/monitor-test"
    assert query["route_name"] == "monitor_test_route"
    summary = next(row for row in writer.records if row["record_type"] == "request_summary")
    assert summary["sql_query_count"] == 2
    assert summary["route_path"] == "/monitor-test"
    assert summary["request_started_at"] <= summary["request_finished_at"]
    assert current_scope_fields() == {"origin_type": "UNKNOWN"}


def test_background_job_context_correlates_query_and_is_cleared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = MemoryWriter()
    monitor = _monitor(writer)
    engine = _sqlite_engine()
    monitor.install(engine)
    monkeypatch.setattr(monitor_module, "_global_monitor", monitor)
    job = BackgroundJob(
        id=81,
        job_type="CERI_FEATURE_BATCH",
        status=JobStatus.RUNNING,
        related_run_id=108,
        worker_id="worker-observe",
        workflow_key="ceri-run-108",
        payload_json={"run_id": 108, "tickers": ["TEST"]},
    )

    def handler(_db, _job):
        with engine.connect() as connection:
            connection.scalar(text("SELECT 1"))
        return {"handled": True}

    result = execute_job(object(), job, {"CERI_FEATURE_BATCH": handler})

    assert result == {"handled": True}
    query = next(row for row in writer.records if row.get("operation") == "SELECT")
    assert query["origin_type"] == "BACKGROUND_JOB"
    assert query["job_id"] == 81
    assert query["job_type"] == "CERI_FEATURE_BATCH"
    assert query["run_id"] == 108
    assert query["worker_id"] == "worker-observe"
    summary = next(row for row in writer.records if row["record_type"] == "job_summary")
    assert summary["sql_query_count"] == 1
    assert summary["attempt"] == 1
    assert summary["job_phases"]["job_handler"]["count"] == 1
    assert summary["job_started_at"] <= summary["job_finished_at"]
    assert current_scope_fields() == {"origin_type": "UNKNOWN"}


def test_full_stack_is_threshold_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    writer = MemoryWriter()
    monitor = _monitor(writer, full_trace_ms=0)
    engine = _sqlite_engine()
    monitor.install(engine)
    monkeypatch.setattr(monitor_module, "_useful_application_frame", lambda _frame: True)
    with engine.connect() as connection:
        connection.scalar(text("SELECT 1"))
    slow_record = next(row for row in writer.records if row["record_type"] == "sql")
    assert slow_record["slow_query"] is True
    assert slow_record["application_stack"]

    fast_writer = MemoryWriter()
    fast_monitor = _monitor(fast_writer, full_trace_ms=10_000)
    fast_engine = _sqlite_engine()
    fast_monitor.install(fast_engine)
    with fast_engine.connect() as connection:
        connection.scalar(text("SELECT 1"))
    fast_record = next(row for row in fast_writer.records if row["record_type"] == "sql")
    assert "application_stack" not in fast_record
    assert fast_record["python_caller"]

    all_stack_writer = MemoryWriter()
    all_stack_monitor = _monitor(
        all_stack_writer,
        full_trace_ms=10_000,
        full_stack_for_all=True,
    )
    all_stack_engine = _sqlite_engine()
    all_stack_monitor.install(all_stack_engine)
    with all_stack_engine.connect() as connection:
        connection.scalar(text("SELECT 1"))
    all_stack_record = next(row for row in all_stack_writer.records if row["record_type"] == "sql")
    assert all_stack_record["application_stack"]


def test_monitor_writer_failure_does_not_fail_database_operation() -> None:
    monitor = _monitor(FailingWriter())
    engine = _sqlite_engine()
    monitor.install(engine)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT 9")) == 9


def test_excluded_monitoring_connection_does_not_recurse() -> None:
    writer = MemoryWriter()
    monitor = _monitor(writer)
    engine = _sqlite_engine()
    monitor.install(engine)

    with engine.connect().execution_options(db_monitor_excluded=True) as connection:
        assert connection.scalar(text("SELECT 1")) == 1

    assert not [row for row in writer.records if row.get("record_type") == "sql"]


def test_queue_overflow_is_counted_without_blocking(tmp_path: Path) -> None:
    class AliveThread:
        @staticmethod
        def is_alive() -> bool:
            return True

    writer = JsonlTelemetryWriter(
        tmp_path,
        retention_days=8,
        queue_size=1,
        max_file_mb=1,
    )
    writer._thread = AliveThread()  # type: ignore[assignment]  # controlled saturation
    writer.queue.put_nowait({"record_type": "already-full"})

    assert writer.enqueue({"record_type": "dropped"}) is False
    assert writer.status()["db_monitor_dropped_records"] == 1


def test_rotation_and_retention(tmp_path: Path) -> None:
    old_path = tmp_path / "sql-2000-01-01-p1.jsonl"
    old_path.write_text("{}\n", encoding="utf-8")
    old_time = (datetime.now(UTC) - timedelta(days=20)).timestamp()
    os.utime(old_path, (old_time, old_time))
    writer = JsonlTelemetryWriter(
        tmp_path,
        retention_days=8,
        queue_size=10,
        max_file_mb=1,
        flush_interval_seconds=0.01,
    )
    writer.start()
    writer.enqueue(
        {
            "record_type": "sql",
            "timestamp": datetime.now(UTC).isoformat(),
            "payload": "x" * (1024 * 1024),
        }
    )
    writer.enqueue(
        {
            "record_type": "sql",
            "timestamp": datetime.now(UTC).isoformat(),
            "payload": "second",
        }
    )
    writer.queue.join()
    writer.stop()

    assert not old_path.exists()
    assert len(list(tmp_path.glob("sql-*.jsonl"))) >= 2


def test_retention_bounds_file_count_and_total_size(tmp_path: Path) -> None:
    writer = JsonlTelemetryWriter(
        tmp_path,
        retention_days=30,
        queue_size=10,
        max_file_mb=1,
        max_files=3,
        max_total_mb=1,
    )
    now = datetime.now(UTC).timestamp()
    for index in range(8):
        path = tmp_path / f"sql-2026-08-22-p1.{index}.jsonl"
        path.write_bytes(b"x" * 300_000)
        os.utime(path, (now + index, now + index))

    writer._apply_retention()

    retained = list(tmp_path.glob("sql-*.jsonl"))
    assert len(retained) <= 3
    assert sum(path.stat().st_size for path in retained) <= 1024 * 1024


def test_retention_protects_active_stream_then_prunes_it_when_inactive(tmp_path: Path) -> None:
    writer = JsonlTelemetryWriter(
        tmp_path,
        retention_days=30,
        queue_size=10,
        max_file_mb=1,
        max_files=2,
        max_total_mb=1,
    )
    paths = [tmp_path / f"sql-2026-08-22-p{index}.jsonl" for index in range(3)]
    now = datetime.now(UTC).timestamp()
    for index, path in enumerate(paths):
        path.write_bytes(b"x" * 100)
        os.utime(path, (now + index, now + index))

    writer._apply_retention(protected_paths={paths[0]})
    assert paths[0].exists()

    writer._apply_retention()
    assert len(list(tmp_path.glob("sql-*.jsonl"))) <= 2


def test_normalization_removes_literals_comments_and_placeholder_names() -> None:
    first = normalize_sql("SELECT * /* trace */ FROM x WHERE id = :run_id AND name = 'Alice'")
    second = normalize_sql("SELECT * FROM x WHERE id = :other AND name = 'Bob'")
    assert first == second
    assert "Alice" not in first
    assert "run_id" not in first


def test_normalization_canonicalizes_in_list_cardinality() -> None:
    three = normalize_sql("SELECT * FROM x WHERE ticker IN (:a, :b, :c)")
    five = normalize_sql("SELECT * FROM x WHERE ticker IN (:a, :b, :c, :d, :e)")

    assert three == five
    assert "IN (?*)" in three


def test_parameter_digest_is_keyed_stable_and_never_contains_values() -> None:
    first = parameter_digest({"ticker": "PRIVATE", "run_id": 7}, False)
    same = parameter_digest({"run_id": 7, "ticker": "PRIVATE"}, False)
    different = parameter_digest({"ticker": "OTHER", "run_id": 7}, False)

    assert first == same
    assert first != different
    assert "PRIVATE" not in str(first)


def test_role_specific_writer_directories_are_independent(tmp_path: Path) -> None:
    worker = DatabaseMonitor(
        Settings(
            database_url="sqlite+pysqlite:///:memory:",
            db_monitor_log_dir=tmp_path,
            db_monitor_test_log_dir=tmp_path / "tests",
            db_monitor_process_role="worker",
        )
    )
    web = DatabaseMonitor(
        Settings(
            database_url="sqlite+pysqlite:///:memory:",
            db_monitor_log_dir=tmp_path,
            db_monitor_test_log_dir=tmp_path / "tests",
            db_monitor_process_role="web",
        )
    )

    assert worker.writer.log_dir == tmp_path / "worker"
    assert web.writer.log_dir == tmp_path / "web"
    assert worker.writer.log_dir != web.writer.log_dir
    assert worker.writer.retention_days >= 7
    assert worker.writer.max_files >= 32


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        ([r"C:\repo\app\serve.py"], "web"),
        ([r"C:\repo\app\worker.py"], "worker"),
        ([r"C:\repo\app\worker_supervisor.py"], "supervisor"),
        ([r"scripts\start_full_week_sql_audit.py"], "diagnostic"),
        ([r"C:\repo\scripts\inspect.py"], "diagnostic"),
    ],
)
def test_process_role_detects_python_module_file_paths(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    expected: str,
) -> None:
    monkeypatch.setattr(monitor_module.sys, "argv", argv)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    assert resolve_process_role("auto") == expected


def test_monitor_health_contains_retention_and_queue_proof(tmp_path: Path) -> None:
    writer = JsonlTelemetryWriter(
        tmp_path,
        retention_days=14,
        queue_size=10,
        max_file_mb=1,
        max_files=512,
        max_total_mb=8192,
        base_metadata={"process_role": "worker"},
    )
    record = writer._health_record()

    assert record["record_type"] == "monitor_health"
    assert record["telemetry_queue_capacity"] == 10
    assert record["retention_days"] == 14
    assert record["max_files"] == 512
    assert record["process_role"] == "worker"


def test_pool_and_transaction_timing_are_scoped_and_cleared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = MemoryWriter()
    monitor = _monitor(writer)
    monkeypatch.setattr(monitor_module, "_global_monitor", monitor)
    engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=MonitoredQueuePool)
    monitor.install(engine)
    handle = begin_scope(ExecutionScope(origin_type="HTTP", request_id="pool-1"))
    with engine.begin() as connection:
        connection.scalar(text("SELECT 1"))
    summary = finish_scope(handle, record_type="request_summary", total_duration_ms=1.0)

    assert summary["pool_checkout_count"] == 1
    assert summary["transaction_count"] == 1
    assert summary["pool_wait_max_ms"] >= 0
    assert current_scope_fields() == {"origin_type": "UNKNOWN"}


@pytest.mark.parametrize("terminal_status", [JobStatus.PARTIAL, JobStatus.CANCELLED])
def test_background_job_summary_records_non_success_terminal_status(
    monkeypatch: pytest.MonkeyPatch,
    terminal_status: str,
) -> None:
    writer = MemoryWriter()
    monitor = _monitor(writer)
    monkeypatch.setattr(monitor_module, "_global_monitor", monitor)
    job = BackgroundJob(
        id=82,
        job_type="TEST_JOB",
        status=JobStatus.RUNNING,
        retry_count=1,
        payload_json={},
    )

    def handler(_db, current_job):
        current_job.status = terminal_status
        return {"status": str(terminal_status)}

    execute_job(object(), job, {"TEST_JOB": handler})
    summary = next(row for row in writer.records if row["record_type"] == "job_summary")

    assert summary["attempt"] == 2
    assert summary["job_status_at_scope_end"] == str(terminal_status)
    assert current_scope_fields() == {"origin_type": "UNKNOWN"}


def test_failed_background_job_still_emits_summary_and_clears_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = MemoryWriter()
    monitor = _monitor(writer)
    monkeypatch.setattr(monitor_module, "_global_monitor", monitor)
    job = BackgroundJob(
        id=83,
        job_type="FAIL_JOB",
        status=JobStatus.RUNNING,
        retry_count=0,
        payload_json={},
    )

    def handler(_db, _job):
        raise RuntimeError("expected test failure")

    with pytest.raises(RuntimeError, match="expected test failure"):
        execute_job(object(), job, {"FAIL_JOB": handler})
    summary = next(row for row in writer.records if row["record_type"] == "job_summary")

    assert summary["outcome"] == "FAILURE"
    assert current_scope_fields() == {"origin_type": "UNKNOWN"}


def test_error_summary_redacts_quoted_values_and_url_credentials() -> None:
    summary = safe_error_summary(
        ValueError(
            'invalid value "top-secret-token" at '
            "postgresql://monitor:database-password@localhost/db"
        )
    )

    assert "top-secret-token" not in summary
    assert "database-password" not in summary
