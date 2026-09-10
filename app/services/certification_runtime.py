from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session

from app.models.tables import BackgroundJob
from app.observability.correlation import CausalityContext
from app.settings import RuntimeMode, Settings, get_settings

CERTIFICATION_AUTHORIZATION_KEY = "certification_authorized"
CERTIFICATION_PLAN_KEY = "transition_preflight_plan_id"
CERTIFICATION_ROOT_JOB_TYPE = "FULL_PIPELINE"

# These are control-plane writes rather than business workflows. They remain
# available so the web process and one durable worker can prove liveness.
CERTIFICATION_ALLOWED_CONTROL_ACTIVITY = (
    "web_runtime",
    "worker_registration",
    "worker_heartbeat",
    "worker_control_loop_heartbeat",
    "supervisor_heartbeat",
    "transition_preflight_read_and_lock",
)

CERTIFICATION_DISABLED_AUTOMATIC_WORKFLOWS = (
    "winner_primary_h5_maturation",
    "winner_cohort_refresh",
    "winner_latest_rescore",
    "winner_prediction_capture",
    "ceri_unrelated_background_work",
    "market_data_prewarm",
    "provider_prefetch",
    "cache_refresh",
    "durable_evidence_retention",
    "stale_job_recovery",
    "unrelated_retry_and_continuation_claims",
)


class CertificationRuntimeViolation(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class QueueIsolationStatus:
    isolated: bool
    unrelated_runnable_jobs: int
    checked_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "isolated": self.isolated,
            "unrelated_runnable_jobs": self.unrelated_runnable_jobs,
            "checked_at": self.checked_at.isoformat(),
        }


def is_certification_mode(settings: Settings | None = None) -> bool:
    return (
        getattr(settings or get_settings(), "runtime_mode", RuntimeMode.NORMAL)
        is RuntimeMode.CERTIFICATION
    )


def certification_root_payload(*, plan_id: int) -> dict[str, Any]:
    return {
        CERTIFICATION_AUTHORIZATION_KEY: True,
        CERTIFICATION_PLAN_KEY: int(plan_id),
    }


def require_enqueue_authorized(
    db: Session,
    *,
    job_type: str,
    payload: dict[str, Any],
    causality: CausalityContext,
    settings: Settings | None = None,
) -> None:
    """Fail closed for every job creation outside the authorized canary lineage."""
    if not is_certification_mode(settings):
        return
    if _is_authorized_root(job_type, payload):
        return
    if causality.root_correlation_id and _authorized_root_exists(db, causality.root_correlation_id):
        return
    raise CertificationRuntimeViolation(
        "CERTIFICATION_JOB_NOT_AUTHORIZED",
        f"{job_type} is not part of the explicitly authorized certification pipeline",
    )


def certification_claim_filter() -> Any:
    """SQL predicate that permits only an authorized root and its descendants."""
    authorized_roots = select(BackgroundJob.root_correlation_id).where(
        BackgroundJob.job_type == CERTIFICATION_ROOT_JOB_TYPE,
        _payload_authorized_expression(),
        BackgroundJob.root_correlation_id.is_not(None),
    )
    return func.coalesce(
        or_(
            and_(
                BackgroundJob.job_type == CERTIFICATION_ROOT_JOB_TYPE,
                _payload_authorized_expression(),
            ),
            BackgroundJob.root_correlation_id.in_(authorized_roots),
        ),
        False,
    )


def queue_isolation_status(
    db: Session,
    *,
    now: datetime | None = None,
    allow_authorized_lineage: bool = False,
) -> QueueIsolationStatus:
    observed_at = now or datetime.now(UTC)
    runnable = or_(
        BackgroundJob.status == "RUNNING",
        and_(
            BackgroundJob.status.in_(("QUEUED", "RECOVERING", "RETRYING")),
            BackgroundJob.run_after <= observed_at,
        ),
    )
    query = select(func.count(BackgroundJob.id)).where(runnable)
    if allow_authorized_lineage:
        query = query.where(~certification_claim_filter())
    count = int(db.scalar(query) or 0)
    return QueueIsolationStatus(
        isolated=count == 0,
        unrelated_runnable_jobs=count,
        checked_at=observed_at,
    )


def apply_certification_claim_scope(query: Select[Any]) -> Select[Any]:
    return query.where(certification_claim_filter())


def effective_runtime_configuration(settings: Settings) -> dict[str, Any]:
    certification = is_certification_mode(settings)
    return {
        "runtime_mode": settings.runtime_mode.value,
        "certification_isolation_active": certification,
        "effective_disabled_automatic_workflows": (
            list(CERTIFICATION_DISABLED_AUTOMATIC_WORKFLOWS) if certification else []
        ),
        "allowed_control_activity": (
            list(CERTIFICATION_ALLOWED_CONTROL_ACTIVITY) if certification else []
        ),
        "authorized_root_job_type": (CERTIFICATION_ROOT_JOB_TYPE if certification else None),
    }


def _is_authorized_root(job_type: str, payload: dict[str, Any]) -> bool:
    return (
        job_type == CERTIFICATION_ROOT_JOB_TYPE
        and payload.get(CERTIFICATION_AUTHORIZATION_KEY) is True
        and isinstance(payload.get(CERTIFICATION_PLAN_KEY), int)
        and int(payload[CERTIFICATION_PLAN_KEY]) > 0
    )


def _authorized_root_exists(db: Session, root_correlation_id: str) -> bool:
    return bool(
        db.scalar(
            select(BackgroundJob.id)
            .where(
                BackgroundJob.job_type == CERTIFICATION_ROOT_JOB_TYPE,
                BackgroundJob.root_correlation_id == root_correlation_id,
                _payload_authorized_expression(),
            )
            .limit(1)
        )
    )


def _payload_authorized_expression() -> Any:
    return BackgroundJob.payload_json[CERTIFICATION_AUTHORIZATION_KEY].as_boolean().is_(True)
