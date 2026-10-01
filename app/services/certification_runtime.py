from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import BigInteger, Select, and_, cast, exists, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.models.tables import BackgroundJob, PipelineRun
from app.observability.correlation import CausalityContext
from app.services.runtime_mutation_authority import CERTIFICATION_CONTROL_MUTATIONS
from app.settings import RuntimeMode, Settings, get_settings

CERTIFICATION_AUTHORIZATION_KEY = "certification_authorized"
CERTIFICATION_PLAN_KEY = "transition_preflight_plan_id"
CERTIFICATION_SESSION_KEY = "certification_session_id"
CERTIFICATION_ROOT_JOB_TYPE = "FULL_PIPELINE"
CERI_FEATURE_CERTIFICATION_ROOT_JOB_TYPE = "CERI_FEATURE_CERTIFICATION"
CERTIFICATION_ROOT_JOB_TYPES = (
    CERTIFICATION_ROOT_JOB_TYPE,
    CERI_FEATURE_CERTIFICATION_ROOT_JOB_TYPE,
)
CERI_FEATURE_CERTIFICATION_CONTRACT_VERSION = "ceri-feature-certification-v1"
CERI_FEATURE_CERTIFICATION_STOP_BOUNDARY = "FEATURE_ONLY"
CERI_FEATURE_CERTIFICATION_MAX_TICKERS = 2
CERI_FEATURE_CERTIFICATION_ALLOWED_PROVIDER_DATASETS = {
    "eodhd": ("earnings", "estimates"),
}
CERTIFICATION_ACTIVE_ROOT_STATUSES = (
    "QUEUED",
    "RECOVERING",
    "RETRYING",
    "RUNNING",
)
# FULL_PIPELINE completion can mean durable child dispatch completed while the
# pipeline remains at WAITING_DEPENDENCY.  Explicitly session-authorized
# descendants must remain claimable until that asynchronous lineage drains.
# Failed/cancelled roots are intentionally excluded.
CERTIFICATION_LINEAGE_ROOT_STATUSES = (
    *CERTIFICATION_ACTIVE_ROOT_STATUSES,
    "COMPLETED",
)
CERTIFICATION_RECOVERABLE_ROOT_STATUSES = (
    *CERTIFICATION_ACTIVE_ROOT_STATUSES,
    "STALLED",
)

# These are control-plane writes rather than business workflows. They remain
# available so the web process and one durable worker can prove liveness.
CERTIFICATION_ALLOWED_CONTROL_ACTIVITY = tuple(sorted(CERTIFICATION_CONTROL_MUTATIONS))

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


def require_certification_session_id(
    settings: Settings | None = None,
    *,
    session_id: str | None = None,
) -> str:
    value = str(
        session_id or getattr(settings or get_settings(), "runtime_instance_id", None) or ""
    ).strip()
    if not value:
        raise CertificationRuntimeViolation(
            "CERTIFICATION_SESSION_REQUIRED",
            "a canonical runtime instance id is required before certification work can start",
        )
    return value


def certification_root_payload(
    *, plan_id: int, settings: Settings | None = None, session_id: str | None = None
) -> dict[str, Any]:
    return {
        CERTIFICATION_AUTHORIZATION_KEY: True,
        CERTIFICATION_PLAN_KEY: int(plan_id),
        CERTIFICATION_SESSION_KEY: require_certification_session_id(
            settings, session_id=session_id
        ),
    }


def ceri_feature_certification_root_payload(
    *, settings: Settings | None = None, session_id: str | None = None
) -> dict[str, Any]:
    """Return the explicit capability marker for the bounded feature-only root."""

    return {
        CERTIFICATION_AUTHORIZATION_KEY: True,
        CERTIFICATION_SESSION_KEY: require_certification_session_id(
            settings, session_id=session_id
        ),
        "certification_contract_version": CERI_FEATURE_CERTIFICATION_CONTRACT_VERSION,
        "stop_boundary": CERI_FEATURE_CERTIFICATION_STOP_BOUNDARY,
    }


