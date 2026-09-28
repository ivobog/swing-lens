from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from copy import deepcopy
from dataclasses import dataclass

from sqlalchemy import event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.sql.dml import Delete, Insert, Update
from sqlalchemy.sql.elements import (
    ReleaseSavepointClause,
    RollbackToSavepointClause,
    SavepointClause,
    TextClause,
)
from sqlalchemy.sql.selectable import Select

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
_fenced_session: ContextVar[tuple[Session, DomainWriteOwnership] | None] = ContextVar(
    "swinglens_fenced_domain_session", default=None
)
_retained_batch_ownership: ContextVar[tuple | None] = ContextVar(
    "swinglens_retained_batch_ownership", default=None
)
_defer_execution_lock: ContextVar[bool] = ContextVar(
    "swinglens_defer_execution_ownership_lock", default=False
)
_force_execution_lock: ContextVar[bool] = ContextVar(
    "swinglens_force_execution_ownership_lock", default=False
)


@contextmanager
def retained_execution_ownership_scope(db):
    """Reuse an actual outer-transaction row lock during one source batch.

    The scope acquires the lock before company savepoints. Their rollback cannot
    release this outer lock. No witness is reused across transactions, Sessions,
    attempts, or an ownership-changing SQL mutation.
    """
    ownership = current_domain_write_ownership()
    if (
        ownership is None
        or db.in_nested_transaction()
        or _retained_batch_ownership.get() is not None
    ):
        # Nested scopes share the outer witness. Resetting a nested token could
        # otherwise restore a witness invalidated by an ownership-changing SQL
        # statement within that nested scope.
        yield
        return
    job_scope = assert_current_execution_ownership(
        db, job_id=ownership.job_id, execution_token=ownership.execution_token
    )
    connection = db.connection(bind_arguments={"mapper": BackgroundJob})
    token = _retained_batch_ownership.set(
        (db, db.get_transaction(), ownership, connection, connection.get_transaction(), job_scope)
    )
    try:
        yield
    finally:
        _retained_batch_ownership.reset(token)


@event.listens_for(Engine, "after_cursor_execute")
def _invalidate_retained_batch_ownership(conn, cursor, statement, parameters, context, executemany):
    retained = _retained_batch_ownership.get()
    if retained is None:
        return
    compiled = getattr(context, "compiled", None)
    expression = getattr(compiled, "statement", None)
    if isinstance(expression, (SavepointClause, RollbackToSavepointClause, ReleaseSavepointClause)):
        return
    if isinstance(expression, TextClause) and expression.text in {
        "SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))",
        "SELECT pg_advisory_xact_lock(:scope)",
    }:
        return
    if isinstance(expression, (Update, Insert, Delete)):
        target_names = {
            name
            for name in (
                getattr(expression.table, "name", None),
                getattr(getattr(expression.table, "original", None), "name", None),
            )
            if name is not None
        }
        if "background_jobs" not in target_names:
            # Structured SQLAlchemy DML exposes its exact target even when the
            # ORM wraps it in an AnnotatedTable.  Only a write to the leased job
            # row can invalidate this transaction-scoped ownership witness.
            return
        # Progress/heartbeat writes cannot change the two locked ownership
        # columns or the retained operation binding. Unknown writes invalidate.
        values = getattr(expression, "_values", None)
        ordered = getattr(expression, "_ordered_values", None)
        columns = (
            [getattr(key, "key", key) for key in values]
            if values
            else [getattr(key, "key", key) for key, _ in ordered]
            if ordered
            else getattr(compiled, "column_keys", None)
        )
        if (
            isinstance(expression, Update)
            and not getattr(expression, "_independent_ctes", ())
            and statement.lstrip().upper().startswith("UPDATE ")
            and not getattr(compiled.compile_state, "is_multitable", False)
            and columns
            and not set(columns)
            & {"status", "execution_token", "job_type", "related_run_id", "payload_json"}
        ):
            return
        _retained_batch_ownership.set(None)
    elif not isinstance(expression, Select) or (
        "background_jobs" in statement.lower()
        and any(word in statement.upper() for word in ("UPDATE ", "INSERT ", "DELETE "))
    ):
        _retained_batch_ownership.set(None)


