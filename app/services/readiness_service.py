from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.tables import BackgroundJob
from app.services.background_job_service import JobStatus
from app.services.redaction import redact_text
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
        checks = {
            "database": self._database_check(),
            "migrations": self._migration_check(),
            "storage": self._storage_check(),
            "worker": self._worker_check(),
            "jobs": self._jobs_check(),
        }
        status = "ok" if all(check.ok for check in checks.values()) else "degraded"
        return ReadinessReport(status=status, checks=checks)

    def _database_check(self) -> ReadinessCheck:
        try:
            with self.engine.connect() as connection:
                connection.execute(text("select 1"))
        except SQLAlchemyError as exc:
            return ReadinessCheck(False, _safe_message(exc))
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
        if not self.settings.job_worker_enabled:
            return ReadinessCheck(False, "durable pipeline worker is disabled")
        return ReadinessCheck(True, f"configured:{self.settings.job_worker_id}")

    def _jobs_check(self) -> ReadinessCheck:
        try:
            with Session(self.engine) as session:
                stale_count = _stale_job_count(session, self.now)
        except SQLAlchemyError as exc:
            return ReadinessCheck(False, _safe_message(exc))
        if stale_count:
            return ReadinessCheck(False, f"stale_running_jobs:{stale_count}")
        return ReadinessCheck(True, "ok")


def _probe_directory(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    probe = directory / ".swinglens_ready_probe"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink(missing_ok=True)


def _stale_job_count(session: Session, now: datetime) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(BackgroundJob)
            .where(BackgroundJob.status == JobStatus.RUNNING)
            .where(BackgroundJob.lease_expires_at.is_not(None))
            .where(BackgroundJob.lease_expires_at < now)
        )
        or 0
    )


def _repository_alembic_heads() -> list[str]:
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    return sorted(script.get_heads())


def _safe_message(exc: Exception) -> str:
    return redact_text(str(exc))
