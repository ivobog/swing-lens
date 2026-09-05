from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import EntryModel, OutcomeStatus, WinnerForwardOutcome
from app.services.us_market_calendar import us_trading_sessions_between
from app.services.winner_probability.outcome_service import (
    OutcomeMaturationCancelled,
    OutcomeMaturationService,
    WinnerOutcomeRepository,
)
from app.services.winner_probability.trading_session_service import latest_completed_session


@dataclass(frozen=True)
class H5DrainResult:
    due_h5_next_open: int
    oldest_due_h5_session: date | None
    oldest_due_h5_age: int | None
    processed_h5: int
    matured_h5: int
    pending_h5_after_cycle: int
    excluded_h5: int
    failed_h5: int
    target_stop_matured: int
    unvisited_h5_after_cycle: int
    last_successful_full_drain_at: str | None
    deferred_pending_h5: int = 0
    scan_completed: bool = False
    last_full_scan_at: str | None = None
    last_zero_due_backlog_at: str | None = None
    material_evidence_changes: int = 0
    reason_counts: dict[str, int] | None = None
    due_total: int = 0
    retry_eligible_now: int = 0
    retry_deferred: int = 0
    unvisited_total: int = 0
    eligible_remaining: int = 0
    earliest_retry_not_before: str | None = None

    def as_dict(self) -> dict[str, object]:
        payload = self.__dict__.copy()
        if self.oldest_due_h5_session is not None:
            payload["oldest_due_h5_session"] = self.oldest_due_h5_session.isoformat()
        return payload


@dataclass(frozen=True)
class H5QueueState:
    due_total: int
    retry_eligible_now: int
    retry_deferred: int
    oldest_due_session: date | None
    earliest_retry_not_before: datetime | None


