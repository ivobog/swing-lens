from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.models.tables import BackgroundJob
from app.services.background_job_service import JobStatus, is_cancel_requested
from app.services.background_worker import CancelRequested
from app.services.setup_lifecycle.evaluation_service import (
    SetupLifecycleEvaluationCancelled,
    SetupLifecycleEvaluationService,
)
from app.services.setup_lifecycle.maintenance_service import SetupLifecycleMaintenanceService
from app.services.setup_lifecycle.replay_service import (
    SetupLifecycleReplayRequest,
    SetupLifecycleReplayService,
)

SETUP_LIFECYCLE_EVALUATE_RUN = "SETUP_LIFECYCLE_EVALUATE_RUN"
SETUP_LIFECYCLE_REPLAY = "SETUP_LIFECYCLE_REPLAY"
SETUP_LIFECYCLE_REPAIR_TICKER = "SETUP_LIFECYCLE_REPAIR_TICKER"
SETUP_LIFECYCLE_DAILY_MAINTENANCE = "SETUP_LIFECYCLE_DAILY_MAINTENANCE"
SETUP_ALERT_REBUILD = "SETUP_ALERT_REBUILD"

SetupLifecycleJobHandler = Callable[[Session, BackgroundJob], dict[str, Any] | None]


def implemented_setup_lifecycle_job_handlers() -> dict[str, SetupLifecycleJobHandler]:
    return {
        SETUP_LIFECYCLE_EVALUATE_RUN: execute_evaluate_run_job,
        SETUP_LIFECYCLE_REPLAY: execute_replay_job,
        SETUP_LIFECYCLE_REPAIR_TICKER: execute_repair_ticker_job,
        SETUP_LIFECYCLE_DAILY_MAINTENANCE: execute_daily_maintenance_job,
        SETUP_ALERT_REBUILD: execute_alert_rebuild_job,
    }


def execute_evaluate_run_job(
    db: Session,
    job: BackgroundJob,
    *,
    evaluation_service: SetupLifecycleEvaluationService | None = None,
) -> dict[str, Any]:
    run_id = _required_int(job.payload_json or {}, "run_id")
    evaluation_service = evaluation_service or SetupLifecycleEvaluationService()

    try:
        result = evaluation_service.evaluate_run(
            db,
            run_id,
            requester=(job.payload_json or {}).get("requester"),
            should_cancel=lambda: _heartbeat_and_check_cancel(db, job),
        )
    except SetupLifecycleEvaluationCancelled as exc:
        raise CancelRequested(str(exc)) from exc

    values = result.as_dict()
    if values.get("failed", 0):
        job.status = JobStatus.PARTIAL
    return {
        "job_type": SETUP_LIFECYCLE_EVALUATE_RUN,
        "run_id": run_id,
        **values,
    }


def execute_replay_job(
    db: Session,
    job: BackgroundJob,
    *,
    replay_service: SetupLifecycleReplayService | None = None,
) -> dict[str, Any]:
    payload = job.payload_json or {}
    replay_service = replay_service or SetupLifecycleReplayService()
    result = replay_service.replay(
        db,
        SetupLifecycleReplayRequest(
            ticker=payload.get("ticker"),
            date_from=_optional_date(payload, "date_from"),
            date_to=_optional_date(payload, "date_to"),
            persist=bool(payload.get("persist", False)),
            requester=payload.get("requester"),
            reason=payload.get("reason"),
            requested_config=payload.get("requested_config") or {},
        ),
    )
    return {"job_type": SETUP_LIFECYCLE_REPLAY, **result}


def execute_repair_ticker_job(
    db: Session,
    job: BackgroundJob,
    *,
    maintenance_service: SetupLifecycleMaintenanceService | None = None,
) -> dict[str, Any]:
    payload = job.payload_json or {}
    ticker = payload.get("ticker")
    if not ticker:
        raise ValueError(f"{SETUP_LIFECYCLE_REPAIR_TICKER} job payload is missing ticker.")
    maintenance_service = maintenance_service or SetupLifecycleMaintenanceService()
    result = maintenance_service.repair_ticker(
        db,
        ticker=str(ticker),
        as_of_date=_optional_date(payload, "as_of_date"),
        setup_family=payload.get("setup_family"),
        evaluation_run_id=_optional_int(payload, "evaluation_run_id"),
    )
    return {"job_type": SETUP_LIFECYCLE_REPAIR_TICKER, **result.as_dict()}


def execute_daily_maintenance_job(
    db: Session,
    job: BackgroundJob,
    *,
    maintenance_service: SetupLifecycleMaintenanceService | None = None,
) -> dict[str, Any]:
    payload = job.payload_json or {}
    as_of_date = _optional_date(payload, "as_of_date")
    if as_of_date is None:
        raise ValueError(f"{SETUP_LIFECYCLE_DAILY_MAINTENANCE} job payload is missing as_of_date.")
    maintenance_service = maintenance_service or SetupLifecycleMaintenanceService()
    result = maintenance_service.daily_maintenance(
        db,
        as_of_date=as_of_date,
        market_session_completed=bool(payload.get("market_session_completed", True)),
        evaluation_run_id=_optional_int(payload, "evaluation_run_id"),
    )
    if result.status == "SKIPPED":
        job.status = JobStatus.PARTIAL
    return {"job_type": SETUP_LIFECYCLE_DAILY_MAINTENANCE, **result.as_dict()}


def execute_alert_rebuild_job(
    db: Session,
    job: BackgroundJob,
    *,
    maintenance_service: SetupLifecycleMaintenanceService | None = None,
) -> dict[str, Any]:
    payload = job.payload_json or {}
    maintenance_service = maintenance_service or SetupLifecycleMaintenanceService()
    result = maintenance_service.rebuild_alerts(
        db,
        ticker=payload.get("ticker"),
        date_from=_optional_date(payload, "date_from"),
        date_to=_optional_date(payload, "date_to"),
    )
    return {"job_type": SETUP_ALERT_REBUILD, **result.as_dict()}


def _heartbeat_and_check_cancel(db: Session, job: BackgroundJob) -> bool:
    heartbeat = getattr(job, "_heartbeat", None)
    if callable(heartbeat):
        heartbeat()
    return is_cancel_requested(db, job.id)


def _required_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if value is None:
        raise ValueError(f"{SETUP_LIFECYCLE_EVALUATE_RUN} job payload is missing {key}.")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be an integer.") from exc


def _optional_int(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be an integer.") from exc


def _optional_date(payload: dict[str, Any], key: str):
    from datetime import date

    value = payload.get(key)
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"{key} must be an ISO date.") from exc
