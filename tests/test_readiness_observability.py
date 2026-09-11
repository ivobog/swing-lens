from __future__ import annotations

from collections import namedtuple
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import Response
from sqlalchemy import create_engine

from app.routers import health_routes
from app.services.readiness_service import ReadinessCheck, ReadinessReport, ReadinessService
from app.settings import RuntimeMode, Settings


@pytest.fixture(autouse=True)
def _reset_observability_collector_state():
    from app.observability.resource_sampler import reset_collector_health

    reset_collector_health()
    yield
    reset_collector_health()


def _service(tmp_path):
    settings = Settings(
        _env_file=None,
        database_url="sqlite+pysqlite:///:memory:",
        upload_dir=tmp_path / "uploads",
        export_dir=tmp_path / "exports",
        cache_dir=tmp_path / "cache",
        db_monitor_log_dir=tmp_path / "logs",
    )
    for path in (
        settings.upload_dir,
        settings.export_dir,
        settings.cache_dir,
        settings.db_monitor_log_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)
    return ReadinessService(engine=create_engine(settings.database_url), settings=settings)


def test_disk_pressure_is_not_ready(tmp_path, monkeypatch) -> None:
    usage = namedtuple("usage", "total used free")(100, 96, 4)
    monkeypatch.setattr("app.services.readiness_service.shutil.disk_usage", lambda _path: usage)
    assert _service(tmp_path)._disk_check().message == "disk_free_critical:4.0%"


def test_db_pool_pressure_is_not_ready(tmp_path) -> None:
    service = _service(tmp_path)
    service.engine = SimpleNamespace(
        pool=SimpleNamespace(
            size=lambda: 10,
            checkedout=lambda: 10,
            overflow=lambda: 0,
            configured_max_overflow=lambda: 0,
        )
    )
    assert service._db_pool_check().message == "db_pool_pressure:10/10"


def test_p0_telemetry_loss_is_not_ready(tmp_path, monkeypatch) -> None:
    monitor = SimpleNamespace(
        enabled=True,
        status=lambda: {"writer_alive": True, "fatal_error": False, "p0_dropped": 1},
    )
    monkeypatch.setattr("app.services.readiness_service.get_database_monitor", lambda: monitor)
    assert _service(tmp_path)._telemetry_check().message == "critical_telemetry_loss:1"


def test_queue_oldest_age_and_depth_pressure_are_not_ready(tmp_path, monkeypatch) -> None:
    now = datetime(2026, 9, 6, 12, tzinfo=UTC)

    class Result:
        @staticmethod
        def one():
            return 1000, 0, 0, 0, 0, 0, now - timedelta(hours=1)

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def execute(_statement):
            return Result()

    service = _service(tmp_path)
    service.now = now
    monkeypatch.setattr("app.services.readiness_service.Session", lambda _engine: FakeSession())

    assert service._queue_pressure_check().message == "queue_depth_critical:1000"


def test_queue_readiness_distinguishes_future_runnable_blocked_and_recovering(
    tmp_path, monkeypatch
) -> None:
    now = datetime(2026, 9, 6, 12, tzinfo=UTC)

    class Result:
        row = (0, 0, 0, 0, 0, 0, None)

        def one(self):
            return self.row

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, _statement):
            return Result()

    monkeypatch.setattr("app.services.readiness_service.Session", lambda _engine: FakeSession())
    service = _service(tmp_path)
    service.now = now

    cases = (
        ((0, 8, 0, 0, 0, 0, None), "runnable=0:scheduled=8", "oldest=0s"),
        ((3, 0, 0, 0, 0, 0, now - timedelta(seconds=5)), "runnable=3", "oldest=5s"),
        ((2, 7, 0, 0, 0, 0, now - timedelta(seconds=9)), "runnable=2:scheduled=7", "oldest=9s"),
        ((0, 0, 4, 0, 0, 0, None), "blocked=4", "oldest=0s"),
        ((0, 0, 0, 6, 0, 0, None), "recovering=6", "oldest=0s"),
    )
    for row, expected, oldest in cases:
        Result.row = row
        check = service._queue_pressure_check()
        assert check.status == "ok"
        assert expected in check.message
        assert oldest in check.message


def test_db_pool_readiness_uses_configured_capacity_and_wait_pressure(tmp_path) -> None:
    service = _service(tmp_path)

    def check(*, size, checked, overflow, maximum, pressure=None):
        service.engine = SimpleNamespace(
            pool=SimpleNamespace(
                size=lambda: size,
                checkedout=lambda: checked,
                overflow=lambda: overflow,
                configured_max_overflow=lambda: maximum,
                observability_status=lambda: pressure or {},
            )
        )
        return service._db_pool_check()

    assert check(size=5, checked=0, overflow=0, maximum=10).status == "ok"
    assert check(size=5, checked=5, overflow=0, maximum=10).status == "ok"
    assert check(size=5, checked=9, overflow=4, maximum=10).status == "ok"
    assert check(size=5, checked=15, overflow=10, maximum=10).status == "failed"
    assert (
        check(
            size=5,
            checked=2,
            overflow=0,
            maximum=10,
            pressure={"recent_timeout": True},
        ).status
        == "degraded"
    )
    assert (
        check(
            size=5,
            checked=2,
            overflow=0,
            maximum=10,
            pressure={"last_wait_seconds": 1.0},
        ).status
        == "degraded"
    )