def require_enqueue_authorized(
    db: Session,
    *,
    job_type: str,
    payload: dict[str, Any],
    causality: CausalityContext,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Return the payload bound to the current session or fail before creation.

    Descendants receive their own explicit authorization marker.  The parent
    relationship is only an enqueue-time delegation check; correlation ancestry
    is never a claim-time capability.
    """
    if not is_certification_mode(settings):
        return payload
    session_id = require_certification_session_id(settings)
    if _is_authorized_root(job_type, payload, session_id=session_id):
        return dict(payload)
    parent_job_id = causality.parent_job_id or causality.triggered_by_job_id
    if parent_job_id and _authorized_parent_exists(
        db,
        parent_job_id=parent_job_id,
        root_correlation_id=causality.root_correlation_id,
        session_id=session_id,
    ):
        return {
            **payload,
            CERTIFICATION_AUTHORIZATION_KEY: True,
            CERTIFICATION_SESSION_KEY: session_id,
        }
    raise CertificationRuntimeViolation(
        "CERTIFICATION_JOB_NOT_AUTHORIZED",
        f"{job_type} is not part of the explicitly authorized certification pipeline",
    )


def certification_claim_filter(*, session_id: str) -> Any:
    """Match jobs explicitly authorized for this epoch and a live root.

    Root correlation is only a cancellation/liveness constraint here.  It can
    narrow an already explicit authorization but can never grant one.
    """
    required_session = require_certification_session_id(session_id=session_id)
    exact_session = and_(
        _payload_authorized_expression(),
        _payload_session_expression(required_session),
    )
    active_roots = select(BackgroundJob.root_correlation_id).where(
        BackgroundJob.job_type.in_(CERTIFICATION_ROOT_JOB_TYPES),
        _payload_authorized_expression(),
        _payload_session_expression(required_session),
        BackgroundJob.root_correlation_id.is_not(None),
        _certification_root_has_live_authority(),
    )
    return func.coalesce(
        and_(
            exact_session,
            or_(
                and_(
                    BackgroundJob.job_type.in_(CERTIFICATION_ROOT_JOB_TYPES),
                    _certification_root_has_live_authority(),
                ),
                BackgroundJob.root_correlation_id.in_(active_roots),
            ),
        ),
        False,
    )


def _certification_root_has_live_authority(job: Any = BackgroundJob) -> Any:
    """Allow completed dispatch roots only while their pipeline remains live."""

    pipeline_id = cast(job.payload_json["pipeline_run_id"].as_string(), BigInteger)
    live_pipeline = exists(
        select(PipelineRun.id).where(
            PipelineRun.id == pipeline_id,
            PipelineRun.execution_authority_state == "ACTIVE",
            PipelineRun.status.not_in({"COMPLETED", "PARTIAL", "FAILED", "BLOCKED", "CANCELLED"}),
        )
    )
    return or_(
        job.status.in_(CERTIFICATION_ACTIVE_ROOT_STATUSES),
        and_(job.status == "COMPLETED", live_pipeline),
    )


def queue_isolation_status(
    db: Session,
    *,
    now: datetime | None = None,
    allow_authorized_lineage: bool = False,
    ignore_inactive_authorized_lineage: bool = False,
    certification_session_id: str | None = None,
) -> QueueIsolationStatus:
    observed_at = now or datetime.now(UTC)
    runnable = or_(
        and_(
            BackgroundJob.status == "RUNNING",
            or_(
                BackgroundJob.lease_expires_at.is_(None),
                BackgroundJob.lease_expires_at > observed_at,
            ),
        ),
        and_(
            BackgroundJob.status.in_(("QUEUED", "RECOVERING", "RETRYING")),
            BackgroundJob.run_after <= observed_at,
        ),
    )
    query = select(func.count(BackgroundJob.id)).where(runnable)
    if allow_authorized_lineage:
        query = query.where(
            ~certification_claim_filter(
                session_id=require_certification_session_id(session_id=certification_session_id)
            )
        )
    if ignore_inactive_authorized_lineage:
        query = query.where(~_inactive_authorized_descendant_expression())
    count = int(db.scalar(query) or 0)
    return QueueIsolationStatus(
        isolated=count == 0,
        unrelated_runnable_jobs=count,
        checked_at=observed_at,
    )


def certification_claimable_job_ids(
    db: Session,
    *,
    certification_session_id: str,
    now: datetime | None = None,
) -> tuple[int, ...]:
    """Read the exact ready set seen by the certification claim predicate."""
    observed_at = now or datetime.now(UTC)
    return tuple(
        int(value)
        for value in db.scalars(
            select(BackgroundJob.id)
            .where(BackgroundJob.status.in_(("QUEUED", "RECOVERING")))
            .where(BackgroundJob.run_after <= observed_at)
            .where(
                certification_claim_filter(
                    session_id=require_certification_session_id(session_id=certification_session_id)
                )
            )
            .order_by(BackgroundJob.priority, BackgroundJob.created_at, BackgroundJob.id)
        ).all()
    )


def apply_certification_claim_scope(
    query: Select[Any], *, certification_session_id: str
) -> Select[Any]:
    return query.where(certification_claim_filter(session_id=certification_session_id))


def certification_recovery_filter(*, session_id: str) -> Any:
    """Match exact-session jobs whose explicit root lineage remains recoverable.

    Fencing deliberately moves a root out of the live claim states before its
    process is terminated. Recovery therefore admits STALLED roots, but never a
    terminal root, and still requires explicit authorization, exact session,
    and a matching FULL_PIPELINE root correlation.
    """
    required_session = require_certification_session_id(session_id=session_id)
    recoverable_roots = select(BackgroundJob.root_correlation_id).where(
        BackgroundJob.job_type.in_(CERTIFICATION_ROOT_JOB_TYPES),
        _payload_authorized_expression(),
        _payload_session_expression(required_session),
        BackgroundJob.root_correlation_id.is_not(None),
        BackgroundJob.status.in_(CERTIFICATION_RECOVERABLE_ROOT_STATUSES),
    )
    return func.coalesce(
        and_(
            _payload_authorized_expression(),
            _payload_session_expression(required_session),
            BackgroundJob.root_correlation_id.in_(recoverable_roots),
        ),
        False,
    )


def apply_certification_recovery_scope(
    query: Select[Any], *, certification_session_id: str
) -> Select[Any]:
    return query.where(certification_recovery_filter(session_id=certification_session_id))


def effective_runtime_configuration(settings: Settings) -> dict[str, Any]:
    certification = is_certification_mode(settings)
    return {
        "runtime_mode": settings.runtime_mode.value,
        "process_role": settings.process_role.value,
        "durable_worker_process_enabled": settings.durable_worker_process_enabled,
        "embedded_job_worker_enabled": settings.embedded_job_worker_enabled,
        "certification_isolation_active": certification,
        "effective_disabled_automatic_workflows": (
            list(CERTIFICATION_DISABLED_AUTOMATIC_WORKFLOWS) if certification else []
        ),
        "allowed_control_activity": (
            list(CERTIFICATION_ALLOWED_CONTROL_ACTIVITY) if certification else []
        ),
        "authorized_root_job_type": (CERTIFICATION_ROOT_JOB_TYPE if certification else None),
        "authorized_root_job_types": (list(CERTIFICATION_ROOT_JOB_TYPES) if certification else []),
        "certification_session_id": (
            getattr(settings, "runtime_instance_id", None) if certification else None
        ),
    }


def _is_authorized_root(job_type: str, payload: dict[str, Any], *, session_id: str) -> bool:
    common = (
        payload.get(CERTIFICATION_AUTHORIZATION_KEY) is True
        and payload.get(CERTIFICATION_SESSION_KEY) == session_id
    )
    if job_type == CERTIFICATION_ROOT_JOB_TYPE:
        return bool(
            common
            and isinstance(payload.get(CERTIFICATION_PLAN_KEY), int)
            and int(payload[CERTIFICATION_PLAN_KEY]) > 0
        )
    if job_type == CERI_FEATURE_CERTIFICATION_ROOT_JOB_TYPE:
        return bool(common and _valid_ceri_feature_certification_contract(payload))
    return False


def _valid_ceri_feature_certification_contract(payload: dict[str, Any]) -> bool:
    tickers = payload.get("tickers")
    provider_datasets = payload.get("provider_datasets")
    prohibited_permissions = (
        "allow_score_capture",
        "allow_change_detection",
        "allow_alerts",
        "allow_setup_publication",
        "allow_winner_publication",
        "allow_lifecycle_publication",
        "allow_downstream_continuation",
    )
    required_text = (
        "request_key",
        "configuration_identity",
        "cutoff_at",
        "as_of_session",
        "calendar_version",
        "scope_id",
        "refresh_cycle_id",
        "acquisition_plan_id",
    )
    return (
        payload.get("certification_contract_version") == CERI_FEATURE_CERTIFICATION_CONTRACT_VERSION
        and payload.get("stop_boundary") == CERI_FEATURE_CERTIFICATION_STOP_BOUNDARY
        and isinstance(tickers, list)
        and len(tickers) == CERI_FEATURE_CERTIFICATION_MAX_TICKERS
        and len(set(tickers)) == len(tickers)
        and tickers == sorted(tickers)
        and all(
            isinstance(ticker, str)
            and ticker == ticker.upper()
            and ticker.replace(".", "").replace("-", "").isalnum()
            for ticker in tickers
        )
        and provider_datasets
        == {
            provider: list(datasets)
            for provider, datasets in CERI_FEATURE_CERTIFICATION_ALLOWED_PROVIDER_DATASETS.items()
        }
        and payload.get("checkpoint_interval") == 1
        and payload.get("feature_batch_count") == 1
        and all(
            isinstance(payload.get(key), int) and int(payload[key]) > 0
            for key in ("run_id", "pipeline_run_id", "calculation_context_id")
        )
        and all(isinstance(payload.get(key), str) and payload[key] for key in required_text)
        and str(payload.get("workflow_key") or "").startswith("ceri:feature-certification:")
        and all(payload.get(permission) is False for permission in prohibited_permissions)
    )


def _authorized_parent_exists(
    db: Session,
    *,
    parent_job_id: int,
    root_correlation_id: str,
    session_id: str,
) -> bool:
    root = aliased(BackgroundJob)
    live_root = exists(
        select(root.id).where(
            root.job_type.in_(CERTIFICATION_ROOT_JOB_TYPES),
            root.root_correlation_id == BackgroundJob.root_correlation_id,
            _payload_authorized_expression(root),
            _payload_session_expression(session_id, root),
            _certification_root_has_live_authority(root),
        )
    )
    return bool(
        db.scalar(
            select(BackgroundJob.id)
            .where(
                BackgroundJob.id == parent_job_id,
                BackgroundJob.root_correlation_id == root_correlation_id,
                _payload_authorized_expression(),
                _payload_session_expression(session_id),
                or_(
                    BackgroundJob.status.in_(CERTIFICATION_ACTIVE_ROOT_STATUSES),
                    and_(BackgroundJob.status == "COMPLETED", live_root),
                ),
            )
            .limit(1)
        )
    )


def _payload_authorized_expression(job: Any = BackgroundJob) -> Any:
    return job.payload_json[CERTIFICATION_AUTHORIZATION_KEY].as_boolean().is_(True)


def _payload_session_expression(session_id: str, job: Any = BackgroundJob) -> Any:
    return job.payload_json[CERTIFICATION_SESSION_KEY].as_string() == session_id


def _inactive_authorized_descendant_expression() -> Any:
    inactive_roots = select(BackgroundJob.root_correlation_id).where(
        BackgroundJob.job_type.in_(CERTIFICATION_ROOT_JOB_TYPES),
        _payload_authorized_expression(),
        BackgroundJob.root_correlation_id.is_not(None),
        ~BackgroundJob.status.in_(CERTIFICATION_LINEAGE_ROOT_STATUSES),
    )
    return func.coalesce(
        and_(
            ~BackgroundJob.job_type.in_(CERTIFICATION_ROOT_JOB_TYPES),
            BackgroundJob.root_correlation_id.in_(inactive_roots),
        ),
        False,
    )
