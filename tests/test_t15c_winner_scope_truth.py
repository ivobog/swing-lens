from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from app.models.tables import WinnerForwardOutcome
from app.services.winner_probability.outcome_orchestration_service import (
    H5NextOpenOrchestrationService,
    H5QueueState,
)
from app.services.winner_probability.outcome_service import OutcomeMaturationResult


@dataclass
class _Db:
    flushes: int = 0

    def flush(self) -> None:
        self.flushes += 1


class _FrozenRepository:
    def __init__(self, rows: list[WinnerForwardOutcome]) -> None:
        self.rows = rows
        self.selections: list[tuple[int, ...] | None] = []

    def get_due_pending_forward_outcomes(self, _db, **kwargs):
        prediction_ids = kwargs.get("prediction_ids")
        self.selections.append(prediction_ids)
        excluded = set(kwargs.get("exclude_ids") or ())
        retained = set(prediction_ids) if prediction_ids is not None else None
        return [
            row
            for row in self.rows
            if row.status == "PENDING"
            and row.id not in excluded
            and (retained is None or row.prediction_id in retained)
        ][: kwargs["limit"]]

    def h5_queue_state(self, _db, **kwargs):
        excluded = set(kwargs.get("exclude_ids") or ())
        prediction_ids = kwargs.get("prediction_ids")
        retained = set(prediction_ids) if prediction_ids is not None else None
        rows = [
            row
            for row in self.rows
            if row.status == "PENDING"
            and row.id not in excluded
            and (retained is None or row.prediction_id in retained)
        ]
        return H5QueueState(len(rows), len(rows), 0, date(2026, 8, 7) if rows else None, None)


class _Maturer:
    def process_forward_outcome(self, _db, row, **_kwargs):
        row.status = "MATURED"
        return OutcomeMaturationResult(processed=1, matured=1)


def _outcome(value: int) -> WinnerForwardOutcome:
    return WinnerForwardOutcome(
        id=value,
        prediction_id=value,
        entry_model="NEXT_OPEN",
        horizon_sessions=5,
        entry_session=date(2026, 8, 3),
        due_session=date(2026, 8, 7),
        status="PENDING",
        revision=1,
        is_current_revision=True,
        metadata_json={},
    )


def test_maturation_continuation_never_adds_newly_due_prediction() -> None:
    rows = [_outcome(value) for value in range(1, 5)]
    repository = _FrozenRepository(rows)
    service = H5NextOpenOrchestrationService(
        repository=repository,
        maturation_service=_Maturer(),
    )
    retained = (1, 2, 3)

    first = service.drain_due(
        _Db(),
        now=datetime(2026, 8, 7, 22, 0, tzinfo=UTC),
        operation_cutoff_at=datetime(2026, 8, 7, 22, 0, tzinfo=UTC),
        batch_size=1,
        max_batches=1,
        prediction_ids=retained,
    )
    assert first.processed_h5 == 1
    assert first.eligible_remaining == 2

    # P4 is already present/current in the repository, but is not a member of
    # the retained S1/R1 population used by the continuation.
    resumed = service.drain_due(
        _Db(),
        now=datetime(2026, 8, 10, 22, 0, tzinfo=UTC),
        operation_cutoff_at=datetime(2026, 8, 7, 22, 0, tzinfo=UTC),
        batch_size=10,
        max_batches=1,
        prediction_ids=retained,
    )
    assert resumed.processed_h5 == 2
    assert rows[3].status == "PENDING"
    assert all(selection == retained for selection in repository.selections)


def test_price_truth_contract_names_the_unavailable_revision_state() -> None:
    from pathlib import Path

    source = Path("app/services/winner_probability/outcome_authority.py").read_text(
        encoding="utf-8"
    )
    assert '"REVISION_IDENTITY_UNAVAILABLE"' in source
    assert '"EXACT" if revision is not None' in source
