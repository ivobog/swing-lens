from __future__ import annotations

import ast
import asyncio
import time
import tracemalloc
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.config import Config
from sqlalchemy.orm import Session

from app.database_safety import (
    DisposableDatabaseIdentity,
    assert_alembic_connection_matches,
    run_guarded_alembic_upgrade,
)
from app.models.ceri_tables import CeriProcessingRun
from app.models.ib_market_intelligence_tables import IBIntelligenceRun
from app.models.tables import BackgroundJob, TechnicalFeatureArtifact
from app.observability.correlation import durable_causality_fields, root_action_scope
from app.observability.metrics import MAX_SERIES_PER_METRIC, operational_metrics
from app.observability.transaction_metrics import COMMIT_DEPENDENT_METRICS
from app.services.cleanup_service import execute_durable_evidence_retention
from app.services.persistence_redaction import redact_session_persistence_boundaries
from app.services.readiness_service import ReadinessService
from app.services.winner_probability import scheduler


@pytest.fixture(autouse=True)
def _metrics_reset():
    operational_metrics.configure(enabled=True)
    operational_metrics.reset()
    yield
    operational_metrics.configure(enabled=True)
    operational_metrics.reset()


SECRET_VALUES = (
    "password=SECRET",
    "Password=SECRET",
    'PASSWORD="SECRET"',
    "pwd='SECRET'",
    "passwd=SECRET",
    "api_key=SECRET",
    "api-key=SECRET",
    "apikey=SECRET",
    "token=SECRET",
    "access_token=SECRET",
    "refresh_token=SECRET",
    "client_secret=SECRET",
    "Authorization: Bearer SECRET",
    "Basic dXNlcjpTRUNSRVQ=",
    "postgresql://user:SECRET@localhost/db",
    "postgresql+psycopg://user:SECRET@localhost/db",
    "https://user:SECRET@example.com/",
    "?token=SECRET",
    "?api_key=SECRET",
    '{"password":"SECRET"}',
    '{"nested":{"client_secret":"SECRET"}}',
)


@pytest.mark.parametrize("secret", SECRET_VALUES)
def test_final_orm_persistence_boundary_redacts_every_error_surface(secret: str) -> None:
    session = Session()
    rows = (
        BackgroundJob(error_message=secret, result_json={"error": secret}),
        CeriProcessingRun(errors_json={"records": [{"error": secret}]}),
        IBIntelligenceRun(error_message=secret),
        TechnicalFeatureArtifact(last_shadow_mismatch_json={"error": secret}),
    )
    session.add_all(rows)
    redact_session_persistence_boundaries(session)
    rendered = repr(
        (
            rows[0].error_message,
            rows[0].result_json,
            rows[1].errors_json,
            rows[2].error_message,
            rows[3].last_shadow_mismatch_json,
        )
    )
    assert "SECRET" not in rendered
    assert "dXNlcjpTRUNSRVQ=" not in rendered


def test_cardinality_is_bounded_for_100k_dynamic_values_per_dimension() -> None:
    tracemalloc.start()
    started = time.perf_counter()
    for index in range(100_000):
        operational_metrics.set_gauge(
            "swinglens_worker_up", 1, worker_id=f"instance-{index}"
        )
        operational_metrics.increment(
            "swinglens_job_progress_total", stage=f"stage-{index}"
        )
        operational_metrics.increment(
            "swinglens_technical_artifact_cache_total",
            result="invalid",
            reason=f"reason-{index}",
        )
        operational_metrics.increment(
            "swinglens_ceri_ingestion_total",
            provider=f"provider-{index}",
            dataset=f"dataset-{index}",
            result="success",
        )

    series_counts = {
        name: len(metric._metrics)
        for name, metric in operational_metrics._metrics.items()
        if hasattr(metric, "_metrics") and len(metric._metrics)
    }
    current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    elapsed = time.perf_counter() - started

    assert all(count <= MAX_SERIES_PER_METRIC for count in series_counts.values())
    assert len(operational_metrics._totals) <= MAX_SERIES_PER_METRIC * 4
    payload = operational_metrics.as_prometheus()
    assert 'worker_id="OTHER"' in payload
    assert 'stage="OTHER"' in payload
    assert 'provider="OTHER"' in payload
    assert len(payload.encode()) < 1_000_000
    assert current_bytes < 5_000_000
    print(
        "CARDINALITY-STRESS "
        f"events=400000 elapsed_seconds={elapsed:.6f} "
        f"series={sum(series_counts.values())} mirror={len(operational_metrics._totals)} "
        f"current_bytes={current_bytes} peak_bytes={peak_bytes} "
        f"scrape_bytes={len(payload.encode())}"
    )


