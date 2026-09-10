from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import case, create_engine, func, select, text
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.models.tables import BackgroundJob, BackgroundSupervisor, BackgroundWorker
from app.observability.db_monitor import get_database_monitor
from app.observability.logging import log_event
from app.services.alembic_heads import database_alembic_heads, repository_alembic_heads
from app.services.background_job_service import JobStatus
from app.services.redaction import redact_text
from app.services.supervisor_registry import live_supervisors
from app.services.worker_registry import has_live_worker_for_job, live_workers
from app.settings import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReadinessCheck:
    ok: bool
    message: str
    status: str | None = None

    def __post_init__(self) -> None:
        if self.status is None:
            object.__setattr__(self, "status", "ok" if self.ok else "failed")


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

    def response_states(self) -> dict[str, str]:
        return {name: str(check.status) for name, check in self.checks.items()}


class ReadinessService:
    def __init__(
        self,
        *,
        engine: Engine,
        settings: Settings,
        now: datetime | None = None,
        ib_available: bool | None = None,
    ) -> None:
        self.engine = engine
        self.settings = settings
        self.now = now or datetime.now(UTC)
        self.ib_available = ib_available

    def report(self, *, database_session: Session | None = None) -> ReadinessReport:
        database = (
            self._database_check()
            if database_session is None
            else self._database_check(database_session)
        )
        if database.ok:
            migrations = self._migration_check()
            jobs = self._jobs_check()
            supervisor = self._supervisor_check()
            worker_registered = self._worker_registration_check()
            worker_heartbeat = self._worker_heartbeat_check()
            worker = self._worker_check()
            sec = self._sec_provider_check()
            queue_pressure = self._queue_pressure_check()
            db_pool = self._db_pool_check()
        else:
            dependency_message = "skipped: database unavailable"
            migrations = ReadinessCheck(False, dependency_message)
            jobs = ReadinessCheck(False, dependency_message)
            supervisor = ReadinessCheck(False, dependency_message)
            worker_registered = ReadinessCheck(False, dependency_message)
            worker_heartbeat = ReadinessCheck(False, dependency_message)
            worker = ReadinessCheck(False, dependency_message)
            sec = ReadinessCheck(False, dependency_message)
            queue_pressure = ReadinessCheck(False, dependency_message)
            db_pool = ReadinessCheck(False, dependency_message)
        checks = {
            "database": database,
            "migrations": migrations,
            "storage": self._storage_check(),
            "disk": self._disk_check(),
            "supervisor": supervisor,
            "worker_registered": worker_registered,
            "worker_heartbeat": worker_heartbeat,
            "worker": worker,
            "jobs": jobs,
            "queue_pressure": queue_pressure,
            "db_pool": db_pool,
            "telemetry": self._telemetry_check(),
            "metrics": self._metrics_configuration_check(),
            "resource_sampler": self._resource_collector_check(),
            "system_collector": self._system_collector_check(),
            "ib": self._ib_check(),
            "sec": sec,
        }
        states = {str(check.status) for check in checks.values()}
        status = "failed" if "failed" in states else ("degraded" if states - {"ok"} else "ok")
        if status != "ok":
            log_event(
                logger,
                "readiness.degraded",
                level=logging.WARNING,
                failed_checks=[name for name, check in checks.items() if not check.ok],
            )
        return ReadinessReport(status=status, checks=checks)

    def _database_check(self, session: Session | None = None) -> ReadinessCheck:
        probe_engine: Engine | None = None
        try:
            if session is not None:
                session.execute(text("select 1"))
                return ReadinessCheck(True, "ok")
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
            expected_heads = tuple(_repository_alembic_heads())
            with self.engine.connect() as connection:
                current_heads = database_alembic_heads(connection)
        except (OSError, SQLAlchemyError) as exc:
            return ReadinessCheck(False, _safe_message(exc))
        if current_heads != expected_heads:
            return ReadinessCheck(
                False,
                "migration head mismatch: "
                f"current={','.join(current_heads) or '<missing>'}; "
                f"expected={','.join(expected_heads) or '<missing>'}",
            )
        return ReadinessCheck(True, f"ok:{','.join(current_heads)}")

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

    def _disk_check(self) -> ReadinessCheck:
        try:
            rows = []
            for directory in (
                self.settings.upload_dir,
                self.settings.export_dir,
                self.settings.cache_dir,
                self.settings.db_monitor_log_dir,
            ):
                directory.mkdir(parents=True, exist_ok=True)
                usage = shutil.disk_usage(directory.resolve())
                rows.append(usage.free / usage.total * 100 if usage.total else 0.0)
        except OSError as exc:
            return ReadinessCheck(False, _safe_message(exc))
        minimum = min(rows, default=100.0)
        if minimum <= self.settings.observability_disk_critical_percent:
            return ReadinessCheck(False, f"disk_free_critical:{minimum:.1f}%")
        if minimum <= self.settings.observability_disk_warning_percent:
            return ReadinessCheck(False, f"disk_free_warning:{minimum:.1f}%", "degraded")
        return ReadinessCheck(True, f"ok:{minimum:.1f}%")

    def _queue_pressure_check(self) -> ReadinessCheck:
        try:
            with Session(self.engine) as session:
                row = session.execute(
                    select(
                        func.sum(
                            case(
                                (
                                    (BackgroundJob.status == JobStatus.QUEUED)
                                    & (BackgroundJob.run_after <= self.now),
                                    1,
                                ),
                                else_=0,
                            )
                        ),
                        func.sum(
                            case(
                                (
                                    (BackgroundJob.status == JobStatus.QUEUED)
                                    & (BackgroundJob.run_after > self.now),
                                    1,
                                ),
                                else_=0,
                            )
                        ),
                        func.sum(case((BackgroundJob.status == JobStatus.BLOCKED, 1), else_=0)),
                        func.sum(case((BackgroundJob.status == JobStatus.RECOVERING, 1), else_=0)),
                        func.sum(case((BackgroundJob.status == JobStatus.STALLED, 1), else_=0)),
                        func.sum(case((BackgroundJob.status == JobStatus.RUNNING, 1), else_=0)),
                        func.min(
                            case(
                                (
                                    (BackgroundJob.status == JobStatus.QUEUED)
                                    & (BackgroundJob.run_after <= self.now),
                                    BackgroundJob.created_at,
                                ),
                                else_=None,
                            )
                        ),
                    ).where(
                        BackgroundJob.status.in_(
                            (
                                JobStatus.QUEUED,
                                JobStatus.BLOCKED,
                                JobStatus.RECOVERING,
                                JobStatus.STALLED,
                                JobStatus.RUNNING,
                            )
                        )
                    )
                ).one()
        except SQLAlchemyError as exc:
            return ReadinessCheck(False, _safe_message(exc))
        depth, scheduled, blocked, recovering, stalled, running, oldest = row
        depth = int(depth or 0)
        oldest_age = 0.0
        if oldest is not None:
            if oldest.tzinfo is None:
                oldest = oldest.replace(tzinfo=UTC)
            oldest_age = max(0.0, (self.now - oldest).total_seconds())
        if (
            self.settings.observability_queue_depth_critical
            and depth >= self.settings.observability_queue_depth_critical
        ):
            return ReadinessCheck(False, f"queue_depth_critical:{depth}", "degraded")
        if oldest_age >= self.settings.observability_queue_oldest_critical_seconds:
            return ReadinessCheck(False, f"queue_oldest_critical:{oldest_age:.0f}s")
        if oldest_age >= self.settings.observability_queue_oldest_warning_seconds:
            return ReadinessCheck(False, f"queue_oldest_warning:{oldest_age:.0f}s", "degraded")
        return ReadinessCheck(
            True,
            "ok:"
            f"runnable={depth}:scheduled={int(scheduled or 0)}:"
            f"blocked={int(blocked or 0)}:recovering={int(recovering or 0)}:"
            f"stalled={int(stalled or 0)}:running={int(running or 0)}:"
            f"oldest={oldest_age:.0f}s",
        )

    def _db_pool_check(self) -> ReadinessCheck:
        pool = self.engine.pool
        size_callback = getattr(pool, "size", None)
        checked_callback = getattr(pool, "checkedout", None)
        overflow_callback = getattr(pool, "overflow", None)
        if not callable(size_callback) or not callable(checked_callback):
            return ReadinessCheck(True, "not applicable")
        size = max(0, int(size_callback()))
        checked = max(0, int(checked_callback()))
        overflow = max(0, int(overflow_callback())) if callable(overflow_callback) else 0
        configured_callback = getattr(pool, "configured_max_overflow", None)
        configured_overflow = (
            max(0, int(configured_callback()))
            if callable(configured_callback)
            else int(self.settings.database_pool_max_overflow)
        )
        capacity = size + configured_overflow
        ratio = checked / capacity if capacity else 0.0
        status_callback = getattr(pool, "observability_status", None)
        pressure = status_callback() if callable(status_callback) else {}
        if pressure.get("recent_timeout"):
            return ReadinessCheck(False, "db_pool_recent_timeout", "degraded")
        if (
            float(pressure.get("last_wait_seconds") or 0)
            >= self.settings.observability_db_pool_wait_warning_seconds
            > 0
        ):
            return ReadinessCheck(False, "db_pool_wait_pressure", "degraded")
        if checked >= capacity or ratio >= self.settings.observability_db_pool_critical_ratio:
            return ReadinessCheck(False, f"db_pool_pressure:{checked}/{capacity}")
        return ReadinessCheck(True, f"ok:{checked}/{capacity}:overflow_in_use={overflow}")

    def _telemetry_check(self) -> ReadinessCheck:
        monitor = get_database_monitor()
        if monitor is None or not monitor.enabled:
            return ReadinessCheck(True, "optional_unavailable", "optional_unavailable")
        status = monitor.status()
        if status.get("fatal_error") or not status.get("writer_alive"):
            return ReadinessCheck(False, "telemetry_writer_failure")
        if int(status.get("p0_dropped") or 0) > 0:
            return ReadinessCheck(False, f"critical_telemetry_loss:{status['p0_dropped']}")
        return ReadinessCheck(True, "ok")

    def _resource_collector_check(self) -> ReadinessCheck:
        if not self.settings.observability_metrics_enabled:
            return ReadinessCheck(True, "disabled_by_configuration", "optional_unavailable")
        from app.observability.resource_sampler import (
            collector_component_status,
            process_sampler_status,
        )

        state = collector_component_status("web", "resource_sampler")
        category_state = process_sampler_status("web")
        observed_at = state.get("last_success")
        if not isinstance(observed_at, datetime):
            # Compatibility for an in-flight process started by the previous release.
            observed_at = category_state.get("last_observed_at")
        if not isinstance(observed_at, datetime):
            return ReadinessCheck(False, "collector_not_started")
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=UTC)
        if (self.now - observed_at).total_seconds() > max(
            5.0, self.settings.observability_collection_interval_seconds * 2.5
        ):
            return ReadinessCheck(False, "collector_dead")
        failed = int(state.get("consecutive_failures") or 0)
        if failed:
            return ReadinessCheck(False, f"resource_sampler_failed:{failed}", "degraded")
        failed_categories = sorted(
            category
            for category in ("process_memory", "cpu")
            if category_state.get(category) == "failed"
        )
        if failed_categories:
            return ReadinessCheck(
                False,
                "resource_sampler_failed:" + ",".join(failed_categories),
                "degraded",
            )
        return ReadinessCheck(True, "ok")

    def _system_collector_check(self) -> ReadinessCheck:
        if not self.settings.observability_metrics_enabled:
            return ReadinessCheck(True, "disabled_by_configuration", "optional_unavailable")
        from app.observability.resource_sampler import (
            collector_component_status,
            process_sampler_status,
        )

        state = collector_component_status("web", "system_metrics_collector")
        observed_at = state.get("last_success")
        if not isinstance(observed_at, datetime):
            return ReadinessCheck(False, "system_collector_not_started")
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=UTC)
        if (self.now - observed_at).total_seconds() > max(
            5.0, self.settings.observability_collection_interval_seconds * 2.5
        ):
            return ReadinessCheck(False, "system_collector_dead")
        failures = int(state.get("consecutive_failures") or 0)
        if failures:
            return ReadinessCheck(False, f"system_collector_failed:{failures}", "degraded")
        category_state = process_sampler_status("web")
        ignored = {"last_observed_at", "process_memory", "cpu"}
        failed_categories = sorted(
            name
            for name, value in category_state.items()
            if name not in ignored and value == "failed"
        )
        if failed_categories:
            return ReadinessCheck(
                False,
                "system_collector_failed:" + ",".join(failed_categories),
                "degraded",
            )
        return ReadinessCheck(True, "ok")

    def _metrics_configuration_check(self) -> ReadinessCheck:
        if self.settings.observability_metrics_enabled:
            return ReadinessCheck(True, "enabled")
        return ReadinessCheck(True, "disabled_by_configuration", "optional_unavailable")

    def _ib_check(self) -> ReadinessCheck:
        workload_required = self._ib_workload_required()
        required = bool(self.settings.observability_ib_required or workload_required)
        available = self.ib_available
        if available is None and required:
            from app.services.ib_gateway_health_service import (
                check_status,
                is_api_ready_status,
            )

            available = is_api_ready_status(check_status(self.settings))
        if available:
            return ReadinessCheck(True, "available")
        if required:
            detail = "runnable_work" if workload_required else "configuration"
            return ReadinessCheck(False, f"required_unavailable:{detail}")
        return ReadinessCheck(True, "optional_unavailable", "optional_unavailable")

    def _ib_workload_required(self) -> bool:
        """One bounded aggregate capability query; no per-job inspection."""
        try:
            with Session(self.engine) as session:
                required_work = (
                    select(BackgroundJob.id)
                    .where(
                        BackgroundJob.status.in_(
                            (
                                JobStatus.QUEUED,
                                JobStatus.RUNNING,
                                JobStatus.RECOVERING,
                                JobStatus.STALLED,
                            )
                        )
                    )
                    .where(
                        (BackgroundJob.status != JobStatus.QUEUED)
                        | (BackgroundJob.run_after <= self.now)
                    )
                    .where(
                        BackgroundJob.job_type.in_(("FULL_PIPELINE", "MARKET_DATA_PREWARM"))
                        | BackgroundJob.job_type.like(r"IB\_%", escape="\\")
                    )
                    .limit(1)
                )
                required = session.scalar(select(required_work.exists()))
            return bool(required)
        except SQLAlchemyError:
            # Database health is reported independently; never turn an optional
            # dependency into a false requirement because this aggregate failed.
            return bool(self.settings.observability_ib_required)

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
                scalars = getattr(session, "scalars", None)
                runnable_types = (
                    list(
                        scalars(
                            select(BackgroundJob.job_type)
                            .where(BackgroundJob.status == JobStatus.QUEUED)
                            .where(BackgroundJob.run_after <= self.now)
                            .distinct()
                            .limit(100)
                        ).all()
                    )
                    if callable(scalars)
                    else ["FULL_PIPELINE"]
                )
                missing_capabilities = [
                    job_type
                    for job_type in runnable_types
                    if not has_live_worker_for_job(
                        session,
                        job_type=str(job_type),
                        heartbeat_timeout_seconds=(
                            self.settings.job_worker_heartbeat_timeout_seconds
                        ),
                        now=self.now,
                    )
                ]
                telemetry = self._worker_telemetry_rows(session, [row.worker_id for row in workers])
        except SQLAlchemyError as exc:
            return ReadinessCheck(False, _safe_message(exc))
        if not workers:
            return ReadinessCheck(False, "no live durable worker heartbeat")
        if missing_capabilities:
            return ReadinessCheck(
                False,
                "no live worker can process:" + ",".join(sorted(map(str, missing_capabilities))),
            )
        for telemetry_row in telemetry:
            worker_id, telemetry_status, collector_status, collector_at = telemetry_row[:4]
            control_at = telemetry_row[4] if len(telemetry_row) > 4 else None
            if str(telemetry_status or "UNKNOWN").upper() == "FAILED":
                return ReadinessCheck(False, f"worker_recorder_failed:{worker_id}")
            if str(collector_status or "UNKNOWN").upper() == "FAILED":
                return ReadinessCheck(False, f"worker_collector_failed:{worker_id}")
            if collector_at is not None and _age_seconds(self.now, collector_at) > max(
                5, int(self.settings.observability_collection_interval_seconds * 2.5)
            ):
                return ReadinessCheck(False, f"worker_collector_dead:{worker_id}")
            if control_at is None or _age_seconds(self.now, control_at) > int(
                self.settings.job_worker_heartbeat_timeout_seconds
            ):
                return ReadinessCheck(False, f"worker_control_loop_dead:{worker_id}")
        pressured = [
            worker
            for worker in workers
            if str(worker.memory_status or "").upper() in {"WARNING", "CRITICAL"}
        ]
        if pressured:
            detail = ",".join(
                f"{worker.worker_id}:{str(worker.memory_status).lower()}" for worker in pressured
            )
            status = (
                "failed"
                if any(
                    str(worker.memory_status or "").upper() == "CRITICAL" for worker in pressured
                )
                else "degraded"
            )
            return ReadinessCheck(False, f"worker_memory_pressure:{detail}", status)
        worker_ids = ",".join(worker.worker_id for worker in workers)
        return ReadinessCheck(True, f"live:{worker_ids}")

    def _worker_telemetry_rows(self, session: Session, worker_ids: list[str]) -> list[tuple]:
        if not worker_ids or not self._table_has_column("background_workers", "telemetry_status"):
            return []
        return list(
            session.execute(
                select(
                    BackgroundWorker.worker_id,
                    BackgroundWorker.telemetry_status,
                    BackgroundWorker.resource_collector_status,
                    BackgroundWorker.resource_collector_heartbeat_at,
                    BackgroundWorker.control_loop_heartbeat_at,
                ).where(BackgroundWorker.worker_id.in_(worker_ids))
            ).all()
        )

    def _table_has_column(self, table: str, column: str) -> bool:
        try:
            return column in {item["name"] for item in sa_inspect(self.engine).get_columns(table)}
        except Exception:
            return False

    def _supervisor_check(self) -> ReadinessCheck:
        if not self.settings.job_worker_enabled:
            return ReadinessCheck(True, "not managed")
        try:
            with Session(self.engine) as session:
                supervisors = live_supervisors(
                    session,
                    heartbeat_timeout_seconds=(self.settings.job_worker_heartbeat_timeout_seconds),
                    now=self.now,
                )
        except SQLAlchemyError as exc:
            return ReadinessCheck(False, _safe_message(exc))
        matching = [row for row in supervisors if row.worker_id == self.settings.job_worker_id]
        if not matching:
            return ReadinessCheck(False, "no live durable worker supervisor heartbeat")
        row = matching[0]
        if self._table_has_column("background_supervisors", "telemetry_status"):
            try:
                with Session(self.engine) as session:
                    telemetry_status, collector_status, collector_at, control_at = session.execute(
                        select(
                            BackgroundSupervisor.telemetry_status,
                            BackgroundSupervisor.resource_collector_status,
                            BackgroundSupervisor.resource_collector_heartbeat_at,
                            BackgroundSupervisor.control_loop_heartbeat_at,
                        ).where(BackgroundSupervisor.worker_id == row.worker_id)
                    ).one()
            except SQLAlchemyError as exc:
                return ReadinessCheck(False, _safe_message(exc))
            if str(telemetry_status or "UNKNOWN").upper() == "FAILED":
                return ReadinessCheck(False, "supervisor_telemetry_failed")
            if str(collector_status or "UNKNOWN").upper() == "FAILED":
                return ReadinessCheck(False, "supervisor_collector_failed")
            if collector_at is not None and _age_seconds(self.now, collector_at) > max(
                5, int(self.settings.observability_collection_interval_seconds * 2.5)
            ):
                return ReadinessCheck(False, "supervisor_collector_dead")
            if control_at is None or _age_seconds(self.now, control_at) > int(
                self.settings.job_worker_heartbeat_timeout_seconds
            ):
                return ReadinessCheck(False, "supervisor_control_loop_dead")
        return ReadinessCheck(
            True, f"live:{row.worker_id}:{row.instance_id}:generation-{row.generation}"
        )

    def _worker_registration_check(self) -> ReadinessCheck:
        if not self.settings.use_durable_pipeline:
            return ReadinessCheck(True, "not required")
        try:
            with Session(self.engine) as session:
                registrations = list(
                    session.scalars(
                        select(BackgroundWorker)
                        .where(BackgroundWorker.stopping_at.is_(None))
                        .order_by(BackgroundWorker.worker_id)
                    ).all()
                )
        except SQLAlchemyError as exc:
            return ReadinessCheck(False, _safe_message(exc))
        if not registrations:
            return ReadinessCheck(False, "no active durable worker registration")
        valid = [
            row
            for row in registrations
            if row.instance_id and row.process_id and row.process_started_at
        ]
        if not valid:
            return ReadinessCheck(False, "worker registration lacks process-instance identity")
        return ReadinessCheck(True, "registered:" + ",".join(row.worker_id for row in valid))

    def _worker_heartbeat_check(self) -> ReadinessCheck:
        if not self.settings.use_durable_pipeline:
            return ReadinessCheck(True, "not required")
        try:
            with Session(self.engine) as session:
                workers = live_workers(
                    session,
                    heartbeat_timeout_seconds=(self.settings.job_worker_heartbeat_timeout_seconds),
                    now=self.now,
                )
        except SQLAlchemyError as exc:
            return ReadinessCheck(False, _safe_message(exc))
        if not workers:
            return ReadinessCheck(False, "no fresh durable worker heartbeat")
        return ReadinessCheck(True, "fresh:" + ",".join(row.worker_id for row in workers))

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
    return list(repository_alembic_heads())


def _safe_message(exc: Exception) -> str:
    return redact_text(str(exc))


def _age_seconds(observed_at: datetime, value: datetime) -> int:
    comparable = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return max(0, int((observed_at - comparable).total_seconds()))
