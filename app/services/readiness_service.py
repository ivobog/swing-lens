from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.models.tables import BackgroundJob
from app.services.background_job_service import JobStatus
from app.services.redaction import redact_text
from app.services.worker_registry import has_live_worker_for_job, live_workers
from app.settings import Settings


@dataclass(frozen=True)
class ReadinessCheck:
    ok: bool
    message: str


@dataclass(frozen=True)
class ReadinessReport:
    status: str
    checks: dict[str, ReadinessCheck]

    @property
    def database_ok(self) -> bool:
        return self.checks["database"].ok

    @property
    def local_dirs_ok(self) -> bool:
        return self.checks["storage"].ok

    def response_checks(self) -> dict[str, str]:
        return {name: check.message for name, check in self.checks.items()}


class ReadinessService:
    def __init__(
        self,
        *,
        engine: Engine,
        settings: Settings,
        now: datetime | None = None,
    ) -> None:
        self.engine = engine
        self.settings = settings
        self.now = now or datetime.now(UTC)

    def report(self) -> ReadinessReport:
        database = self._database_check()
        if database.ok:
            migrations = self._migration_check()
            jobs = self._jobs_check()
            worker = self._worker_check()
            sec = self._sec_provider_check()
        else:
            dependency_message = "skipped: database unavailable"
            migrations = ReadinessCheck(False, dependency_message)
            jobs = ReadinessCheck(False, dependency_message)
            worker = ReadinessCheck(False, dependency_message)
            sec = ReadinessCheck(False, dependency_message)
        checks = {
            "database": database,
            "migrations": migrations,
            "storage": self._storage_check(),
            "worker": worker,
            "jobs": jobs,
            "sec": sec,
        }
        status = "ok" if all(check.ok for check in checks.values()) else "degraded"
        return ReadinessReport(status=status, checks=checks)

    def _database_check(self) -> ReadinessCheck:
        probe_engine: Engine | None = None
        try:
            engine_url = getattr(self.engine, "url", None)
            driver_name = getattr(engine_url, "drivername", "")
            if str(driver_name).startswith("postgresql"):
                probe_engine = create_engine(
                    engine_url,
                    poolclass=NullPool,
                    connect_args={
                        "connect_timeout": self.settings.database_connect_timeout_seconds
                    },
                )
            connection_engine = probe_engine or self.engine
            with connection_engine.connect() as connection:
                connection.execute(text("select 1"))
        except SQLAlchemyError as exc:
            return ReadinessCheck(False, _safe_message(exc))
        finally:
            if probe_engine is not None:
                probe_engine.dispose()
        return ReadinessCheck(True, "ok")

    def _migration_check(self) -> ReadinessCheck:
        try:
            expected_heads = _repository_alembic_heads()
            with self.engine.connect() as connection:
                current = connection.execute(
                    text("select version_num from alembic_version limit 1")
                ).scalar()
        except (OSError, SQLAlchemyError) as exc:
            return ReadinessCheck(False, _safe_message(exc))
        if current not in expected_heads:
            return ReadinessCheck(
                False,
                f"migration head mismatch: current={current or '<missing>'}",
            )
        return ReadinessCheck(True, f"ok:{current}")

    def _storage_check(self) -> ReadinessCheck:
        try:
            for directory in (
                self.settings.upload_dir,
                self.settings.export_dir,
                self.settings.cache_dir,
            ):
                _probe_directory(directory)
        except OSError as exc:
            return ReadinessCheck(False, _safe_message(exc))
        return ReadinessCheck(True, "ok")

    def _worker_check(self) -> ReadinessCheck:
        if not self.settings.use_durable_pipeline:
            return ReadinessCheck(True, "not required")
        try:
            with Session(self.engine) as session:
                workers = live_workers(
                    session,
                    heartbeat_timeout_seconds=(self.settings.job_worker_heartbeat_timeout_seconds),
                    now=self.now,
                )
                capable = has_live_worker_for_job(
                    session,
                    job_type="FULL_PIPELINE",
                    heartbeat_timeout_seconds=(self.settings.job_worker_heartbeat_timeout_seconds),
                    now=self.now,
                )
        except SQLAlchemyError as exc:
            return ReadinessCheck(False, _safe_message(exc))
        if not workers:
            return ReadinessCheck(False, "no live durable worker heartbeat")
        if not capable:
            return ReadinessCheck(False, "no live worker can process FULL_PIPELINE")
        pressured = [
            worker
            for worker in workers
            if str(worker.memory_status or "").upper() in {"WARNING", "CRITICAL"}
        ]
        if pressured:
            detail = ",".join(
                f"{worker.worker_id}:{str(worker.memory_status).lower()}" for worker in pressured
            )
            return ReadinessCheck(False, f"worker_memory_pressure:{detail}")
        worker_ids = ",".join(worker.worker_id for worker in workers)
        return ReadinessCheck(True, f"live:{worker_ids}")

    def _sec_provider_check(self) -> ReadinessCheck:
        if not self.settings.ceri_provider_ingest_enabled:
            return ReadinessCheck(True, "not required")
        try:
            from app.services.ceri.config import load_ceri_config
            from app.services.ceri.enums import (
                CeriDataset,
                CeriProvider,
                CeriProviderCapability,
            )
            from app.services.ceri.sec.processor_lifecycle import lifecycle_state

            config = load_ceri_config()
            guidance = config.datasets.get(CeriDataset.GUIDANCE)
            capabilities = config.providers.capabilities.get(CeriProvider.SEC, ())
            with Session(self.engine) as session:
                processor = lifecycle_state(session)
            if not guidance or not guidance.enabled:
                return ReadinessCheck(False, "SEC guidance dataset is disabled")
            if CeriProviderCapability.GUIDANCE not in capabilities:
                return ReadinessCheck(False, "SEC guidance capability is not configured")
            if not processor.deployed_is_active:
                return ReadinessCheck(False, "deployed SEC processor is not ACTIVE")
        except (OSError, SQLAlchemyError, ValueError) as exc:
            return ReadinessCheck(False, _safe_message(exc))
        return ReadinessCheck(True, f"ready:{processor.active_signature}")

    def _jobs_check(self) -> ReadinessCheck:
        try:
            with Session(self.engine) as session:
                stale_count, stalled_count, recovering_count = _unhealthy_job_counts(
                    session, self.now
                )
        except SQLAlchemyError as exc:
            return ReadinessCheck(False, _safe_message(exc))
        if stale_count:
            return ReadinessCheck(False, f"stale_running_jobs:{stale_count}")
        if stalled_count:
            return ReadinessCheck(False, f"stalled_jobs:{stalled_count}")
        if recovering_count:
            return ReadinessCheck(False, f"recovering_jobs:{recovering_count}")
        return ReadinessCheck(True, "ok")


def _probe_directory(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    probe = directory / ".swinglens_ready_probe"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink(missing_ok=True)


def _unhealthy_job_counts(session: Session, now: datetime) -> tuple[int, int, int]:
    stale = int(
        session.scalar(
            select(func.count())
            .select_from(BackgroundJob)
            .where(BackgroundJob.status == JobStatus.RUNNING)
            .where(BackgroundJob.lease_expires_at.is_not(None))
            .where(BackgroundJob.lease_expires_at < now)
        )
        or 0
    )
    stalled = int(
        session.scalar(
            select(func.count())
            .select_from(BackgroundJob)
            .where(BackgroundJob.status == JobStatus.STALLED)
        )
        or 0
    )
    recovering = int(
        session.scalar(
            select(func.count())
            .select_from(BackgroundJob)
            .where(BackgroundJob.status == JobStatus.RECOVERING)
        )
        or 0
    )
    return stale, stalled, recovering


def _repository_alembic_heads() -> list[str]:
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    return sorted(script.get_heads())


def _safe_message(exc: Exception) -> str:
    return redact_text(str(exc))
