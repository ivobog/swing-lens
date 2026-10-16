"""Uvicorn entrypoint that installs deterministic read-only QA dependencies.

This module is imported only by the opt-in certification subprocess.  The
application, worker, persistence services, and fetch executor remain real; only
the external IB session is replaced.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy.engine import make_url

from app.services.us_market_calendar import (
    latest_completed_us_trading_day,
    previous_us_trading_day,
)


class DeterministicReadOnlyIB:
    """Small ib-insync-compatible, read-only historical-data adapter."""

    def __init__(self) -> None:
        self._connected = False
        self._log_path = Path(os.environ["CERTIFICATION_IB_LOG"])
        self.client = SimpleNamespace(
            connect=self._connect_api,
            isReady=lambda: self._connected,
            serverVersion=lambda: 180,
        )

    def _connect_api(self, _host, _port, *, clientId, timeout):
        # Admission probes use the low-level API handshake, without the
        # account/execution synchronization performed by IB.connect().
        self._connected = True
        self._record("api_handshake", {"clientId": clientId, "timeout": timeout})

    def connect(self, _host, _port, **kwargs):
        if kwargs.get("readonly") is not True:
            raise AssertionError("certification IB connections must be read-only")
        self._connected = True
        self._record("connect", {"readonly": True, "clientId": kwargs.get("clientId")})
        return self

    def disconnect(self) -> None:
        self._record("disconnect", {})
        self._connected = False

    def isConnected(self) -> bool:  # noqa: N802 - matches ib-insync
        return self._connected

    def reqCurrentTime(self):  # noqa: N802 - matches ib-insync
        if not self._connected:
            raise RuntimeError("deterministic IB is not connected")
        self._record("current_time", {})
        return datetime.now(UTC)

    def qualifyContracts(self, contract):  # noqa: N802 - matches ib-insync
        if not self._connected:
            raise RuntimeError("deterministic IB is not connected")
        return [contract]

    def reqHistoricalData(self, contract, **kwargs):  # noqa: N802 - matches ib-insync
        ticker = str(contract.symbol).upper()
        what_to_show = str(kwargs.get("whatToShow") or "TRADES")
        self._record(
            "historical_data",
            {
                "ticker": ticker,
                "what_to_show": what_to_show,
                "duration": kwargs.get("durationStr"),
                "bar_size": kwargs.get("barSizeSetting"),
            },
        )
        return _bars_for_ticker(ticker)

    def __getattr__(self, name: str):
        if "order" in name.lower():
            self._record("forbidden_order_api", {"method": name})
            raise AssertionError(f"order API is forbidden in certification: {name}")
        raise AttributeError(name)

    def _record(self, event: str, payload: dict) -> None:
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"event": event, **payload}, sort_keys=True) + "\n")


def _bars_for_ticker(ticker: str) -> list[SimpleNamespace]:
    seed = sum(ord(char) for char in ticker)
    end = latest_completed_us_trading_day(datetime.now(UTC))
    dates: list[date] = []
    current = end
    while len(dates) < 320:
        dates.append(current)
        current = previous_us_trading_day(current)
    dates.reverse()
    bars: list[SimpleNamespace] = []
    for index, bar_date in enumerate(dates):
        close = 60.0 + seed % 35 + index * (0.08 + (seed % 5) * 0.01)
        bars.append(
            SimpleNamespace(
                date=bar_date,
                open=round(close - 0.35, 4),
                high=round(close + 1.10, 4),
                low=round(close - 1.05, 4),
                close=round(close, 4),
                volume=900_000 + seed * 100 + index * 750,
            )
        )
    return bars


def _install_deterministic_fetch_dependency() -> None:
    from app.services import background_worker, pipeline_executor
    from app.services.ceri.artifact_lineage import CeriArtifactOwnership
    from app.services.ceri.feature_rebuild_service import (
        CeriFeatureRebuildRequest,
        CeriFeatureRebuildService,
    )
    from app.services.ib_fetch_executor import execute_fetch_plan as real_execute_fetch_plan
    from app.services.ib_gateway_health_service import check_status as real_check_status
    from app.services.ib_historical_capability import (
        check_historical_data_capability as real_check_historical_data_capability,
    )
    from app.services.market_calculation_context_service import (
        validate_pipeline_job_market_context,
    )
    from app.services.pipeline_executor import PipelineExecutionDependencies

    def deterministic_execute_fetch_plan(**kwargs):
        return real_execute_fetch_plan(
            **kwargs,
            ib_client_factory=DeterministicReadOnlyIB,
        )

    def deterministic_check_status():
        return real_check_status(ib_factory=DeterministicReadOnlyIB)

    def deterministic_check_historical_capability():
        return real_check_historical_data_capability(ib_factory=DeterministicReadOnlyIB)

    def deterministic_pipeline_dependencies(*args, **kwargs):
        kwargs.setdefault("execute_fetch_plan", deterministic_execute_fetch_plan)
        kwargs.setdefault("check_ib_gateway", deterministic_check_status)
        kwargs.setdefault(
            "check_ib_historical_capability",
            deterministic_check_historical_capability,
        )
        return PipelineExecutionDependencies(*args, **kwargs)

    production_execute_full_pipeline_job = background_worker._execute_full_pipeline_job

    def execute_full_pipeline_job(db, job):
        pipeline_run_id = job.payload_json.get("pipeline_run_id")
        if pipeline_run_id is None:
            raise ValueError("FULL_PIPELINE job payload is missing pipeline_run_id.")
        if os.environ.get("CERTIFICATION_FROZEN_CERI_PROVIDERS") != "true":
            market_cutoff = validate_pipeline_job_market_context(
                db,
                pipeline_run_id=int(pipeline_run_id),
                payload=job.payload_json,
            )
            feature_result = CeriFeatureRebuildService().rebuild(
                db,
                CeriFeatureRebuildRequest(
                    as_of_session=market_cutoff.latest_completed_session,
                    cutoff_at=market_cutoff.cutoff_at,
                    mode="AS_KNOWN",
                    calculation_context_id=market_cutoff.context_id,
                    calendar_version=market_cutoff.calendar_version,
                    ownership_mode=CeriArtifactOwnership.PIPELINE.value,
                ),
            )
            if feature_result.failed:
                raise RuntimeError(
                    "disposable certification CERI feature preparation failed: "
                    f"{feature_result.errors}"
                )

        return production_execute_full_pipeline_job(db, job)

    pipeline_executor.PipelineExecutionDependencies = deterministic_pipeline_dependencies
    background_worker._execute_full_pipeline_job = execute_full_pipeline_job


def _install_frozen_ceri_providers() -> None:
    """Freeze external payloads while retaining the production asynchronous DAG."""

    if os.environ.get("CERTIFICATION_FROZEN_CERI_PROVIDERS") != "true":
        return
    from frozen_ceri import frozen_ceri_registry

    registry = frozen_ceri_registry()

    from app.services.ceri import batched_job_handlers, batched_workflow

    batched_workflow.CeriProviderRegistry = lambda *args, **kwargs: registry
    batched_job_handlers.CeriProviderRegistry = lambda *args, **kwargs: registry


def _install_deterministic_outcome_clock() -> None:
    """Freeze the production Winner outcome worker handler for certification."""
    from app.services.winner_probability import job_handlers

    fixed_now = datetime.fromisoformat(os.environ["CERTIFICATION_OUTCOME_NOW"])
    real_execute_outcome_maturation_job = job_handlers.execute_outcome_maturation_job

    def execute_outcome_maturation_job(db, job, **kwargs):
        return real_execute_outcome_maturation_job(
            db,
            job,
            now=kwargs.pop("now", None) or fixed_now,
            **kwargs,
        )

    job_handlers.execute_outcome_maturation_job = execute_outcome_maturation_job
    # Cohort operations consume the future maturation observation. Supply the
    # same explicit simulation clock at the normal operation-clock source.
    job_handlers._utcnow = lambda: fixed_now + timedelta(microseconds=1)

    # Maturation is a new operation, whose business cutoff is frozen when it
    # is enqueued. Advancing only worker time cannot make a C1 job observe C2.
    # The disposable simulation must supply its future clock at admission too.
    from app.routers import winner_probability_routes

    real_enqueue = winner_probability_routes.enqueue_outcome_maturation_workflow

    def enqueue_at_simulated_cutoff(db, *, payload, **kwargs):
        return real_enqueue(
            db,
            payload={**payload, "operation_cutoff_at": fixed_now.isoformat()},
            **kwargs,
        )

    winner_probability_routes.enqueue_outcome_maturation_workflow = enqueue_at_simulated_cutoff


def _install_pipeline_profiler() -> None:
    """Opt-in timings/SQL counts around real stages; no business replacements."""
    if os.environ.get("CERTIFICATION_PROFILE") != "1":
        return
    import cProfile
    import time
    from collections import Counter
    from contextlib import contextmanager

    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    from app.services import pipeline_executor

    output = Path(os.environ["CERTIFICATION_IB_LOG"]).parent
    original_step = pipeline_executor._pipeline_step
    active = None

    def count_sql(_conn, _cursor, statement, _params, _context, _many):
        if active is not None:
            active[statement.lstrip().split(None, 1)[0].upper()] += 1

    event.listen(Engine, "before_cursor_execute", count_sql)

    @contextmanager
    def profiled_step(*args, **kwargs):
        nonlocal active
        counts = Counter()
        previous = active
        active = counts
        started = time.perf_counter()
        profile = cProfile.Profile() if os.environ.get("CERTIFICATION_CPROFILE") == "1" else None
        if profile is not None:
            profile.enable()
        name = kwargs.get("step_name", args[2] if len(args) > 2 else "UNKNOWN")
        try:
            with original_step(*args, **kwargs) as step:
                yield step
        finally:
            if profile is not None:
                profile.disable()
                profile.dump_stats(str(output / f"pipeline-{name}.prof"))
            active = previous
            with (output / "pipeline-profile.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "stage": name,
                            "seconds": time.perf_counter() - started,
                            "sql": dict(counts),
                            "total_sql": sum(counts.values()),
                        }
                    )
                    + "\n"
                )

    pipeline_executor._pipeline_step = profiled_step


def _install_native_configuration_profile() -> None:
    # Select an actual disposable YAML profile before app/worker imports. The
    # production parser and full configuration delivery remain authoritative.
    from app.services.winner_probability import config as native_config

    profile = Path(os.environ["CERTIFICATION_WINNER_CONFIGURATION"])
    original_loader = native_config.load_winner_probability_config

    def load_configuration(path=profile):
        return original_loader(path)

    native_config.load_winner_probability_config = load_configuration


_install_native_configuration_profile()
_install_frozen_ceri_providers()
_install_deterministic_fetch_dependency()
_install_deterministic_outcome_clock()
_install_pipeline_profiler()

from app.db import engine as application_engine  # noqa: E402
from app.main import app  # noqa: E402  (environment and dependency must be installed first)
from app.routers import ib_gateway_admin_routes, run_routes  # noqa: E402
from app.services import pre_enqueue_operational_gate  # noqa: E402
from app.services.ib_gateway_health_service import check_status as real_check_status  # noqa: E402


def _deterministic_route_check_status(*, settings=None):
    return real_check_status(settings=settings, ib_factory=DeterministicReadOnlyIB)


run_routes.check_status = _deterministic_route_check_status
ib_gateway_admin_routes.check_status = _deterministic_route_check_status
pre_enqueue_operational_gate.check_status = _deterministic_route_check_status

trace_installed = False
intercept_installed = False
admission_mode = os.environ.get("CERTIFICATION_ADMISSION_MODE", "normal")
if (
    os.environ.get("CERTIFICATION_TRANSACTION_TRACE_DIR")
    and os.environ.get("PROCESS_ROLE") == "WEB"
):
    from transaction_ownership import (  # noqa: E402
        install_transaction_ownership_trace,
        record_admission_boundary,
        record_trace_event,
    )

    trace_installed = install_transaction_ownership_trace(application_engine)

    import time  # noqa: E402

    from app.services.setup_lifecycle import (  # noqa: E402
        transition_candidate_service as candidate_service,
    )

    def _timed(stage, function, result_size):
        def wrapped(*args, **kwargs):
            started_at = time.perf_counter()
            try:
                result = function(*args, **kwargs)
                return result
            finally:
                record_trace_event(
                    "admission_stage_timing",
                    stage=stage,
                    seconds=round(time.perf_counter() - started_at, 6),
                    result_size=result_size(locals().get("result")),
                )

        return wrapped

    candidate_service.SetupLifecycleSourceLoader.load_run_context = _timed(
        "source_loading",
        candidate_service.SetupLifecycleSourceLoader.load_run_context,
        lambda result: len(result.tickers) if result is not None else None,
    )
    candidate_service.preview_run_technicals = _timed(
        "technical_reconstruction",
        candidate_service.preview_run_technicals,
        lambda result: len(result) if result is not None else None,
    )
    candidate_service.build_run_context_snapshots = _timed(
        "candidate_construction",
        candidate_service.build_run_context_snapshots,
        lambda result: len(result) if result is not None else None,
    )
    candidate_service.TransitionCandidateDiscoveryService.discover_for_run = _timed(
        "candidate_discovery_total",
        candidate_service.TransitionCandidateDiscoveryService.discover_for_run,
        lambda result: len(result) if result is not None else None,
    )

    if admission_mode == "intercept_before_enqueue" and os.environ.get("PROCESS_ROLE") == "WEB":
        from fastapi import HTTPException  # noqa: E402

        database_name = make_url(os.environ["DATABASE_URL"]).database or ""
        if (
            os.environ.get("RUNTIME_MODE") != "CERTIFICATION"
            or os.environ.get("SWINGLENS_DATABASE_SAFETY_CONTEXT") != "DISPOSABLE_TEST"
            or not database_name.startswith("swinglens_pytest_cert_")
            or not trace_installed
        ):
            raise RuntimeError("UNSAFE_CERTIFICATION_ADMISSION_INTERCEPT_CONFIGURATION")

        class _AdmissionInterceptReached(RuntimeError):
            pass

        production_start_pipeline = run_routes.start_pipeline

        def _intercept_after_verification(db, verified_preflight):
            record_admission_boundary(
                db,
                phase="AFTER_INDEPENDENT_VERIFICATION_BEFORE_ADMISSION_MUTATION",
                plan_id=(
                    int(verified_preflight.plan.id) if verified_preflight is not None else None
                ),
            )
            raise _AdmissionInterceptReached

        def _start_pipeline_with_intercept(db, *args, **kwargs):
            kwargs["before_admission_mutation"] = _intercept_after_verification
            try:
                return production_start_pipeline(db, *args, **kwargs)
            except _AdmissionInterceptReached as exc:
                db.rollback()
                record_admission_boundary(
                    db,
                    phase="AFTER_INTERCEPT_ROLLBACK",
                )
                raise HTTPException(
                    status_code=409,
                    detail={"code": "CERTIFICATION_PREFLIGHT_BOUNDARY_REACHED"},
                ) from exc

        run_routes.start_pipeline = _start_pipeline_with_intercept
        intercept_installed = True
        record_trace_event("admission_intercept_installed", admission_mode=admission_mode)


def _write_adapter_status() -> None:
    status_root = os.environ.get("CERTIFICATION_ADAPTER_STATUS_DIR")
    if not status_root:
        return
    status_dir = Path(status_root).resolve()
    status_dir.mkdir(parents=True, exist_ok=True)
    probe = status_dir / f".adapter-write-probe-{os.getpid()}"
    probe.write_text("writable", encoding="utf-8")
    probe.unlink()
    status = {
        "adapter_module": __name__,
        "process_id": os.getpid(),
        "process_role": os.environ.get("PROCESS_ROLE", "UNKNOWN"),
        "execution_id": os.environ.get("SWINGLENS_RUNTIME_INSTANCE_ID"),
        "runtime_mode": os.environ.get("RUNTIME_MODE"),
        "database_safety_context": os.environ.get("SWINGLENS_DATABASE_SAFETY_CONTEXT"),
        "database_name": application_engine.url.database,
        "trace_installed": trace_installed,
        "trace_directory": str(Path(os.environ["CERTIFICATION_TRANSACTION_TRACE_DIR"]).resolve()),
        "trace_directory_writable": True,
        "admission_mode": admission_mode,
        "intercept_installed": intercept_installed,
    }
    target = status_dir / f"adapter-status-{os.getpid()}.json"
    target.write_text(json.dumps(status, indent=2, sort_keys=True), encoding="utf-8")


_write_adapter_status()

__all__ = ["app"]


if __name__ == "__main__":
    from app.services.background_worker import run_worker

    run_worker(
        worker_id=os.environ["JOB_WORKER_ID"],
        queues=("interactive", "broker", "background"),
    )