class H5NextOpenOrchestrationService:
    """Bounded, deterministic drain for the primary H5 NEXT_OPEN queue."""

    def __init__(
        self,
        *,
        repository: WinnerOutcomeRepository | None = None,
        maturation_service: OutcomeMaturationService | None = None,
    ) -> None:
        self.repository = repository or WinnerOutcomeRepository()
        self.maturation_service = maturation_service or OutcomeMaturationService(
            repository=self.repository
        )

    def drain_due(
        self,
        db: Session,
        *,
        now: datetime | None = None,
        batch_size: int = 500,
        max_batches: int = 10,
        due_session: date | None = None,
        should_cancel: Callable[[], bool] | None = None,
        lease_guard: Callable[[], None] | None = None,
    ) -> H5DrainResult:
        now = now or datetime.now(UTC)
        completed_on = min(
            latest_completed_session(now), due_session or latest_completed_session(now)
        )
        before = self._queue_state(db, completed_on=completed_on, retry_as_of=now)
        processed_ids: list[int] = []
        processed = matured = excluded = failed = target_stop_matured = deferred = 0
        material_changes = 0
        reason_counts: dict[str, int] = {}

        for _ in range(max_batches):
            rows = self.repository.get_due_pending_forward_outcomes(
                db,
                completed_on=completed_on,
                limit=batch_size,
                entry_model=EntryModel.NEXT_OPEN,
                horizon_sessions=5,
                due_session=completed_on,
                exclude_ids=tuple(processed_ids),
                retry_as_of=now,
            )
            if not rows:
                break
            build_context = getattr(self.maturation_service, "build_batch_context", None)
            context = build_context(db, rows) if callable(build_context) else None
            for row in rows:
                if should_cancel is not None and should_cancel():
                    raise OutcomeMaturationCancelled("winner H5 NEXT_OPEN drain was cancelled")
                processed_ids.append(row.id)
                if context is None:
                    result = self.maturation_service.process_forward_outcome(db, row, now=now)
                else:
                    result = self.maturation_service.process_forward_outcome(
                        db, row, now=now, context=context
                    )
                processed += result.processed
                matured += result.matured
                deferred += result.pending
                excluded += result.excluded
                failed += result.failed
                target_stop_matured += result.target_stop_matured
                material_changes += len(result.material_changes)
                for reason, count in result.reason_counts.items():
                    reason_counts[reason] = reason_counts.get(reason, 0) + count
            db.flush()
            if lease_guard is not None:
                lease_guard()

        after = self._queue_state(db, completed_on=completed_on, retry_as_of=now)
        unvisited = self._queue_state(
            db,
            completed_on=completed_on,
            retry_as_of=now,
            exclude_ids=tuple(processed_ids),
        )
        scan_completed = unvisited.due_total == 0
        full_scan_at = now.isoformat() if scan_completed else None
        zero_due_at = now.isoformat() if after.due_total == 0 else None
        return H5DrainResult(
            due_h5_next_open=before.due_total,
            oldest_due_h5_session=before.oldest_due_session,
            oldest_due_h5_age=(
                us_trading_sessions_between(before.oldest_due_session, completed_on)
                if before.oldest_due_session is not None
                else None
            ),
            processed_h5=processed,
            matured_h5=matured,
            pending_h5_after_cycle=after.due_total,
            excluded_h5=excluded,
            failed_h5=failed,
            target_stop_matured=target_stop_matured,
            unvisited_h5_after_cycle=unvisited.due_total,
            last_successful_full_drain_at=zero_due_at,
            deferred_pending_h5=deferred,
            scan_completed=scan_completed,
            last_full_scan_at=full_scan_at,
            last_zero_due_backlog_at=zero_due_at,
            material_evidence_changes=material_changes,
            reason_counts=reason_counts,
            due_total=before.due_total,
            retry_eligible_now=before.retry_eligible_now,
            retry_deferred=after.retry_deferred,
            unvisited_total=unvisited.due_total,
            eligible_remaining=unvisited.retry_eligible_now,
            earliest_retry_not_before=(
                after.earliest_retry_not_before.isoformat()
                if after.earliest_retry_not_before is not None
                else None
            ),
        )

    def queue_state(
        self,
        db: Session,
        *,
        now: datetime | None = None,
        due_session: date | None = None,
    ) -> H5QueueState:
        now = now or datetime.now(UTC)
        completed_on = min(
            latest_completed_session(now), due_session or latest_completed_session(now)
        )
        return self._queue_state(db, completed_on=completed_on, retry_as_of=now)

    def _queue_state(
        self,
        db: Session,
        *,
        completed_on: date,
        retry_as_of: datetime,
        exclude_ids: tuple[int, ...] = (),
    ) -> H5QueueState:
        custom = getattr(self.repository, "h5_queue_state", None)
        if callable(custom):
            value = custom(
                db,
                completed_on=completed_on,
                retry_as_of=retry_as_of,
                exclude_ids=exclude_ids,
            )
            if isinstance(value, H5QueueState):
                return value
            return H5QueueState(**value)

        eligible = (WinnerForwardOutcome.retry_not_before_at.is_(None)) | (
            WinnerForwardOutcome.retry_not_before_at <= retry_as_of
        )
        deferred = WinnerForwardOutcome.retry_not_before_at > retry_as_of
        statement = (
            select(
                func.count(WinnerForwardOutcome.id),
                func.count(WinnerForwardOutcome.id).filter(eligible),
                func.count(WinnerForwardOutcome.id).filter(deferred),
                func.min(WinnerForwardOutcome.due_session),
                func.min(WinnerForwardOutcome.retry_not_before_at).filter(deferred),
            )
            .where(WinnerForwardOutcome.status == OutcomeStatus.PENDING)
            .where(WinnerForwardOutcome.is_current_revision.is_(True))
            .where(WinnerForwardOutcome.entry_model == EntryModel.NEXT_OPEN)
            .where(WinnerForwardOutcome.horizon_sessions == 5)
            .where(WinnerForwardOutcome.due_session <= completed_on)
        )
        if exclude_ids:
            statement = statement.where(WinnerForwardOutcome.id.not_in(exclude_ids))
        row = db.execute(statement).one()
        return H5QueueState(
            due_total=int(row[0] or 0),
            retry_eligible_now=int(row[1] or 0),
            retry_deferred=int(row[2] or 0),
            oldest_due_session=row[3],
            earliest_retry_not_before=row[4],
        )
