from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import event
from sqlalchemy.orm import Session
from sqlalchemy.orm.session import SessionTransaction

from app.observability.metrics import operational_metrics
from app.services.redaction import redact_sensitive

logger = logging.getLogger(__name__)

MetricAction = Literal["increment", "observe", "set_gauge"]
_PENDING_KEY = "swinglens_pending_commit_metrics"


@dataclass(frozen=True)
class PendingMetric:
    action: MetricAction
    name: str
    value: float
    labels: dict[str, Any]


def publish_after_commit(
    session: Any,
    action: MetricAction,
    name: str,
    value: float = 1.0,
    **labels: Any,
) -> None:
    """Publish a commit-dependent metric exactly once after the outer commit.

    Lightweight test doubles do not expose SQLAlchemy transaction state; their
    metrics remain immediate so domain unit tests can use the compatibility API.
    """
    if not isinstance(session, Session):
        _publish(PendingMetric(action, name, float(value), labels))
        return
    transaction = session.get_nested_transaction() or session.get_transaction()
    if transaction is None:
        # Commit-dependent publishers frequently run just before the first ORM
        # add/flush. Start the same transaction SQLAlchemy would auto-begin so
        # an early publication can never escape a later rollback.
        session.begin()
        transaction = session.get_transaction()
        if transaction is None:  # defensive: a conforming Session always has one
            return
    pending = session.info.setdefault(_PENDING_KEY, {})
    pending.setdefault(transaction, []).append(PendingMetric(action, name, float(value), labels))


def pending_metric_count(session: Session) -> int:
    return sum(len(items) for items in session.info.get(_PENDING_KEY, {}).values())


def _after_commit(session: Session) -> None:
    transaction = session.get_nested_transaction() or session.get_transaction()
    if transaction is None:
        return
    pending = session.info.get(_PENDING_KEY, {})
    items = pending.pop(transaction, [])
    parent = transaction.parent
    if transaction.nested and parent is not None:
        pending.setdefault(parent, []).extend(items)
        return
    for item in items:
        _publish(item)
    if not pending:
        session.info.pop(_PENDING_KEY, None)


def _after_rollback(session: Session) -> None:
    transaction = session.get_nested_transaction() or session.get_transaction()
    if transaction is None:
        session.info.pop(_PENDING_KEY, None)
        return
    pending = session.info.get(_PENDING_KEY, {})
    pending.pop(transaction, None)
    if not transaction.nested:
        session.info.pop(_PENDING_KEY, None)


def _after_soft_rollback(session: Session, previous_transaction: SessionTransaction) -> None:
    pending = session.info.get(_PENDING_KEY, {})
    pending.pop(previous_transaction, None)
    if not pending:
        session.info.pop(_PENDING_KEY, None)


def _publish(item: PendingMetric) -> None:
    try:
        callback = getattr(operational_metrics, item.action)
        callback(item.name, item.value, **item.labels)
    except Exception as exc:  # metrics must never invalidate committed business state
        logger.warning(
            "observability.metric_publish_failed",
            extra=redact_sensitive(
                {
                    "metric_name": item.name,
                    "metric_action": item.action,
                    "error_type": type(exc).__name__,
                    "error_summary": str(exc),
                }
            ),
        )


event.listen(Session, "after_commit", _after_commit)
event.listen(Session, "after_rollback", _after_rollback)
event.listen(Session, "after_soft_rollback", _after_soft_rollback)