def current_fenced_domain_session(job_id: int, execution_token: str) -> Session | None:
    """Find this attempt's synchronous transaction retaining the job row lock."""
    retained = _fenced_session.get()
    if retained is None:
        return None
    db, ownership = retained
    if ownership != DomainWriteOwnership(job_id, execution_token) or not db.in_transaction():
        return None
    return db


@event.listens_for(Session, "after_transaction_end")
def _release_fenced_session(db: Session, transaction) -> None:
    retained = _fenced_session.get()
    if transaction.parent is None and retained is not None and retained[0] is db:
        _fenced_session.set(None)


def current_domain_write_ownership() -> DomainWriteOwnership | None:
    """Return the supplied durable attempt, without resolving or inventing a job."""
    return _current_ownership.get()


def assert_current_execution_ownership(
    db: Session,
    *,
    job_id: int,
    execution_token: str,
    lock_row: bool | None = None,
) -> dict:
    """Validate the job attempt and normally retain its row lock.

    The row lock is retained by ``db`` through its commit or rollback. Reclaim
    must update the same job row, so ownership validation and the domain commit
    have one database serialization point instead of a check-then-commit gap.
    A long-work deferred scope performs only the token check; ``before_commit``
    always forces the locking validation before domain state can publish.
    """

    ownership = DomainWriteOwnership(job_id, execution_token)
    retained = _retained_batch_ownership.get()
    if retained is not None:
        (
            retained_db,
            transaction,
            retained_ownership,
            connection,
            sql_transaction,
            job_scope,
        ) = retained
        if (
            retained_db is db
            and retained_ownership == ownership
            and transaction is db.get_transaction()
            and transaction is not None
            and transaction.is_active
            and not connection.closed
            and not connection.invalidated
            and connection.get_transaction() is sql_transaction
            and sql_transaction is not None
            and sql_transaction.is_active
        ):
            return deepcopy(job_scope)
    statement = select(
        BackgroundJob.status,
        BackgroundJob.execution_token,
        BackgroundJob.job_type,
        BackgroundJob.related_run_id,
        BackgroundJob.payload_json,
    ).where(BackgroundJob.id == job_id)
    should_lock = (
        lock_row
        if lock_row is not None
        else not _defer_execution_lock.get() or _force_execution_lock.get()
    )
    if should_lock:
        statement = statement.with_for_update()
    with db.no_autoflush:
        current = db.execute(statement).one_or_none()
    if (
        current is None
        or current.status != JobStatus.RUNNING
        or current.execution_token != execution_token
    ):
        raise JobLeaseLost(f"Background job {job_id} lease is no longer held.")
    _fenced_session.set((db, DomainWriteOwnership(job_id, execution_token)))
    return deepcopy(dict(current._mapping))


def retained_execution_job_scope(db, *, job_id, execution_token):
    """Return the exact locked scope only while its batch witness is valid."""
    if _retained_batch_ownership.get() is None:
        return None
    return assert_current_execution_ownership(db, job_id=job_id, execution_token=execution_token)


@contextmanager
def deferred_execution_ownership_lock() -> Iterator[None]:
    """Check ownership during long work, reserving the row lock for commit."""
    token = _defer_execution_lock.set(True)
    try:
        yield
    finally:
        _defer_execution_lock.reset(token)


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


@contextmanager
def detached_control_plane_scope() -> Iterator[None]:
    """Keep operational progress commits outside the domain-write fence.

    A detached heartbeat/progress Session runs synchronously inside the durable
    handler's context, so ContextVars would otherwise make its own commit hook
    acquire the calculation job's domain ``FOR UPDATE`` fence.  That control
    Session is restricted by its caller to lease/progress/cancellation fields;
    the calculation Session remains fenced independently at its domain commit.
    """

    reset_token = _current_ownership.set(None)
    try:
        yield
    finally:
        _current_ownership.reset(reset_token)


@event.listens_for(Session, "before_commit")
def _fence_active_domain_commit(db: Session) -> None:
    ownership = _current_ownership.get()
    if ownership is None:
        return
    token = _force_execution_lock.set(True)
    try:
        assert_current_execution_ownership(
            db,
            job_id=ownership.job_id,
            execution_token=ownership.execution_token,
        )
    finally:
        _force_execution_lock.reset(token)