def test_obsolete_worker_gauge_child_is_removed() -> None:
    operational_metrics.set_gauge("swinglens_worker_up", 1, worker_id="old-slot")
    assert 'worker_id="old-slot"' in operational_metrics.as_prometheus()
    operational_metrics.remove_series("swinglens_worker_up", worker_id="old-slot")
    assert 'worker_id="old-slot"' not in operational_metrics.as_prometheus()
    assert operational_metrics.total("swinglens_worker_up", worker_id="old-slot") == 0


def test_commit_dependent_metrics_have_no_direct_facade_bypass() -> None:
    violations: list[str] = []
    for path in Path("app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {"increment", "observe", "set_gauge"} or not node.args:
                continue
            if not isinstance(node.args[0], ast.Constant):
                continue
            if node.args[0].value in COMMIT_DEPENDENT_METRICS:
                violations.append(f"{path}:{node.lineno}:{node.args[0].value}")
    assert violations == []


@pytest.mark.parametrize(
    "status",
    (
        "QUEUED",
        "RUNNING",
        "COMPLETED",
        "PARTIAL",
        "FAILED",
        "BLOCKED",
        "RECOVERING",
        "STALLED",
        "CANCELLED",
        "STALE",
    ),
)
def test_winner_all_same_session_statuses_are_idle_without_audit(status, monkeypatch) -> None:
    existing = SimpleNamespace(id=1, status=status)
    db = SimpleNamespace(scalar=lambda _statement: existing)
    roots: list[object] = []
    enqueues: list[object] = []

    @contextmanager
    def root_scope(*args):
        roots.append(args)
        yield

    monkeypatch.setattr(scheduler, "root_action_scope", root_scope)
    monkeypatch.setattr(
        scheduler,
        "enqueue_outcome_maturation_workflow",
        lambda *args, **kwargs: enqueues.append((args, kwargs)),
    )
    for _ in range(2_000):
        assert scheduler.schedule_primary_h5_maturation(
            db, now=datetime(2026, 9, 7, 12, tzinfo=UTC)
        ) is existing
    assert roots == []
    assert enqueues == []


def test_workload_aware_ib_required_states(monkeypatch, tmp_path, settings_factory) -> None:
    settings = settings_factory(
        database_url="sqlite+pysqlite:///:memory:",
        upload_dir=tmp_path / "uploads",
        export_dir=tmp_path / "exports",
        cache_dir=tmp_path / "cache",
        db_monitor_log_dir=tmp_path / "logs",
        observability_ib_required=False,
    )
    service = ReadinessService(engine=SimpleNamespace(), settings=settings, ib_available=False)
    monkeypatch.setattr(service, "_ib_workload_required", lambda: False)
    assert service._ib_check().status == "optional_unavailable"
    monkeypatch.setattr(service, "_ib_workload_required", lambda: True)
    assert service._ib_check().message == "required_unavailable:runnable_work"
    service.ib_available = True
    assert service._ib_check().status == "ok"


def test_resource_and_system_collector_death_are_independent(
    monkeypatch, tmp_path, settings_factory
) -> None:
    now = datetime.now(UTC)
    settings = settings_factory(
        database_url="sqlite+pysqlite:///:memory:",
        upload_dir=tmp_path / "uploads",
        export_dir=tmp_path / "exports",
        cache_dir=tmp_path / "cache",
        db_monitor_log_dir=tmp_path / "logs",
        observability_metrics_enabled=True,
    )
    service = ReadinessService(engine=SimpleNamespace(), settings=settings, now=now)
    states = {
        "resource_sampler": {"last_success": now, "consecutive_failures": 0},
        "system_metrics_collector": {
            "last_success": now.replace(year=now.year - 1),
            "consecutive_failures": 0,
        },
    }
    monkeypatch.setattr(
        "app.observability.resource_sampler.collector_component_status",
        lambda _role, component: states[component],
    )
    assert service._resource_collector_check().status == "ok"
    assert service._system_collector_check().message == "system_collector_dead"

    states["resource_sampler"]["last_success"] = now.replace(year=now.year - 1)
    states["system_metrics_collector"]["last_success"] = now
    assert service._resource_collector_check().message == "collector_dead"
    assert service._system_collector_check().status == "ok"


def test_collector_exception_then_success_recovers_health() -> None:
    from app.observability import resource_sampler

    resource_sampler._component_attempt("web", "system_metrics_collector")
    resource_sampler._component_failure("web", "system_metrics_collector")
    assert resource_sampler.collector_component_status(
        "web", "system_metrics_collector"
    )["consecutive_failures"] == 1
    resource_sampler._component_success("web", "system_metrics_collector")
    recovered = resource_sampler.collector_component_status(
        "web", "system_metrics_collector"
    )
    assert recovered["consecutive_failures"] == 0
    assert recovered["last_success"] is not None

    def fail() -> None:
        raise RuntimeError("injected queue collector failure")

    resource_sampler._fault_contained_sample("web", "queue_snapshot", fail)
    assert resource_sampler.process_sampler_status("web")["queue_snapshot"] == "failed"
    resource_sampler._fault_contained_sample("web", "queue_snapshot", lambda: None)
    assert resource_sampler.process_sampler_status("web")["queue_snapshot"] == "ok"


def test_metrics_off_web_lifespan_starts_no_prometheus_collectors(
    monkeypatch, tmp_path, settings_factory
) -> None:
    from app import main as app_main

    starts: list[str] = []

    class DatabaseSampler:
        def __init__(self, *_args):
            pass

        def start(self):
            starts.append("database_health")

        def stop(self):
            pass

    class ForbiddenCollector:
        def __init__(self, *_args, **_kwargs):
            starts.append("prometheus_constructed")

    settings = settings_factory(
        database_url="sqlite+pysqlite:///:memory:",
        upload_dir=tmp_path / "uploads",
        export_dir=tmp_path / "exports",
        cache_dir=tmp_path / "cache",
        db_monitor_log_dir=tmp_path / "logs",
        observability_metrics_enabled=False,
        job_worker_enabled=False,
    )
    monkeypatch.setattr(app_main, "DatabaseHealthSampler", DatabaseSampler)
    monkeypatch.setattr(app_main, "ResourceSampler", ForbiddenCollector)
    monkeypatch.setattr(app_main, "SystemMetricsCollector", ForbiddenCollector)
    app = app_main.create_app(settings)

    async def enter() -> None:
        async with app_main.lifespan(app):
            assert not operational_metrics.enabled

    asyncio.run(enter())
    assert starts == ["database_health"]
    assert (
        ReadinessService(engine=SimpleNamespace(), settings=settings)
        ._metrics_configuration_check()
        .message
        == "disabled_by_configuration"
    )


def test_alerts_cover_target_and_functional_loop_loss_and_true_pool_capacity() -> None:
    rules = Path("monitoring/prometheus/alerts.yml").read_text(encoding="utf-8")
    for alert in (
        "SwingLensWebMissing",
        "SwingLensWorkerMissing",
        "SwingLensSupervisorMissing",
        "SwingLensWorkerControlLoopHung",
        "SwingLensSupervisorControlLoopHung",
        "SwingLensSystemCollectorMissing",
    ):
        assert f"alert: {alert}" in rules
    assert "swinglens_db_pool_checked_out / clamp_min(swinglens_db_pool_capacity, 1)" in rules
    assert "swinglens_db_pool_size + swinglens_db_pool_overflow" not in rules


def test_active_and_malformed_database_urls_stop_before_alembic(monkeypatch) -> None:
    entered: list[object] = []

    def upgrade(*args):
        entered.append(args)

    monkeypatch.setattr(
        "app.database_safety._read_identity",
        lambda _url: ("swinglens", "127.0.0.1:5432"),
    )
    with pytest.raises(RuntimeError, match="active SwingLens database"):
        run_guarded_alembic_upgrade(
            Config(),
            "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/swinglens",
            upgrade=upgrade,
        )
    with pytest.raises(RuntimeError, match="malformed"):
        run_guarded_alembic_upgrade(Config(), "not a database url %", upgrade=upgrade)
    assert entered == []


def test_alembic_connection_guard_rejects_ignored_database_override(monkeypatch) -> None:
    expected = DisposableDatabaseIdentity(
        database_name="swinglens_pytest_expected",
        server_identity="127.0.0.1:5432",
        safe_url="postgresql+psycopg://user:***@127.0.0.1/swinglens_pytest_expected",
    )
    result = SimpleNamespace(scalar_one=lambda: "swinglens")
    connection = SimpleNamespace(execute=lambda _statement: result)
    monkeypatch.setattr(
        "app.database_safety._server_identity", lambda _connection: "127.0.0.1:5432"
    )
    with pytest.raises(RuntimeError, match="other than the verified disposable target"):
        assert_alembic_connection_matches(connection, expected)


def test_metrics_off_is_empty_and_durable_cleanup_is_independent(monkeypatch) -> None:
    operational_metrics.configure(enabled=False)
    operational_metrics.increment("swinglens_jobs_enqueued_total", job_type="TEST")
    assert operational_metrics.samples() == []
    assert operational_metrics.as_prometheus() == ""

    calls: list[int] = []
    monkeypatch.setattr(
        "app.services.cleanup_service.prune_enqueue_attempt_evidence",
        lambda _db, *, retention_days: calls.append(retention_days)
        or {"attempts": 3, "roots": 2},
    )
    settings = SimpleNamespace(observability_enqueue_attempt_retention_days=30)
    assert execute_durable_evidence_retention(object(), settings) == {"attempts": 3, "roots": 2}
    assert calls == [30]


def test_metrics_off_worker_and_supervisor_start_no_prometheus_components(
    monkeypatch,
) -> None:
    from app import worker, worker_supervisor

    settings = SimpleNamespace(
        job_worker_id="logical-worker",
        observability_metrics_enabled=False,
        observability_metrics_host="127.0.0.1",
        observability_worker_metrics_port=9101,
        observability_supervisor_metrics_port=9102,
        observability_collection_interval_seconds=1,
    )
    forbidden: list[str] = []
    worker_runs: list[str] = []

    class ForbiddenSampler:
        def __init__(self, *_args, **_kwargs):
            forbidden.append("sampler")

    class StoppedEvent:
        def is_set(self) -> bool:
            return True

        def set(self) -> None:
            pass

    def forbidden_server(*_args, **_kwargs):
        forbidden.append("server")

    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(worker, "configure_json_logging", lambda _role: None)
    monkeypatch.setattr(worker.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(worker, "ResourceSampler", ForbiddenSampler)
    monkeypatch.setattr(worker, "start_metrics_http_server", forbidden_server)
    monkeypatch.setattr(
        worker,
        "run_worker",
        lambda **kwargs: worker_runs.append(str(kwargs["worker_id"])),
    )
    worker.main(["--worker-id", "logical-worker"])

    monkeypatch.setattr(worker_supervisor, "get_settings", lambda: settings)
    monkeypatch.setattr(worker_supervisor, "configure_json_logging", lambda _role: None)
    monkeypatch.setattr(worker_supervisor, "Event", StoppedEvent)
    monkeypatch.setattr(worker_supervisor, "ResourceSampler", ForbiddenSampler)
    monkeypatch.setattr(worker_supervisor, "start_metrics_http_server", forbidden_server)
    worker_supervisor.main(["--worker-id", "logical-worker"])

    assert worker_runs == ["logical-worker"]
    assert forbidden == []
    assert operational_metrics.samples() == []


def test_metrics_off_preserves_causality_and_sql_recorder_setting(
    settings_factory, tmp_path
) -> None:
    settings = settings_factory(
        database_url="sqlite+pysqlite:///:memory:",
        upload_dir=tmp_path / "uploads",
        export_dir=tmp_path / "exports",
        cache_dir=tmp_path / "cache",
        db_monitor_log_dir=tmp_path / "logs",
        observability_metrics_enabled=False,
        db_monitor_enabled=True,
    )
    operational_metrics.configure(enabled=False)
    with root_action_scope("ADMINISTRATIVE", "metrics-off-proof"):
        causality = durable_causality_fields()
    assert causality["root_correlation_id"]
    assert settings.db_monitor_enabled is True
    assert settings.observability_metrics_enabled is False
