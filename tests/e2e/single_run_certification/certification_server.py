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
        if os.environ.get("CERTIFICATION_GOLDEN_VERSION"):
            from single_run_certification.golden_oracle import load_golden

            return datetime.fromisoformat(load_golden()["business_time"]["market_cutoff"])
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
    if os.environ.get("CERTIFICATION_GOLDEN_VERSION"):
        from single_run_certification.golden_oracle import artifact

        return [SimpleNamespace(date=date.fromisoformat(row["date"]), **{
            key: float(row[key]) for key in ("open", "high", "low", "close", "volume")
        }) for row in artifact("market-data.json")["ib_replay"][ticker]]
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
    """Replace client creation only; retain the original production handler."""
    from sqlalchemy.engine import make_url

    from app.services import (
        background_worker,
        ib_connection,
        ib_fetch_executor,
        ib_gateway_health_service,
        ib_historical_capability,
    )
    from app.settings import RuntimeMode, get_settings

    settings = get_settings()
    database = make_url(settings.database_url)
    if (
        settings.runtime_mode is not RuntimeMode.CERTIFICATION
        or os.environ.get("SWINGLENS_DATABASE_SAFETY_CONTEXT") != "DISPOSABLE_TEST"
        or not (database.database or "").startswith("swinglens_pytest_cert_")
        or database.host not in {"127.0.0.1", "localhost", "::1"}
    ):
        raise RuntimeError("DETERMINISTIC_ADAPTER_REQUIRES_DISPOSABLE_CERTIFICATION")

    original_handler = background_worker._execute_full_pipeline_job
    if (
        original_handler.__module__ != "app.services.background_worker"
        or original_handler.__qualname__ != "_execute_full_pipeline_job"
        or background_worker.default_job_handlers()["FULL_PIPELINE"] is not original_handler
    ):
        raise RuntimeError("CERTIFICATION_FULL_PIPELINE_HANDLER_REPLACED")

    # The native functions have imported aliases and IB-valued default arguments.
    # Invoke the saved factory to retain its event-loop setup, supplying only the
    # external client. No health/capability/fetch or executor callable is changed.
    native_create_client = ib_connection.create_ib_client

    def deterministic_create_client(_ib_factory=None):
        return native_create_client(DeterministicReadOnlyIB)

    for module in (
        ib_connection,
        ib_gateway_health_service,
        ib_historical_capability,
        ib_fetch_executor,
    ):
        module.create_ib_client = deterministic_create_client

    # Deny Python provider sockets (including a live IB on loopback). PostgreSQL
    # and this web listener are the only permitted application endpoints.
    import socket

    allowed_ports = {database.port or 5432, settings.app_port}
    socketpair_code = getattr(socket.socketpair, "__code__", None)

    def network_guard(event, args):
        if event not in {"socket.connect", "socket.getaddrinfo"}:
            return
        address = args[1] if event == "socket.connect" else args[:2]
        caller = sys._getframe(1)
        # Windows implements socketpair using a just-created loopback listener.
        # Permit only that exact stdlib frame/listener, never arbitrary local IB.
        if event == "socket.connect" and caller.f_code is socketpair_code:
            listener = caller.f_locals.get("lsock")
            if listener is not None and listener.getsockname()[:2] == address[:2]:
                return
        if (
            not isinstance(address, tuple)
            or len(address) < 2
            or address[0] not in {"127.0.0.1", "localhost", "::1"}
            or address[1] not in allowed_ports
        ):
            raise RuntimeError("CERTIFICATION_UNEXPECTED_NETWORK_ACCESS")

    import sys

    sys.addaudithook(network_guard)
    _observe_production_handler(original_handler)


