from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from app.models.tables import BackgroundJob
from app.services.background_job_service import JobStatus, enqueue_job, mark_job_completed
from app.services.csv_export import write_csv
from app.services.operational_metrics import operational_metrics
from app.services.readiness_service import ReadinessService
from app.settings import Settings


def test_readiness_degrades_for_migration_mismatch(tmp_path, monkeypatch) -> None:
    engine = _readiness_engine(alembic_revision="old-head")
    monkeypatch.setattr(
        "app.services.readiness_service._repository_alembic_heads",
        lambda: ["head"],
    )

    report = ReadinessService(engine=engine, settings=_settings(tmp_path)).report()

    assert report.status == "degraded"
    assert report.checks["migrations"].ok is False
    assert "migration head mismatch" in report.checks["migrations"].message


def test_readiness_degrades_for_stale_running_jobs(tmp_path, monkeypatch) -> None:
    now = datetime(2026, 8, 3, 12, tzinfo=UTC)
    engine = _readiness_engine(alembic_revision="head")
    with engine.begin() as connection:
        connection.execute(
            text(
                "insert into background_jobs (status, lease_expires_at) "
                "values ('RUNNING', :lease_expires_at)"
            ),
            {"lease_expires_at": now - timedelta(seconds=1)},
        )
    monkeypatch.setattr(
        "app.services.readiness_service._repository_alembic_heads",
        lambda: ["head"],
    )

    report = ReadinessService(engine=engine, settings=_settings(tmp_path), now=now).report()

    assert report.status == "degraded"
    assert report.checks["jobs"].ok is False
    assert report.checks["jobs"].message == "stale_running_jobs:1"


def test_readiness_degrades_for_storage_failure_and_redacts_error(tmp_path, monkeypatch) -> None:
    engine = _readiness_engine(alembic_revision="head")
    monkeypatch.setattr(
        "app.services.readiness_service._repository_alembic_heads",
        lambda: ["head"],
    )

    def fail_probe(_directory):
        raise OSError(
            r"C:\Users\Ivica\Documents\secret.txt failed; "
            "Bearer abc123; select * from users where password='x'"
        )

    monkeypatch.setattr("app.services.readiness_service._probe_directory", fail_probe)

    report = ReadinessService(engine=engine, settings=_settings(tmp_path)).report()

    assert report.status == "degraded"
    assert report.checks["storage"].ok is False
    assert report.checks["storage"].message == "<restricted:sql>"
    assert "abc123" not in str(report.response_checks())
    assert "Ivica" not in str(report.response_checks())


def test_readiness_uses_live_external_worker_heartbeat_even_when_embedded_is_disabled(
    tmp_path, monkeypatch
) -> None:
    now = datetime(2026, 8, 12, 13, tzinfo=UTC)
    engine = _readiness_engine(alembic_revision="head", worker_heartbeat_at=now)
    monkeypatch.setattr(
        "app.services.readiness_service._repository_alembic_heads",
        lambda: ["head"],
    )
    settings = _settings(tmp_path)
    settings.job_worker_enabled = False

    report = ReadinessService(engine=engine, settings=settings, now=now).report()

    assert report.checks["worker"].ok is True
    assert report.checks["worker"].message == "live:test-worker"


def test_readiness_degrades_for_stale_external_worker_heartbeat(tmp_path, monkeypatch) -> None:
    now = datetime(2026, 8, 12, 13, tzinfo=UTC)
    engine = _readiness_engine(
        alembic_revision="head",
        worker_heartbeat_at=now - timedelta(seconds=31),
    )
    monkeypatch.setattr(
        "app.services.readiness_service._repository_alembic_heads",
        lambda: ["head"],
    )

    report = ReadinessService(engine=engine, settings=_settings(tmp_path), now=now).report()

    assert report.checks["worker"].ok is False
    assert report.checks["worker"].message == "no live durable worker heartbeat"


