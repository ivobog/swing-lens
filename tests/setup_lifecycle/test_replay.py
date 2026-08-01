from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from app.models.tables import SetupLifecycleEvaluationRun, SetupSignalSnapshot
from app.services.setup_lifecycle.replay_service import (
    SetupLifecycleReplayRequest,
    SetupLifecycleReplayService,
)


def test_replay_dry_run_returns_proposed_decisions_without_creating_run() -> None:
    repository = FakeReplayRepository()
    service = SetupLifecycleReplayService(repository=repository)

    result = service.replay(FakeReplayDb([_snapshot()]), SetupLifecycleReplayRequest())

    assert result["mode"] == "DRY_RUN_REPLAY"
    assert result["persisted"] is False
    assert result["evaluation_run_id"] is None
    assert result["snapshot_count"] == 1
    assert result["proposed"][0]["ticker"] == "MSFT"
    assert repository.created == []
    assert repository.completed == []


def test_persisted_replay_creates_new_evaluation_version() -> None:
    repository = FakeReplayRepository()
    service = SetupLifecycleReplayService(repository=repository)

    result = service.replay(
        FakeReplayDb([_snapshot()]),
        SetupLifecycleReplayRequest(
            ticker="msft",
            date_from=date(2026, 8, 1),
            date_to=date(2026, 8, 1),
            persist=True,
            requester="tester",
        ),
    )

    assert result["mode"] == "PERSISTED_REPLAY"
    assert result["evaluation_run_id"] == 91
    assert repository.created[0].output_evaluation_version.startswith("slse-")
    assert ":replay:" in repository.created[0].output_evaluation_version
    assert repository.completed[0].status == "COMPLETED"
    assert repository.completed[0].read_count == 1


class FakeReplayDb:
    def __init__(self, snapshots: list[SetupSignalSnapshot]) -> None:
        self.snapshots = snapshots

    def scalars(self, _statement):
        return self.snapshots


class FakeReplayRepository:
    def __init__(self) -> None:
        self.created: list[SetupLifecycleEvaluationRun] = []
        self.completed: list[SetupLifecycleEvaluationRun] = []

    @staticmethod
    def normalize_ticker(ticker: str) -> str:
        return ticker.strip().upper()

    def create_evaluation_run(self, _db, **kwargs) -> SetupLifecycleEvaluationRun:
        kwargs["ticker_scope_json"] = kwargs.pop("ticker_scope", [])
        kwargs["requested_config_json"] = kwargs.pop("requested_config", {})
        run = SetupLifecycleEvaluationRun(id=91, **kwargs)
        self.created.append(run)
        return run

    def complete_evaluation_run(
        self,
        _db,
        evaluation_run: SetupLifecycleEvaluationRun,
        *,
        status: str,
        current_phase: str,
        counts: dict[str, int],
    ) -> SetupLifecycleEvaluationRun:
        evaluation_run.status = status
        evaluation_run.current_phase = current_phase
        evaluation_run.read_count = counts["read"]
        evaluation_run.canonical_count = counts["canonical"]
        evaluation_run.transitioned_count = counts["transitioned"]
        self.completed.append(evaluation_run)
        return evaluation_run


def _snapshot() -> SetupSignalSnapshot:
    return SetupSignalSnapshot(
        id=10,
        ticker="MSFT",
        timeframe="1d",
        data_as_of_date=date(2026, 8, 1),
        calculated_at=datetime(2026, 8, 1, 21, tzinfo=UTC),
        origin_type="RUN_CAPTURE",
        engine_version="slse-1.0.0",
        config_version="v1",
        config_hash="hash",
        source_data_hash="source",
        schema_version="v1",
        is_canonical=True,
        data_quality_label="HIGH",
        setup_score=Decimal("8.25"),
        technical_classification="Breakout",
        close_above_trigger=True,
        signals_json={
            "setup_score": {"value": 8.25},
            "technical_score": {"value": 8.25},
            "classification": {"value": "Breakout"},
            "close_trigger_cross": {"value": True},
            "market_regime": {"value": "RISK_ON"},
        },
        source_lineage_json={"source": "test"},
    )
