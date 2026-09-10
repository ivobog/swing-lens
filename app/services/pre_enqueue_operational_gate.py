from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.tables import MarketCalculationContext, TransitionPreflightPlan
from app.services.alembic_heads import repository_alembic_heads
from app.services.background_queue import job_queue_class
from app.services.certification_runtime import (
    QueueIsolationStatus,
    effective_runtime_configuration,
    is_certification_mode,
    queue_isolation_status,
)
from app.services.ib_gateway_health_service import (
    IBGatewayHealthState,
    IBGatewayHealthStatus,
    check_status,
)
from app.services.transition_preflight_plan_service import (
    TransitionPreflightError,
    VerifiedTransitionPreflight,
    verify_transition_preflight_for_enqueue,
)
from app.services.worker_registry import live_workers
from app.settings import Settings, get_settings

logger = logging.getLogger(__name__)

RUN_WITHOUT_PIPELINE_CLASSIFICATION = "VALID_AUDIT_RECORD"
PLAN_REJECTION_SEMANTICS = "RESERVED_REUSABLE_UNTIL_EXPIRY"
CONTEXT_REJECTION_SEMANTICS = "RESERVED_UNOWNED_REUSABLE_WITH_PLAN"

HealthProbe = Callable[..., IBGatewayHealthStatus]


class PreEnqueueOperationalGateError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        plan_id: int | None,
        context_id: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.plan_id = plan_id
        self.context_id = context_id
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": False,
            "code": self.code,
            "message": self.message,
            "plan_id": self.plan_id,
            "context_id": self.context_id,
            "run_without_pipeline_classification": RUN_WITHOUT_PIPELINE_CLASSIFICATION,
            "plan_state_after_rejection": PLAN_REJECTION_SEMANTICS,
            "context_state_after_rejection": CONTEXT_REJECTION_SEMANTICS,
            **self.details,
        }


@dataclass(frozen=True)
class PreEnqueueOperationalGateResult:
    verified_preflight: VerifiedTransitionPreflight
    ib_status: IBGatewayHealthStatus
    queue_status: QueueIsolationStatus
    worker_count: int
    worker_id: str
    schema_head: str
    checked_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": True,
            "runtime_mode": "CERTIFICATION",
            "worker_count": self.worker_count,
            "worker_id": self.worker_id,
            "queue_isolation": self.queue_status.to_dict(),
            "ib": self.ib_status.to_dict(),
            "schema_head": self.schema_head,
            "checked_at": self.checked_at.isoformat(),
            "plan_id": self.verified_preflight.plan.id,
            "context_id": self.verified_preflight.plan.market_calculation_context_id,
            "run_without_pipeline_classification": RUN_WITHOUT_PIPELINE_CLASSIFICATION,
        }


