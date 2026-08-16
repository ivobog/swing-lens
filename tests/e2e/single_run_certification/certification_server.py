"""Uvicorn entrypoint that installs deterministic read-only QA dependencies.

This module is imported only by the opt-in certification subprocess.  The
application, worker, persistence services, and fetch executor remain real; only
the external IB session is replaced.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace


class DeterministicReadOnlyIB:
    """Small ib-insync-compatible, read-only historical-data adapter."""

    def __init__(self) -> None:
        self._connected = False
        self._log_path = Path(os.environ["CERTIFICATION_IB_LOG"])

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
    end = date.today()
    while end.weekday() >= 5:
        end -= timedelta(days=1)
    dates: list[date] = []
    current = end
    while len(dates) < 320:
        if current.weekday() < 5:
            dates.append(current)
        current -= timedelta(days=1)
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
    from app.services import background_worker
    from app.services.background_job_service import is_cancel_requested
    from app.services.ib_fetch_executor import execute_fetch_plan as real_execute_fetch_plan
    from app.services.ib_gateway_health_service import check_status as real_check_status
    from app.services.pipeline_executor import (
        PipelineCancelled,
        PipelineExecutionDependencies,
        execute_full_pipeline,
    )

    def deterministic_execute_fetch_plan(**kwargs):
        return real_execute_fetch_plan(
            **kwargs,
            ib_client_factory=DeterministicReadOnlyIB,
        )

    def deterministic_check_status():
        return real_check_status(ib_factory=DeterministicReadOnlyIB)

    def execute_full_pipeline_job(db, job):
        pipeline_run_id = job.payload_json.get("pipeline_run_id")
        if pipeline_run_id is None:
            raise ValueError("FULL_PIPELINE job payload is missing pipeline_run_id.")

        def lease_guard() -> None:
            heartbeat = getattr(job, "_heartbeat", None)
            if callable(heartbeat):
                heartbeat()

        def should_cancel() -> bool:
            lease_guard()
            return is_cancel_requested(db, job.id)

        try:
            result = execute_full_pipeline(
                db=db,
                pipeline_run_id=int(pipeline_run_id),
                should_cancel=should_cancel,
                lease_guard=lease_guard,
                dependencies=PipelineExecutionDependencies(
                    execute_fetch_plan=deterministic_execute_fetch_plan,
                    check_ib_gateway=deterministic_check_status,
                ),
            )
        except PipelineCancelled as exc:
            raise background_worker.CancelRequested(str(exc)) from exc
        return result.__dict__

    background_worker._execute_full_pipeline_job = execute_full_pipeline_job


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


_install_deterministic_fetch_dependency()
_install_deterministic_outcome_clock()

from app.main import app  # noqa: E402  (environment and dependency must be installed first)
from app.routers import ib_gateway_admin_routes, run_routes  # noqa: E402
from app.services.ib_gateway_health_service import check_status as real_check_status  # noqa: E402


def _deterministic_route_check_status(*, settings=None):
    return real_check_status(settings=settings, ib_factory=DeterministicReadOnlyIB)


run_routes.check_status = _deterministic_route_check_status
ib_gateway_admin_routes.check_status = _deterministic_route_check_status

__all__ = ["app"]
