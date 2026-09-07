from __future__ import annotations

import ast
import gc
import json
import logging
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.observability.logging import JsonLogFormatter
from app.observability.metrics import DEFINITIONS, operational_metrics
from app.observability.resource_sampler import ResourceSampler, SystemMetricsCollector
from app.observability.transaction_metrics import pending_metric_count, publish_after_commit
from app.services.background_job_service import _queue_fanout_threshold_metric
from app.services.background_job_service import _safe_error as job_safe_error
from app.services.ceri.batched_job_handlers import _safe_error as ceri_safe_error
from app.services.ceri.observability import CeriMetricRegistry
from app.services.ceri.providers.eodhd_client import _safe_error as provider_safe_error
from app.services.readiness_service import _safe_message as readiness_safe_message
from app.services.redaction import redact_sensitive, redact_text, redacted_token_metadata


@pytest.fixture(autouse=True)
def reset_metrics():
    operational_metrics.reset()
    yield
    operational_metrics.reset()


@pytest.mark.parametrize(
    "secret_text",
    [
        "Bearer abc.DEF-123",
        "Basic dXNlcjpwYXNz",
        "password=SECRET",
        "passwd='SECRET'",
        'PWD: "SECRET"',
        "api_key=SECRET",
        "ApiKey='SECRET'",
        "api-key=SECRET",
        "token=SECRET",
        "access_token=SECRET",
        "refresh_token=SECRET",
        "client_secret=SECRET",
        "authorization=SECRET",
        "postgresql://user:SECRET@host/db",
        "https://user:SECRET@example.test/path",
        "https://example.test/path?api_key=SECRET&x=1",
        "https://example.test/path?provider_token=SECRET&x=1",
        '{"Access_Token":"SECRET","safe":"visible"}',
    ],
)
def test_shared_redaction_removes_embedded_secrets_idempotently(secret_text: str) -> None:
    redacted = redact_text(secret_text)
    assert "SECRET" not in redacted
    assert "abc.DEF-123" not in redacted
    assert "dXNlcjpwYXNz" not in redacted
    assert redact_text(redacted) == redacted


def test_shared_redaction_covers_all_observability_surfaces() -> None:
    source = "provider failed: PASSWORD=SECRET postgresql://u:SECRET@db/x"
    record = logging.LogRecord("test", logging.ERROR, __file__, 1, source, (), None)
    structured = JsonLogFormatter("worker").format(record)
    payload = json.loads(structured)
    surfaces = (
        structured,
        job_safe_error(source),
        readiness_safe_message(RuntimeError(source)),
        ceri_safe_error(RuntimeError(source)),
        provider_safe_error(RuntimeError(source)),
        json.dumps(redact_sensitive({"operations": source, "error_message": source})),
        json.dumps(redact_sensitive({"ceri_persisted_error": source})),
    )
    assert payload["severity"] == "ERROR"
    assert all("SECRET" not in value for value in surfaces)
    token_metadata = redacted_token_metadata("execution-token-secret")
    assert token_metadata["execution_token_suffix"] is None
    assert "secret" not in json.dumps(token_metadata)


def test_commit_buffer_success_rollback_nested_and_retry() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with Session(engine) as session:
        assert session.get_transaction() is None
        publish_after_commit(session, "increment", "swinglens_jobs_enqueued_total", job_type="TEST")
        assert pending_metric_count(session) == 1
        session.rollback()
    assert operational_metrics.total("swinglens_jobs_enqueued_total") == 0

    with Session(engine) as session:
        session.execute(text("select 1"))
        publish_after_commit(session, "increment", "swinglens_jobs_enqueued_total", job_type="TEST")
        assert pending_metric_count(session) == 1
        session.rollback()
    assert operational_metrics.total("swinglens_jobs_enqueued_total") == 0

    with Session(engine) as session:
        session.execute(text("select 1"))
        publish_after_commit(session, "increment", "swinglens_jobs_enqueued_total", job_type="TEST")
        nested = session.begin_nested()
        publish_after_commit(session, "increment", "swinglens_jobs_enqueued_total", job_type="TEST")
        nested.rollback()
        session.commit()
    assert operational_metrics.total("swinglens_jobs_enqueued_total", job_type="TEST") == 1

    with Session(engine) as session:
        session.execute(text("select 1"))
        nested = session.begin_nested()
        publish_after_commit(session, "increment", "swinglens_jobs_enqueued_total", job_type="TEST")
        nested.commit()
        session.commit()
    assert operational_metrics.total("swinglens_jobs_enqueued_total", job_type="TEST") == 2

    with Session(engine) as session:
        session.execute(text("select 1"))
        publish_after_commit(session, "increment", "swinglens_jobs_enqueued_total", job_type="TEST")
        session.rollback()
        session.execute(text("select 1"))
        publish_after_commit(session, "increment", "swinglens_jobs_enqueued_total", job_type="TEST")
        session.commit()
    assert operational_metrics.total("swinglens_jobs_enqueued_total", job_type="TEST") == 3


