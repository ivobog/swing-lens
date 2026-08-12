from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, not_, or_
from sqlalchemy.sql.elements import ColumnElement

from app.models.tables import BackgroundJob

INTERACTIVE = "interactive"
BROKER = "broker"
BACKGROUND = "background"
VALID_WORKER_QUEUES = (INTERACTIVE, BROKER, BACKGROUND)

INTERACTIVE_JOB_TYPES = frozenset({"FULL_PIPELINE"})
BROKER_JOB_TYPES = frozenset({"MARKET_DATA_PREWARM"})


@dataclass(frozen=True)
class QueueClaimGroup:
    queues: tuple[str, ...]
    created_before: datetime | None = None


@dataclass
class WorkerClaimState:
    consecutive_interactive_claims: int = 0

    def record(self, job_type: str) -> None:
        if job_queue_class(job_type) == INTERACTIVE:
            self.consecutive_interactive_claims += 1
        else:
            self.consecutive_interactive_claims = 0


def normalize_worker_queues(queues: str | Iterable[str] | None) -> tuple[str, ...]:
    if queues is None:
        return VALID_WORKER_QUEUES
    values = tuple(queues.split(",") if isinstance(queues, str) else queues)
    requested = {str(value).strip().lower() for value in values if str(value).strip()}
    normalized = tuple(
        queue
        for queue in VALID_WORKER_QUEUES
        if queue in requested
    )
    unknown = requested - set(VALID_WORKER_QUEUES)
    if unknown:
        raise ValueError(f"unknown worker queues: {', '.join(sorted(unknown))}")
    if not normalized:
        raise ValueError("at least one worker queue is required")
    return normalized


def job_queue_class(job_type: str) -> str:
    if job_type in INTERACTIVE_JOB_TYPES:
        return INTERACTIVE
    if job_type in BROKER_JOB_TYPES or job_type.startswith("IB_"):
        return BROKER
    return BACKGROUND


def build_worker_claim_groups(
    queues: Iterable[str],
    *,
    fairness_enabled: bool,
    claim_state: WorkerClaimState,
    max_consecutive_interactive: int,
    age_promotion_seconds: int,
    now: datetime | None = None,
) -> tuple[QueueClaimGroup, ...]:
    allowed = normalize_worker_queues(queues)
    if not fairness_enabled:
        return (QueueClaimGroup(allowed),)

    interactive = (INTERACTIVE,) if INTERACTIVE in allowed else ()
    noninteractive = tuple(
        queue for queue in (BROKER, BACKGROUND) if queue in allowed
    )
    force_noninteractive = bool(
        noninteractive
        and claim_state.consecutive_interactive_claims
        >= max(1, int(max_consecutive_interactive))
    )
    groups: list[QueueClaimGroup] = []
    if interactive and not force_noninteractive:
        groups.append(QueueClaimGroup(interactive))
    if noninteractive and age_promotion_seconds > 0:
        observed_at = now or datetime.now(UTC)
        groups.append(
            QueueClaimGroup(
                noninteractive,
                created_before=observed_at
                - timedelta(seconds=int(age_promotion_seconds)),
            )
        )
    groups.extend(QueueClaimGroup((queue,)) for queue in noninteractive)
    if interactive and force_noninteractive:
        groups.append(QueueClaimGroup(interactive))
    return tuple(groups)


def worker_queue_filter(queues: Iterable[str]) -> ColumnElement[bool] | None:
    normalized = normalize_worker_queues(queues)
    if set(normalized) == set(VALID_WORKER_QUEUES):
        return None

    interactive = BackgroundJob.job_type.in_(INTERACTIVE_JOB_TYPES)
    broker = or_(
        BackgroundJob.job_type.in_(BROKER_JOB_TYPES),
        BackgroundJob.job_type.like(r"IB\_%", escape="\\"),
    )
    clauses: list[ColumnElement[bool]] = []
    if INTERACTIVE in normalized:
        clauses.append(interactive)
    if BROKER in normalized:
        clauses.append(broker)
    if BACKGROUND in normalized:
        clauses.append(and_(not_(interactive), not_(broker)))
    return or_(*clauses)