def _observe_production_handler(original_handler) -> None:
    """Observe native code objects without wrapping/rebinding any worker code."""
    log_path = os.environ.get("CERTIFICATION_WORKER_FIDELITY_LOG")
    if not log_path:
        return
    import sys
    from collections import Counter
    from types import CodeType

    from app.services import background_worker, pipeline_executor
    from app.services.configuration_delivery import current_delivery
    from app.services.redaction import redacted_token_metadata

    output = Path(log_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    callback_codes = {
        code: code.co_name
        for code in original_handler.__code__.co_consts
        if isinstance(code, CodeType)
        and code.co_name in {"lease_guard", "should_cancel", "progress_callback", "memory_probe"}
    }
    calls = {}
    callbacks = {}
    executor_code = pipeline_executor.execute_full_pipeline.__wrapped__.__code__
    final_status_code = pipeline_executor._final_pipeline_status.__code__

    def write(record):
        with output.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"pid": os.getpid(), **record}, sort_keys=True) + "\n")

    def started(code, _offset):
        frame = sys._getframe(1)
        if code is final_status_code:
            # Observe the exact deciding object without wrapping production code
            # or accessing its transaction. Public results omit three inputs.
            values = json.loads(json.dumps(frame.f_locals["result"], default=str))
            partial_keys = (
                "incomplete_rows", "warning_rows", "ib_failure_count",
                "technical_error_count", "fetch_failed", "market_regime_low_confidence",
                "ceri_failed", "setup_lifecycle_failed", "winner_prediction_failed",
            )
            write({
                "event": "native_final_status_inputs", "result": values,
                "partial_predicates": {key: bool(values[key]) for key in partial_keys},
                "first_partial_predicate": next((key for key in partial_keys if values[key]), None),
            })
            return
        if code in callback_codes:
            owner = frame.f_back
            while owner is not None and owner.f_code is not original_handler.__code__:
                owner = owner.f_back
            if owner is not None:
                callbacks.setdefault(id(owner), Counter())[callback_codes[code]] += 1
            return
        if code is executor_code:
            values = frame.f_locals
            write({
                "event": "native_executor_entered",
                "job_id": values["execution_job_id"],
                **redacted_token_metadata(values["execution_token"]),
                "callbacks": {
                    name: getattr(values[name], "__qualname__", None)
                    for name in (
                        "lease_guard", "should_cancel", "progress_callback", "memory_probe",
                    )
                },
                "independent_control_factory": dict(zip(
                    values["should_cancel"].__code__.co_freevars,
                    (cell.cell_contents for cell in values["should_cancel"].__closure__),
                    strict=True,
                )).get("control_factory") is not None,
            })
            return
        job = frame.f_locals["job"]
        values = job.__dict__
        delivery = current_delivery()
        ancestors = []
        caller = frame.f_back
        while caller is not None:
            ancestors.append(caller.f_code.co_name)
            caller = caller.f_back
        record = {
            "event": "native_handler_entered",
            "job_id": values["id"],
            "pipeline_id": values["payload_json"]["pipeline_run_id"],
            "resume_from_step": values["payload_json"].get("resume_from_step"),
            "worker_id": values.get("worker_id"),
            "worker_instance_id": values.get("worker_instance_id"),
            "certification_session_id": values["payload_json"].get("certification_session_id"),
            "scope_id": values.get("scope_id"),
            "refresh_cycle_id": values.get("refresh_cycle_id"),
            "configuration_anchor": delivery.anchor["fingerprint"] if delivery else None,
            "handler": original_handler.__module__ + "." + original_handler.__qualname__,
            "registered_original": (
                background_worker.default_job_handlers()["FULL_PIPELINE"] is original_handler
                and background_worker._execute_full_pipeline_job is original_handler
            ),
            "native_worker_dispatch": "execute_job" in ancestors and "run_worker_once" in ancestors,
            **redacted_token_metadata(values.get("execution_token")),
        }
        calls[id(frame)] = record
        write(record)

    def finished(_code, _offset, result):
        if _code is final_status_code:
            write({"event": "native_final_status_returned", "status": result})
            return
        call_id = id(sys._getframe(1))
        write({
            **calls.pop(call_id),
            "event": "native_handler_raised" if isinstance(result, BaseException)
            else "native_handler_returned",
            "pipeline_status": result.get("status") if isinstance(result, dict) else None,
            "error_type": type(result).__name__ if isinstance(result, BaseException) else None,
            "callback_calls": dict(callbacks.pop(call_id, {})),
        })

    # Local CPython monitoring events incur no per-frame trace/profile hook and
    # leave the production functions, registration and call path untouched.
    tool_id = next(index for index in range(6) if sys.monitoring.get_tool(index) is None)
    sys.monitoring.use_tool_id(tool_id, "certification-worker-fidelity")
    sys.monitoring.register_callback(tool_id, sys.monitoring.events.PY_START, started)
    sys.monitoring.register_callback(tool_id, sys.monitoring.events.PY_RETURN, finished)
    for code in (original_handler.__code__, executor_code, *callback_codes):
        sys.monitoring.set_local_events(tool_id, code, sys.monitoring.events.PY_START)
    sys.monitoring.set_local_events(
        tool_id, original_handler.__code__,
        sys.monitoring.events.PY_START | sys.monitoring.events.PY_RETURN,
    )
    sys.monitoring.set_local_events(
        tool_id, final_status_code,
        sys.monitoring.events.PY_START | sys.monitoring.events.PY_RETURN,
    )
    write({"event": "native_handler_registered", "registered_original": True})


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
if os.environ.get("CERTIFICATION_GOLDEN_VERSION"):
    from app.settings import get_settings as _golden_settings
    from single_run_certification.golden_oracle import VERSION as _golden_version
    from single_run_certification.golden_runtime import business_inputs

    if os.environ["CERTIFICATION_GOLDEN_VERSION"] != _golden_version:
        raise RuntimeError("UNSUPPORTED_CERTIFICATION_GOLDEN_VERSION")
    _golden_business_scope = business_inputs(_golden_settings().database_url)
    _golden_business_scope.__enter__()
_install_deterministic_outcome_clock()
_install_pipeline_profiler()

from app.main import app  # noqa: E402  (environment and dependency must be installed first)

__all__ = ["app"]


if __name__ == "__main__":
    from app.services.background_worker import run_worker

    run_worker(
        worker_id=os.environ["JOB_WORKER_ID"],
        queues=("interactive", "broker", "background"),
    )
