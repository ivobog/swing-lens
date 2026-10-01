from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from copy import deepcopy
from dataclasses import dataclass

from sqlalchemy import event, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
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
from app.settings import get_settings

logger = logging.getLogger(__name__)


class ControlPlaneLockTimeout(RuntimeError):
    """A bounded control-plane write could not acquire its PostgreSQL lock."""


@dataclass(frozen=True)
class DomainWriteOwnership:
    """The immutable job-attempt identity authorized to commit domain state."""

    job_id: int
    execution_token: str


_current_ownership: ContextVar[DomainWriteOwnership | None] = ContextVar(
    "swinglens_domain_write_ownership",
    default=None,
)
_domain_session: ContextVar[Session | None] = ContextVar(
    "swinglens_domain_write_session", default=None
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
    """Return the only Session explicitly authorized for this domain attempt."""
    db = _domain_session.get()
    ownership = _current_ownership.get()
    if db is None or ownership != DomainWriteOwnership(job_id, execution_token):
        return None
    if not db.in_transaction():
        return None
    return db


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
    db: Session,
    *,
    job_id: int | None,
    execution_token: str | None,
) -> Iterator[None]:
    """Fence every ORM commit made synchronously by one durable job attempt."""

    if job_id is None or not execution_token:
        yield
        return
    ownership_token: Token[DomainWriteOwnership | None] = _current_ownership.set(
        DomainWriteOwnership(job_id=job_id, execution_token=execution_token)
    )
    session_token: Token[Session | None] = _domain_session.set(db)
    try:
        yield
    finally:
        _domain_session.reset(session_token)
        _current_ownership.reset(ownership_token)


@contextmanager
def bounded_domain_session(
    db: Session,
    *,
    job_id: int | None = None,
    execution_token: str | None = None,
) -> Iterator[Session]:
    """Create one short domain Session explicitly bound to the current attempt.

    Services that checkpoint per item must not create a Session that merely
    inherits the ambient ownership ContextVar. This boundary copies the
    immutable attempt identity deliberately and gives the child Session its
    own commit-time fence.
    """

    if not isinstance(db, Session):
        yield db
        return
    ownership = current_domain_write_ownership()
    effective_job_id = job_id if job_id is not None else getattr(ownership, "job_id", None)
    effective_token = (
        execution_token
        if execution_token is not None
        else getattr(ownership, "execution_token", None)
    )
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    with factory() as child_db:
        with fence_domain_commits(
            child_db,
            job_id=effective_job_id,
            execution_token=effective_token,
        ):
            yield child_db


@contextmanager
def detached_control_plane_scope() -> Iterator[None]:
    """Keep operational progress commits outside the domain-write fence.

    A detached heartbeat/progress Session runs synchronously inside the durable
    handler's context, so ContextVars would otherwise make its own commit hook
    acquire the calculation job's domain ``FOR UPDATE`` fence.  That control
    Session is restricted by its caller to lease/progress/cancellation fields;
    the calculation Session remains fenced independently at its domain commit.
    """

    ownership_token = _current_ownership.set(None)
    session_token = _domain_session.set(None)
    try:
        yield
    finally:
        _domain_session.reset(session_token)
        _current_ownership.reset(ownership_token)


@contextmanager
def control_plane_transaction(
    db: Session, *, lock_timeout_seconds: float | None = None
) -> Iterator[Session]:
    """Run and commit one short control transaction with no domain authority.

    This is the sole boundary for synchronous lease, heartbeat, progress, and
    cancellation commits made from inside a domain execution context.
    """

    timeout = float(
        lock_timeout_seconds
        if lock_timeout_seconds is not None
        else get_settings().job_control_lock_timeout_seconds
    )
    with detached_control_plane_scope():
        try:
            bind = db.get_bind()
            if bind is not None and bind.dialect.name == "postgresql":
                # set_config(..., true) is transaction-scoped and supports a
                # bound value. It cannot leak into the pooled connection.
                db.execute(
                    text("SELECT set_config('lock_timeout', :timeout, true)"),
                    {"timeout": f"{max(1, int(timeout * 1000))}ms"},
                )
            yield db
            db.commit()
        except OperationalError as exc:
            db.rollback()
            sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
            if sqlstate == "55P03":
                logger.error(
                    "job.control_plane.lock_timeout",
                    extra={
                        "reason_code": "CONTROL_PLANE_LOCK_TIMEOUT",
                        "lock_timeout_seconds": timeout,
                    },
                )
                raise ControlPlaneLockTimeout(
                    f"Control-plane transaction exceeded {timeout:g}s lock timeout."
                ) from exc
            raise
        except Exception:
            db.rollback()
            raise


@event.listens_for(Session, "before_commit")
def _fence_active_domain_commit(db: Session) -> None:
    ownership = _current_ownership.get()
    domain_session = _domain_session.get()
    if ownership is None:
        return
    if domain_session is not db:
        raise JobLeaseLost(
            "Domain transaction Session was not explicitly bound to the current job attempt."
        )
    if db.in_nested_transaction():
        # ``before_commit`` also fires when SQLAlchemy releases a nested
        # SAVEPOINT.  A savepoint is not a durable publication boundary, and
        # taking the background-job row lock there retains it in the outer
        # transaction.  Long-running handlers then deadlock their detached
        # heartbeat/progress Session against themselves.  The outer commit
        # fires this hook again after the savepoint has ended and remains the
        # mandatory serialization point for publishing domain state. A
        # different Session can never inherit this lock implicitly.
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
