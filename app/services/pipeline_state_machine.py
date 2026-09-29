from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from app.models.tables import PipelineDependency, PipelineRun, PipelineStep
from app.observability.transaction_metrics import publish_after_commit


class PipelineTransitionError(RuntimeError):
    """A caller attempted a transition outside its pipeline authority."""


QUEUED_PIPELINE_STATES = frozenset({"PENDING", "QUEUED"})
WAITING_PIPELINE_STATES = frozenset(
    {"WAITING_DEPENDENCY", "WAITING_FOR_CERI_COMPLETION", "PREPARING"}
)
CANCELLING_PIPELINE_STATES = frozenset({"CANCEL_REQUESTED"})
TERMINAL_PIPELINE_STATES = frozenset(
    {"COMPLETED", "PARTIAL", "FAILED", "BLOCKED", "CANCELLED", "FEATURE_CERTIFIED"}
)
ACTIVE_PIPELINE_STATES = frozenset(
    {
        "RUNNING",
        "WAITING_FOR_MARKET_DATA",
        "SCORING_FUNDAMENTALS",
        "FETCHING_MARKET_DATA",
        "SCORING_TECHNICALS",
        "MARKET_REGIME_SNAPSHOT",
        "COMBINING_RESULTS",
        "RANKING_PROFILES",
        "SECTOR_ROTATION_SNAPSHOT",
        "FREEZING_DECISION_HANDOFF_MANIFEST",
        "CERI_PROVIDER_INGEST",
        "CERI_FEATURE_CERTIFYING",
        "CERI_CAPTURE_SNAPSHOT",
        "CAPTURING_SETUP_SIGNALS",
        "EVALUATING_SETUP_LIFECYCLES",
        "CAPTURING_WINNER_PREDICTIONS",
    }
)


def transition_pipeline(
    db: Session,
    pipeline: PipelineRun,
    target: str,
    *,
    actor: str,
    current_step: str | None = None,
    message: str | None = None,
    error_message: str | None = None,
    completed_at: datetime | None = None,
    operator_resume: bool = False,
    clear_current_step: bool = False,
) -> PipelineRun:
    """Apply one validated PipelineRun transition through the authoritative path."""

    source = str(pipeline.status)
    if target == "COMPLETED" and _has_unresolved_dependency(db, pipeline.id):
        raise PipelineTransitionError(
            f"PIPELINE_COMPLETION_BLOCKED_BY_DEPENDENCY:pipeline={pipeline.id}"
        )
    if not _transition_allowed(source, target, actor=actor, operator_resume=operator_resume):
        raise PipelineTransitionError(
            f"PIPELINE_TRANSITION_FORBIDDEN:{source}->{target}:actor={actor}"
        )
    pipeline.status = target
    if clear_current_step:
        pipeline.current_step = None
    elif current_step is not None:
        pipeline.current_step = current_step
    if message is not None:
        pipeline.message = message
    pipeline.error_message = error_message
    if target in TERMINAL_PIPELINE_STATES:
        pipeline.completed_at = completed_at or datetime.now(UTC)
    elif target not in TERMINAL_PIPELINE_STATES:
        pipeline.completed_at = None
    db.flush()
    publish_after_commit(
        db,
        "increment",
        "swinglens_pipeline_transitions_total",
        source=source,
        target=target,
        actor=actor,
    )
    return pipeline


def transition_pipeline_step(
    db: Session,
    step: PipelineStep,
    target: str,
    *,
    message: str | None = None,
    error_message: str | None = None,
    completed_at: datetime | None = None,
) -> PipelineStep:
    terminal = {"COMPLETED", "FAILED", "BLOCKED", "CANCELLED", "INTERRUPTED", "SKIPPED"}
    if str(step.status) in terminal and target in {"PENDING", "RUNNING"}:
        # Explicit continuation replay is the only caller allowed to reset a
        # terminal step and must clear its terminal timestamps first.
        if completed_at is not None:
            raise PipelineTransitionError(
                f"PIPELINE_STEP_TRANSITION_FORBIDDEN:{step.status}->{target}"
            )
    step.status = target
    step.message = message
    step.error_message = error_message
    step.completed_at = completed_at if target in terminal else None
    db.flush()
    return step


def _transition_allowed(
    source: str,
    target: str,
    *,
    actor: str,
    operator_resume: bool,
) -> bool:
    if source == target:
        return True
    if source in TERMINAL_PIPELINE_STATES:
        return bool(
            operator_resume
            and actor == "operator"
            and source in {"FAILED", "BLOCKED", "PARTIAL"}
            and target in QUEUED_PIPELINE_STATES
        )
    if source in CANCELLING_PIPELINE_STATES:
        return target in {"CANCELLED", "FAILED"}
    if source in WAITING_PIPELINE_STATES:
        if actor == "dependency":
            return target in QUEUED_PIPELINE_STATES | {"RUNNING"} | TERMINAL_PIPELINE_STATES
        return target in CANCELLING_PIPELINE_STATES | TERMINAL_PIPELINE_STATES
    if source in QUEUED_PIPELINE_STATES:
        return target in (
            ACTIVE_PIPELINE_STATES
            | WAITING_PIPELINE_STATES
            | CANCELLING_PIPELINE_STATES
            | TERMINAL_PIPELINE_STATES
        )
    if source in ACTIVE_PIPELINE_STATES:
        if actor == "recovery" and target in QUEUED_PIPELINE_STATES:
            return True
        return target in (
            ACTIVE_PIPELINE_STATES
            | WAITING_PIPELINE_STATES
            | CANCELLING_PIPELINE_STATES
            | TERMINAL_PIPELINE_STATES
        )
    return False


def _has_unresolved_dependency(db: Session, pipeline_id: int | None) -> bool:
    if pipeline_id is None or not isinstance(db, Session):
        return False
    bind = db.get_bind()
    if bind.dialect.name == "sqlite" and not inspect(bind).has_table(
        PipelineDependency.__tablename__
    ):
        return False
    return bool(
        db.scalar(
            select(func.count(PipelineDependency.id)).where(
                PipelineDependency.pipeline_run_id == pipeline_id,
                PipelineDependency.state.in_(("PENDING_ENQUEUE", "QUEUED", "RUNNING")),
            )
        )
    )