def test_readiness_short_circuits_database_dependent_checks_when_unavailable(
    tmp_path, monkeypatch
) -> None:
    class UnavailableEngine:
        def connect(self):
            raise OperationalError("select 1", {}, OSError("database unavailable"))

    def unexpected_check(_self):
        raise AssertionError("database-dependent readiness check should have been skipped")

    monkeypatch.setattr(ReadinessService, "_migration_check", unexpected_check)
    monkeypatch.setattr(ReadinessService, "_jobs_check", unexpected_check)

    report = ReadinessService(
        engine=UnavailableEngine(),  # type: ignore[arg-type]
        settings=_settings(tmp_path),
    ).report()

    assert report.status == "degraded"
    assert report.database_ok is False
    assert report.checks["migrations"].message == "skipped: database unavailable"
    assert report.checks["jobs"].message == "skipped: database unavailable"


def test_job_and_export_paths_emit_operational_metrics() -> None:
    operational_metrics.reset()
    db = FakeJobDb()

    queued = enqueue_job(db, "FULL_PIPELINE", {"pipeline_run_id": 7})
    running = BackgroundJob(
        id=2,
        job_type="FULL_PIPELINE",
        status=JobStatus.RUNNING,
        execution_token="token-1",
        operational_metadata_json={},
    )
    mark_job_completed(db, running, {"ok": True}, execution_token="token-1")
    write_csv(["ticker"], [{"ticker": "MSFT"}], schema_id="swinglens.test.export.v1")

    assert queued.status == JobStatus.QUEUED
    prometheus = operational_metrics.as_prometheus()
    assert 'swinglens_jobs_enqueued_total{job_type="FULL_PIPELINE"} 1' in prometheus
    assert (
        'swinglens_jobs_finished_total{job_type="FULL_PIPELINE",status="COMPLETED"} 1' in prometheus
    )
    assert 'swinglens_exports_generated_total{schema_id="swinglens.test.export.v1"} 1' in prometheus
    assert 'swinglens_export_rows_total{schema_id="swinglens.test.export.v1"} 1' in prometheus
    assert operational_metrics.total("swinglens_jobs_finished_total") == 1
    assert (
        operational_metrics.total(
            "swinglens_jobs_finished_total",
            status="COMPLETED",
        )
        == 1
    )


def _readiness_engine(
    *,
    alembic_revision: str,
    worker_heartbeat_at: datetime | None = None,
):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    heartbeat_at = worker_heartbeat_at or datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(text("create table alembic_version (version_num text not null)"))
        connection.execute(
            text("insert into alembic_version values (:revision)"),
            {"revision": alembic_revision},
        )
        connection.execute(
            text("create table background_jobs (status text, lease_expires_at timestamp)")
        )
        connection.execute(
            text(
                "create table background_workers ("
                "worker_id text primary key, queues_json text not null, hostname text, "
                "process_id integer, started_at timestamp not null, "
                "heartbeat_at timestamp not null, stopping_at timestamp, "
                "instance_id text, rss_bytes integer, private_bytes integer, memory_status text)"
            )
        )
        connection.execute(
            text(
                "insert into background_workers "
                "(worker_id, queues_json, started_at, heartbeat_at) "
                'values (\'test-worker\', \'["interactive","broker","background"]\', '
                ":started_at, :heartbeat_at)"
            ),
            {"started_at": heartbeat_at, "heartbeat_at": heartbeat_at},
        )
    return engine


def _settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        upload_dir=tmp_path / "uploads",
        export_dir=tmp_path / "exports",
        cache_dir=tmp_path / "cache",
        job_worker_enabled=True,
        use_durable_pipeline=True,
    )


class FakeJobDb:
    def __init__(self) -> None:
        self.added = []
        self.jobs = []
        self.flushes = 0

    def add(self, row) -> None:
        self.added.append(row)
        if isinstance(row, BackgroundJob):
            self.jobs.append(row)

    def flush(self) -> None:
        self.flushes += 1


@pytest.fixture(autouse=True)
def _reset_metrics():
    operational_metrics.reset()
    yield
    operational_metrics.reset()