def test_flush_success_then_commit_failure_discards_metric() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    class FailingCommitSession(Session):
        def commit(self) -> None:
            self.flush()
            self.rollback()
            raise RuntimeError("commit failed")

    with FailingCommitSession(engine) as session:
        session.execute(text("select 1"))
        publish_after_commit(session, "increment", "swinglens_jobs_enqueued_total", job_type="TEST")
        with pytest.raises(RuntimeError, match="commit failed"):
            session.commit()
    assert operational_metrics.total("swinglens_jobs_enqueued_total") == 0


def test_metric_catalog_is_closed_typed_and_has_complete_schema() -> None:
    for name, definition in DEFINITIONS.items():
        assert definition.kind in {"counter", "gauge", "histogram"}
        assert definition.description.strip()
        assert definition.unit.strip()
        assert definition.allowed_values_policy == "bounded-runtime-values"
        assert len(definition.labels) == len(set(definition.labels))
        if definition.kind == "histogram":
            assert definition.buckets
        if name.endswith("_total"):
            assert definition.kind == "counter"
        if name.endswith("_ratio"):
            assert definition.kind == "gauge"
        if name.endswith("_seconds"):
            assert definition.kind in {"gauge", "histogram"}
            assert definition.unit == "seconds"
        if name.endswith("_bytes"):
            assert definition.kind == "gauge"
            assert definition.unit == "bytes"
        assert not name.endswith(("_duration", "_latency", "_count"))
    with pytest.raises(ValueError, match="undeclared"):
        operational_metrics.increment("swinglens_not_declared_total")


def test_every_literal_business_metric_call_matches_the_catalog() -> None:
    actions = {"increment": "counter", "set_gauge": "gauge", "observe": "histogram"}
    issues: list[str] = []
    for path in Path("app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in actions or not node.args:
                continue
            if (
                not isinstance(node.func.value, ast.Name)
                or node.func.value.id != "operational_metrics"
            ):
                continue
            name_node = node.args[0]
            if not isinstance(name_node, ast.Constant) or not isinstance(name_node.value, str):
                continue
            name = name_node.value
            definition = DEFINITIONS.get(name)
            if definition is None:
                issues.append(f"{path}:{node.lineno}: undeclared {name}")
                continue
            if definition.kind != actions[node.func.attr]:
                issues.append(f"{path}:{node.lineno}: wrong type for {name}")
            if not any(keyword.arg is None for keyword in node.keywords):
                actual_labels = {
                    keyword.arg
                    for keyword in node.keywords
                    if keyword.arg is not None and keyword.arg != "value"
                }
                if actual_labels != set(definition.labels):
                    issues.append(
                        f"{path}:{node.lineno}: labels {sorted(actual_labels)} for {name}"
                    )
    assert issues == []


def test_metric_client_failure_is_contained(monkeypatch) -> None:
    metric = operational_metrics._metrics["swinglens_jobs_enqueued_total"]
    monkeypatch.setattr(metric, "labels", lambda **_labels: (_ for _ in ()).throw(OSError("down")))
    operational_metrics.increment("swinglens_jobs_enqueued_total", job_type="TEST")
    assert operational_metrics.total("swinglens_jobs_enqueued_total") == 0


@pytest.mark.performance
def test_metrics_hot_path_and_scrape_remain_within_budget() -> None:
    events = 20_000
    started = time.perf_counter()
    for _ in range(events):
        operational_metrics.increment(
            "swinglens_jobs_enqueued_total", job_type="OBS_PERFORMANCE_FIXTURE"
        )
    emission_elapsed = time.perf_counter() - started
    started = time.perf_counter()
    payload = operational_metrics.as_prometheus()
    scrape_elapsed = time.perf_counter() - started
    assert operational_metrics.total(
        "swinglens_jobs_enqueued_total", job_type="OBS_PERFORMANCE_FIXTURE"
    ) == events
    assert "swinglens_jobs_enqueued_total" in payload
    assert emission_elapsed / events < 0.0001
    assert scrape_elapsed < 0.25
    print(
        "OBS-PERF metrics "
        f"events={events} mean_us={emission_elapsed / events * 1_000_000:.3f} "
        f"scrape_ms={scrape_elapsed * 1000:.3f} bytes={len(payload.encode('utf-8'))}"
    )


def test_fanout_threshold_is_per_root_and_distinguishes_abnormal_shapes(
    settings_factory, monkeypatch
) -> None:
    settings = settings_factory(
        observability_fanout_warning=50,
        observability_fanout_critical=100,
        observability_fanout_thresholds={
            "WINNER_MATURATION": {
                "descendants_warning": 10,
                "descendants_critical": 20,
                "depth_warning": 4,
                "depth_critical": 8,
            }
        },
    )
    monkeypatch.setattr("app.services.background_job_service.get_settings", lambda: settings)

    def root(*, descendants: int, depth: int):
        return type(
            "Root",
            (),
            {
                "workflow_family": "WINNER_MATURATION",
                "total_descendant_count": descendants,
                "maximum_depth": depth,
                "warning_emitted": False,
                "critical_emitted": False,
            },
        )()

    for _ in range(100):
        _queue_fanout_threshold_metric(object(), root(descendants=1, depth=0))
    assert operational_metrics.total("swinglens_job_fanout_abnormal_roots_total") == 0

    excessive = root(descendants=12, depth=1)
    _queue_fanout_threshold_metric(object(), excessive)
    _queue_fanout_threshold_metric(object(), excessive)
    assert (
        operational_metrics.total(
            "swinglens_job_fanout_abnormal_roots_total",
            workflow_family="WINNER_MATURATION",
            severity="warning",
            reason="descendants",
        )
        == 1
    )

    deep = root(descendants=3, depth=9)
    _queue_fanout_threshold_metric(object(), deep)
    assert (
        operational_metrics.total(
            "swinglens_job_fanout_abnormal_roots_total",
            workflow_family="WINNER_MATURATION",
            severity="critical",
            reason="depth",
        )
        == 1
    )


def test_ceri_registry_is_stateless_bounded_and_thread_safe() -> None:
    registry = CeriMetricRegistry()
    events_per_thread = 2500

    def emit() -> None:
        for _ in range(events_per_thread):
            registry.increment("ceri_processing_retries_total", job_type="CERI_PROVIDER_INGEST")

    tracemalloc.start()
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _index: emit(), range(8)))
    gc.collect()
    retained_after_first, peak_after_first = tracemalloc.get_traced_memory()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _index: emit(), range(8)))
    elapsed = time.perf_counter() - started
    gc.collect()
    retained_after_second, peak_after_second = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    snapshot = registry.snapshot()
    assert snapshot["sample_count"] == 0
    assert snapshot["samples"] == []
    assert snapshot["counters"] == {}
    assert retained_after_second - retained_after_first < 2 * 1024 * 1024
    assert (
        operational_metrics.total(
            "swinglens_ceri_processing_retries_total", job_type="CERI_PROVIDER_INGEST"
        )
        == 40_000
    )
    print(
        "OBS-PERF ceri-metrics "
        f"events=40000 elapsed_seconds={elapsed:.6f} "
        f"retained_growth_bytes={retained_after_second - retained_after_first} "
        f"peak_bytes={max(peak_after_first, peak_after_second)} sample_count=0"
    )


