from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.models.tables import BackgroundJob
from app.services.background_job_service import JobLeaseLost, JobStatus


@dataclass(frozen=True)
class DomainWriteOwnership:
    """The immutable job-attempt identity authorized to commit domain state."""

    job_id: int
    execution_token: str


_current_ownership: ContextVar[DomainWriteOwnership | None] = ContextVar(
    "swinglens_domain_write_ownership",
    default=None,
)


def assert_current_execution_ownership(
    db: Session,
    *,
    job_id: int,
    execution_token: str,
) -> None:
    """Lock and validate the job attempt in the transaction about to commit.

    The row lock is retained by ``db`` through its commit or rollback. Reclaim
    must update the same job row, so ownership validation and the domain commit
    have one database serialization point instead of a check-then-commit gap.
    """

    with db.no_autoflush:
        current = db.execute(
            select(BackgroundJob.status, BackgroundJob.execution_token)
            .where(BackgroundJob.id == job_id)
            .with_for_update()
        ).one_or_none()
    if (
        current is None
        or current.status != JobStatus.RUNNING
        or current.execution_token != execution_token
    ):
        raise JobLeaseLost(f"Background job {job_id} lease is no longer held.")


@contextmanager
def fence_domain_commits(
    *,
    job_id: int | None,
    execution_token: str | None,
) -> Iterator[None]:
    """Fence every ORM commit made synchronously by one durable job attempt."""

    if job_id is None or not execution_token:
        yield
        return
    reset_token: Token[DomainWriteOwnership | None] = _current_ownership.set(
        DomainWriteOwnership(job_id=job_id, execution_token=execution_token)
    )
    try:
        yield
    finally:
        _current_ownership.reset(reset_token)


@event.listens_for(Session, "before_commit")
def _fence_active_domain_commit(db: Session) -> None:
    ownership = _current_ownership.get()
    if ownership is None:
        return
    assert_current_execution_ownership(
        db,
        job_id=ownership.job_id,
        execution_token=ownership.execution_token,
    )
