from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql

from app.db import get_db
from app.main import create_app
from app.services.operations_service import OperationsService
from app.services.readiness_service import ReadinessCheck, ReadinessReport
from app.settings import Settings


class EmptyResult:
    def all(self):
        return []


class RecordingDb:
    def __init__(self) -> None:
        self.statements = []

    def scalars(self, statement):
        self.statements.append(statement)
        return EmptyResult()

    def execute(self, statement):
        self.statements.append(statement)
        return EmptyResult()


def test_causality_queries_are_bounded(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        database_url="sqlite+pysqlite:///:memory:",
        upload_dir=tmp_path / "uploads",
        export_dir=tmp_path / "exports",
        cache_dir=tmp_path / "cache",
        db_monitor_log_dir=tmp_path / "logs",
        observability_operations_limit=25,
    )
    service = OperationsService(engine=create_engine(settings.database_url), settings=settings)
    db = RecordingDb()

    tree = service.causality_tree(db, "root-test")

    assert tree["jobs"] == []
    assert tree["enqueue_attempts"] == []
    assert len(db.statements) == 2
    assert all(statement._limit_clause is not None for statement in db.statements)


def test_anomalies_include_fanout_and_provider_degradation(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        database_url="sqlite+pysqlite:///:memory:",
        upload_dir=tmp_path / "uploads",
        export_dir=tmp_path / "exports",
        cache_dir=tmp_path / "cache",
        db_monitor_log_dir=tmp_path / "logs",
        observability_fanout_warning=3,
        observability_fanout_critical=5,
    )
    service = OperationsService(engine=create_engine(settings.database_url), settings=settings)
    readiness = ReadinessReport(status="ok", checks={"database": ReadinessCheck(True, "ok")})

    anomalies = service._anomalies(
        readiness,
        [],
        [],
        [{"provider": "SEC", "requests": 10, "failures": 3}],
        {"telemetry": {}},
        [],
        [{"root_correlation_id": "root-1", "attempts": 5}],
    )

    assert {row["type"] for row in anomalies} == {"provider", "fanout"}
    assert next(row for row in anomalies if row["type"] == "fanout")["severity"] == "critical"


def test_provider_snapshot_uses_time_leading_bounded_sample(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        database_url="sqlite+pysqlite:///:memory:",
        upload_dir=tmp_path / "uploads",
        export_dir=tmp_path / "exports",
        cache_dir=tmp_path / "cache",
        db_monitor_log_dir=tmp_path / "logs",
        observability_operations_provider_sample_limit=321,
    )
    service = OperationsService(engine=create_engine(settings.database_url), settings=settings)
    db = RecordingDb()
    assert service._providers(db, datetime(2026, 9, 7, tzinfo=UTC)) == []
    sql = str(
        db.statements[0].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "observed_at >=" in sql
    assert "ORDER BY ceri_provider_request_telemetry.observed_at DESC" in sql
    assert "LIMIT 321" in sql


def test_operations_readiness_is_short_ttl_cached(tmp_path, monkeypatch) -> None:
    settings = Settings(
        _env_file=None,
        database_url="sqlite+pysqlite:///:memory:",
        upload_dir=tmp_path / "uploads",
        export_dir=tmp_path / "exports",
        cache_dir=tmp_path / "cache",
        db_monitor_log_dir=tmp_path / "logs",
        observability_operations_readiness_cache_seconds=10,
    )
    service = OperationsService(engine=create_engine(settings.database_url), settings=settings)
    calls = 0

    class FakeReadiness:
        def __init__(self, **_kwargs):
            pass

        def report(self):
            nonlocal calls
            calls += 1
            return ReadinessReport(status="ok", checks={"database": ReadinessCheck(True, "ok")})

    monkeypatch.setattr("app.services.operations_service.ReadinessService", FakeReadiness)
    now = datetime(2026, 9, 7, tzinfo=UTC)
    assert service._readiness(now).status == "ok"
    assert service._readiness(now).status == "ok"
    assert calls == 1


def test_system_operations_page_and_api_render_bounded_snapshot(tmp_path, monkeypatch) -> None:
    settings = Settings(
        _env_file=None,
        database_url="sqlite+pysqlite:///:memory:",
        upload_dir=tmp_path / "uploads",
        export_dir=tmp_path / "exports",
        cache_dir=tmp_path / "cache",
        db_monitor_log_dir=tmp_path / "logs",
        db_monitor_enabled=False,
        observability_metrics_enabled=False,
        job_worker_enabled=False,
        use_durable_pipeline=False,
    )
    snapshot = {
        "observed_at": "2026-09-06T12:00:00+00:00",
        "health": {"web": {"ok": True, "message": "ok"}},
        "supervisor": None,
        "workers": [],
        "queue": [_empty_queue_fixture("interactive")],
        "pipelines": {"active": [], "recent_failures": []},
        "database": {"pool": {}, "telemetry": {}},
        "providers": [],
        "anomalies": [],
        "recent_roots": [],
        "limits": {"rows": 100},
    }
    monkeypatch.setattr(OperationsService, "snapshot", lambda *_args, **_kwargs: snapshot)
    app = create_app(settings)
    app.dependency_overrides[get_db] = lambda: iter([object()])

    with TestClient(app) as client:
        html = client.get("/system/operations")
        payload = client.get("/api/system/operations")

    assert html.status_code == 200
    assert "System Operations" in html.text
    assert payload.status_code == 200
    assert payload.json()["limits"] == {"rows": 100}


def _empty_queue_fixture(queue_class: str) -> dict:
    return {
        "queue_class": queue_class,
        "runnable": 0,
        "scheduled": 0,
        "blocked": 0,
        "running": 0,
        "stalled": 0,
        "recovering": 0,
        "failed_recent": 0,
        "oldest_age_seconds": 0.0,
    }
