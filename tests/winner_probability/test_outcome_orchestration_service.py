from __future__ import annotations

from datetime import UTC, date, datetime

import app.services.winner_probability.outcome_orchestration_service as orchestration_module
from app.models.tables import EntryModel, OutcomeStatus, WinnerForwardOutcome
from app.services.winner_probability.outcome_orchestration_service import (
    H5NextOpenOrchestrationService,
)
from app.services.winner_probability.outcome_service import OutcomeMaturationResult
from app.services.winner_probability.trading_session_service import latest_completed_session


def test_more_than_one_batch_drains_fully_and_retry_is_idempotent() -> None:
    repository = FakeDrainRepository([_outcome(index) for index in range(1, 7)])
    service = H5NextOpenOrchestrationService(
        repository=repository,
        maturation_service=FakeMaturationService(),
    )
    db = FakeDrainDb()

    result = service.drain_due(db, now=_now(), batch_size=2, max_batches=10)
    retried = service.drain_due(db, now=_now(), batch_size=2, max_batches=10)

    assert result.due_h5_next_open == 6
    assert result.processed_h5 == 6
    assert result.matured_h5 == 6
    assert result.pending_h5_after_cycle == 0
    assert result.last_successful_full_drain_at is not None
    assert retried.processed_h5 == 0


def test_primary_h5_cannot_starve_behind_other_horizons() -> None:
    h1 = _outcome(1, horizon=1)
    h5 = _outcome(2)
    repository = FakeDrainRepository([h1, h5])
    service = H5NextOpenOrchestrationService(
        repository=repository,
        maturation_service=FakeMaturationService(),
    )

    result = service.drain_due(FakeDrainDb(), now=_now(), batch_size=1, max_batches=1)

    assert result.processed_h5 == 1
    assert h5.status == OutcomeStatus.MATURED
    assert h1.status == OutcomeStatus.PENDING


def test_partial_cycle_resumes_and_missing_bar_does_not_block_valid_rows() -> None:
    missing = _outcome(1)
    valid = [_outcome(index) for index in range(2, 6)]
    repository = FakeDrainRepository([missing, *valid])
    maturation = FakeMaturationService(missing_ids={1})
    service = H5NextOpenOrchestrationService(
        repository=repository,
        maturation_service=maturation,
    )
    db = FakeDrainDb()

    first = service.drain_due(db, now=_now(), batch_size=2, max_batches=1)
    second = service.drain_due(db, now=_now(), batch_size=2, max_batches=10)

    assert first.unvisited_h5_after_cycle == 3
    assert second.matured_h5 == 3
    assert all(row.status == OutcomeStatus.MATURED for row in valid)
    assert missing.status == OutcomeStatus.PENDING
    assert second.pending_h5_after_cycle == 1


def test_default_clock_reads_current_utc_time(monkeypatch) -> None:
    fixed_now = datetime(2026, 8, 14, 2, 34, 21, tzinfo=UTC)
    observed_timezones = []

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            observed_timezones.append(tz)
            return fixed_now

    monkeypatch.setattr(orchestration_module, "datetime", FrozenDateTime)
    repository = FakeDrainRepository([_outcome(1)])
    maturation = FakeMaturationService()
    service = H5NextOpenOrchestrationService(
        repository=repository,
        maturation_service=maturation,
    )

    service.drain_due(FakeDrainDb())

    assert observed_timezones == [UTC]
    assert repository.completed_ons
    assert set(repository.completed_ons) == {latest_completed_session(fixed_now)}
    assert maturation.now_values == [fixed_now]


def test_injected_time_controls_h5_due_session_and_maturation_clock() -> None:
    injected_now = datetime(2027, 1, 15, 22, 0, tzinfo=UTC)
    repository = FakeDrainRepository([_outcome(1)])
    maturation = FakeMaturationService()
    service = H5NextOpenOrchestrationService(
        repository=repository,
        maturation_service=maturation,
    )

    result = service.drain_due(FakeDrainDb(), now=injected_now)

    assert result.matured_h5 == 1
    assert repository.completed_ons
    assert set(repository.completed_ons) == {latest_completed_session(injected_now)}
    assert maturation.now_values == [injected_now]


class FakeDrainRepository:
    def __init__(self, rows: list[WinnerForwardOutcome]) -> None:
        self.rows = rows
        self.completed_ons = []

    def get_due_pending_forward_outcomes(
        self,
        _db,
        *,
        completed_on,
        limit,
        entry_model=None,
        horizon_sessions=None,
        due_session=None,
        exclude_ids=(),
    ):
        self.completed_ons.append(completed_on)
        return [
            row
            for row in self.rows
            if row.status == OutcomeStatus.PENDING
            and row.due_session <= completed_on
            and row.id not in exclude_ids
            and (entry_model is None or row.entry_model == entry_model)
            and (horizon_sessions is None or row.horizon_sessions == horizon_sessions)
        ][:limit]

    def h5_backlog(self, _db, *, completed_on):
        self.completed_ons.append(completed_on)
        rows = [
            row
            for row in self.rows
            if row.status == OutcomeStatus.PENDING
            and row.entry_model == EntryModel.NEXT_OPEN
            and row.horizon_sessions == 5
            and row.due_session <= completed_on
        ]
        return len(rows), min((row.due_session for row in rows), default=None)

    def count_unvisited_h5(self, _db, *, completed_on, exclude_ids):
        return sum(
            1
            for row in self.rows
            if row.status == OutcomeStatus.PENDING
            and row.entry_model == EntryModel.NEXT_OPEN
            and row.horizon_sessions == 5
            and row.due_session <= completed_on
            and row.id not in exclude_ids
        )


class FakeMaturationService:
    def __init__(self, missing_ids: set[int] | None = None) -> None:
        self.missing_ids = missing_ids or set()
        self.now_values = []

    def process_forward_outcome(self, _db, row, *, now):
        self.now_values.append(now)
        if row.id in self.missing_ids:
            row.metadata_json = {"pending_reason": "missing_entry_bar"}
            return OutcomeMaturationResult(processed=1, pending=1)
        row.status = OutcomeStatus.MATURED
        return OutcomeMaturationResult(processed=1, matured=1, target_stop_matured=1)


class FakeDrainDb:
    def flush(self) -> None:
        pass


def _outcome(id: int, *, horizon: int = 5) -> WinnerForwardOutcome:
    return WinnerForwardOutcome(
        id=id,
        prediction_id=id,
        entry_model=EntryModel.NEXT_OPEN,
        horizon_sessions=horizon,
        entry_session=date(2026, 7, 27),
        due_session=date(2026, 7, 31),
        status=OutcomeStatus.PENDING,
        revision=1,
        is_current_revision=True,
        metadata_json={},
    )


def _now() -> datetime:
    return datetime(2026, 8, 14, 2, 34, 21, tzinfo=UTC)