def test_ib_required_and_optional_states(tmp_path) -> None:
    service = _service(tmp_path)
    service.ib_available = False
    service.settings.observability_ib_required = False
    assert service._ib_check().status == "optional_unavailable"
    service.settings.observability_ib_required = True
    assert service._ib_check().status == "failed"
    service.ib_available = True
    assert service._ib_check().status == "ok"


def test_certification_ib_is_a_separate_pre_enqueue_gate(tmp_path) -> None:
    service = _service(tmp_path)
    service.settings.runtime_mode = RuntimeMode.CERTIFICATION
    service.ib_available = False
    service.settings.observability_ib_required = False

    check = service._ib_check()

    assert check.status == "ok"
    assert check.message == "separate_pre_enqueue_gate"


def test_worker_recorder_and_collector_failures_are_durable_readiness_inputs(
    tmp_path, monkeypatch
) -> None:
    class ScalarResult:
        @staticmethod
        def all():
            return []

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def scalars(_statement):
            return ScalarResult()

    worker = SimpleNamespace(worker_id="worker-1", memory_status="NORMAL")
    monkeypatch.setattr("app.services.readiness_service.Session", lambda _engine: FakeSession())
    monkeypatch.setattr("app.services.readiness_service.live_workers", lambda *_a, **_k: [worker])
    monkeypatch.setattr(
        "app.services.readiness_service.has_live_worker_for_job", lambda *_a, **_k: True
    )
    service = _service(tmp_path)
    service._worker_telemetry_rows = lambda *_a: [("worker-1", "FAILED", "OK", service.now)]
    assert service._worker_check().message == "worker_recorder_failed:worker-1"
    service._worker_telemetry_rows = lambda *_a: [("worker-1", "OK", "FAILED", service.now)]
    assert service._worker_check().message == "worker_collector_failed:worker-1"


def test_resource_collector_dead_and_supervisor_missing(tmp_path, monkeypatch) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr(
        "app.observability.resource_sampler.process_sampler_status",
        lambda _role: {"last_observed_at": service.now - timedelta(minutes=5)},
    )
    assert service._resource_collector_check().message == "collector_dead"

    service.settings.durable_worker_process_enabled = True
    monkeypatch.setattr("app.services.readiness_service.live_supervisors", lambda *_a, **_k: [])

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr("app.services.readiness_service.Session", lambda _engine: FakeSession())
    assert service._supervisor_check().message == "no live durable worker supervisor heartbeat"


def test_database_failure_makes_overall_readiness_failed(tmp_path, monkeypatch) -> None:
    service = _service(tmp_path)
    service.settings.observability_metrics_enabled = False
    monkeypatch.setattr(
        service, "_database_check", lambda: ReadinessCheck(False, "database unavailable")
    )
    report = service.report()
    assert report.status == "failed"
    assert report.checks["database"].status == "failed"
    assert report.checks["jobs"].status == "failed"


def test_ready_http_status_is_200_for_degraded_and_503_for_failed(tmp_path, monkeypatch) -> None:
    settings = _service(tmp_path).settings
    checks = {
        name: ReadinessCheck(True, "ok")
        for name in (
            "database",
            "migrations",
            "storage",
            "supervisor",
            "worker_registered",
            "worker_heartbeat",
            "worker",
            "jobs",
        )
    }
    monkeypatch.setattr(health_routes, "get_settings", lambda: settings)

    for state, expected_http in (("degraded", 200), ("failed", 503)):
        report = ReadinessReport(status=state, checks=checks)
        monkeypatch.setattr(
            health_routes,
            "ReadinessService",
            lambda current=report, **_kwargs: SimpleNamespace(report=lambda: current),
        )
        response = Response()
        payload = health_routes.ready(response)
        assert response.status_code == expected_http
        assert payload.status == state


def test_worker_critical_memory_is_not_ready(tmp_path, monkeypatch) -> None:
    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    worker = SimpleNamespace(worker_id="worker-critical", memory_status="CRITICAL")
    monkeypatch.setattr("app.services.readiness_service.Session", lambda _engine: FakeSession())
    monkeypatch.setattr(
        "app.services.readiness_service.live_workers", lambda *_args, **_kwargs: [worker]
    )
    monkeypatch.setattr(
        "app.services.readiness_service.has_live_worker_for_job",
        lambda *_args, **_kwargs: True,
    )

    assert (
        _service(tmp_path)._worker_check().message
        == "worker_memory_pressure:worker-critical:critical"
    )