def test_resource_sampler_fault_is_contained_and_recovers(monkeypatch) -> None:
    calls = 0

    def failing_process():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("password=SECRET")
        return type(
            "Process",
            (),
            {
                "memory_info": lambda self: type("Memory", (), {"rss": 123, "private": 45})(),
                "cpu_percent": lambda self, _interval: 2.5,
            },
        )()

    monkeypatch.setattr("app.observability.resource_sampler.psutil.Process", failing_process)
    sampler = ResourceSampler(process_role="worker", interval_seconds=1, worker_id="test")
    sampler.sample_once()
    sampler.sample_once()
    assert (
        operational_metrics.total(
            "swinglens_resource_sampler_errors_total",
            process_role="worker",
            category="process_memory",
        )
        == 1
    )
    assert (
        operational_metrics.total(
            "swinglens_resource_sampler_up", process_role="worker", category="process_memory"
        )
        == 1
    )
    assert operational_metrics.total("swinglens_worker_rss_bytes", worker_id="test") == 123


def test_system_sampler_categories_fail_independently_loop_and_recover(
    settings_factory, monkeypatch, caplog
) -> None:
    settings = settings_factory(observability_collection_interval_seconds=1)
    collector = SystemMetricsCollector(create_engine("sqlite+pysqlite:///:memory:"), settings)
    collector.interval_seconds = 0.01
    collector._db_size_interval = 60
    collector._last_db_size = time.monotonic()
    calls = {name: 0 for name in ("queue", "pool", "disk", "log", "monitor")}

    def intermittent(name: str):
        def sample(*_args):
            calls[name] += 1
            if calls[name] == 1:
                raise OSError(f"password=SECRET {name}")

        return sample

    monkeypatch.setattr(collector, "_queue", intermittent("queue"))
    monkeypatch.setattr(collector, "_workers", lambda *_args: None)
    monkeypatch.setattr(collector, "_supervisor", lambda *_args: None)
    monkeypatch.setattr(collector, "_pool", intermittent("pool"))
    monkeypatch.setattr(collector, "_disk_space", intermittent("disk"))
    monkeypatch.setattr(collector, "_log_storage", intermittent("log"))
    monkeypatch.setattr(collector, "_db_monitor", intermittent("monitor"))

    with caplog.at_level(logging.WARNING):
        collector.start()
        deadline = time.monotonic() + 2
        while min(calls.values()) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert collector.alive
        collector.stop()

    assert all(count >= 2 for count in calls.values())
    assert "SECRET" not in caplog.text
    for category in ("queue_snapshot", "db_pool", "disk", "log_storage", "sql_recorder"):
        assert (
            operational_metrics.total(
                "swinglens_resource_sampler_up", process_role="web", category=category
            )
            == 1
        )


def test_redaction_timestamp_fixture_is_stable() -> None:
    assert datetime.now(UTC).tzinfo is UTC