def validate_pre_enqueue_operational_gate(
    db: Session,
    *,
    upload_run_id: int,
    plan_id: int,
    settings: Settings | None = None,
    discovery: Any | None = None,
    health_probe: HealthProbe = check_status,
    now: datetime | None = None,
    repo_root: Path | None = None,
) -> PreEnqueueOperationalGateResult:
    """Validate the entire certification boundary before Pipeline/Job creation."""
    settings = settings or get_settings()
    observed_at = now or datetime.now(UTC)
    context_id = _validate_reserved_control_state(
        db,
        upload_run_id=upload_run_id,
        plan_id=plan_id,
        observed_at=observed_at,
    )
    if not is_certification_mode(settings):
        _reject(
            "CERTIFICATION_MODE_REQUIRED",
            "The pre-enqueue certification gate requires RUNTIME_MODE=CERTIFICATION.",
            plan_id=plan_id,
            context_id=context_id,
            details={"runtime": effective_runtime_configuration(settings)},
        )

    workers = live_workers(
        db,
        heartbeat_timeout_seconds=settings.job_worker_heartbeat_timeout_seconds,
        now=observed_at,
    )
    capable_workers = [
        worker
        for worker in workers
        if job_queue_class("FULL_PIPELINE") in set(worker.queues_json or [])
        and worker.control_loop_heartbeat_at is not None
        and worker.control_loop_heartbeat_at
        >= observed_at.astimezone(UTC)
        - timedelta(seconds=settings.job_worker_heartbeat_timeout_seconds)
    ]
    if len(workers) != 1 or len(capable_workers) != 1:
        _reject(
            "DURABLE_WORKER_ISOLATION_FAILED",
            "Certification requires exactly one live durable worker capable of FULL_PIPELINE.",
            plan_id=plan_id,
            context_id=context_id,
            details={
                "worker_count": len(workers),
                "full_pipeline_worker_count": len(capable_workers),
            },
        )

    queue_status = queue_isolation_status(
        db,
        now=observed_at,
        allow_authorized_lineage=False,
    )
    if not queue_status.isolated:
        _reject(
            "CERTIFICATION_QUEUE_NOT_ISOLATED",
            "Unrelated runnable or scheduled-due work exists; no pipeline was created.",
            plan_id=plan_id,
            context_id=context_id,
            details={"queue_isolation": queue_status.to_dict()},
        )

    expected_heads = repository_alembic_heads(repo_root or Path.cwd())
    actual_head = str(db.scalar(text("select version_num from alembic_version")) or "")
    if len(expected_heads) != 1 or actual_head != expected_heads[0]:
        _reject(
            "DATABASE_SCHEMA_MISMATCH",
            "Database schema does not match the single repository Alembic head.",
            plan_id=plan_id,
            context_id=context_id,
            details={"expected_heads": list(expected_heads), "actual_head": actual_head},
        )

    ib_status = health_probe(settings=settings)
    ib_decision_at = now if now is not None else datetime.now(UTC)
    if (
        ib_status.status != IBGatewayHealthState.IB_API_READY.value
        or not ib_status.api_ready
        or not ib_status.is_fresh(
            max_age_seconds=settings.ib_readiness_max_age_seconds,
            now=ib_decision_at,
        )
    ):
        _reject(
            "IB_API_NOT_READY",
            ib_status.message,
            plan_id=plan_id,
            context_id=context_id,
            details={"ib": ib_status.to_dict()},
        )

    try:
        verified = verify_transition_preflight_for_enqueue(
            db,
            plan_id=plan_id,
            upload_run_id=upload_run_id,
            discovery=discovery,
            now=observed_at,
        )
    except TransitionPreflightError as exc:
        _reject(
            exc.code,
            str(exc),
            plan_id=plan_id,
            context_id=context_id,
        )

    result = PreEnqueueOperationalGateResult(
        verified_preflight=verified,
        ib_status=ib_status,
        queue_status=queue_status,
        worker_count=1,
        worker_id=str(capable_workers[0].worker_id),
        schema_head=actual_head,
        checked_at=ib_decision_at,
    )
    logger.info("pre_enqueue_operational_gate_passed %s", result.to_dict())
    return result


def _validate_reserved_control_state(
    db: Session,
    *,
    upload_run_id: int,
    plan_id: int,
    observed_at: datetime,
) -> int | None:
    plan = db.get(TransitionPreflightPlan, plan_id)
    if plan is None:
        _reject("STALE_PREFLIGHT", f"plan {plan_id} was not found", plan_id=plan_id)
    context_id = int(plan.market_calculation_context_id)
    if plan.upload_run_id != upload_run_id:
        _reject(
            "STALE_PREFLIGHT",
            "The plan belongs to a different upload run.",
            plan_id=plan_id,
            context_id=context_id,
        )
    if plan.status != "RESERVED":
        _reject(
            "PREFLIGHT_NOT_RESERVED",
            f"The plan is {plan.status}, not RESERVED.",
            plan_id=plan_id,
            context_id=context_id,
        )
    expires_at = plan.expires_at
    if expires_at.tzinfo is None or expires_at.utcoffset() is None or expires_at <= observed_at:
        _reject(
            "PLAN_EXPIRED",
            "The reserved preflight plan has expired.",
            plan_id=plan_id,
            context_id=context_id,
        )
    context = db.get(MarketCalculationContext, context_id)
    if context is None or context.pipeline_run_id is not None:
        _reject(
            "CONTEXT_MISMATCH",
            "The reserved market context is missing or already owned.",
            plan_id=plan_id,
            context_id=context_id,
        )
    return context_id


def _reject(
    code: str,
    message: str,
    *,
    plan_id: int | None,
    context_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    error = PreEnqueueOperationalGateError(
        code,
        message,
        plan_id=plan_id,
        context_id=context_id,
        details=details,
    )
    logger.warning("pre_enqueue_operational_gate_rejected %s", error.to_dict())
    raise error
