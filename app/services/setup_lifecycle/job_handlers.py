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

SETUP_LIFECYCLE_EVALUATE_RUN = "SETUP_LIFECYCLE_EVALUATE_RUN"

SetupLifecycleJobHandler = Callable[[Session, BackgroundJob], dict[str, Any] | None]


def implemented_setup_lifecycle_job_handlers() -> dict[str, SetupLifecycleJobHandler]:
    return {SETUP_LIFECYCLE_EVALUATE_RUN: execute_evaluate_run_job}


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
