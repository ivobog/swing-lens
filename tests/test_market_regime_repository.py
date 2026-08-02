from datetime import date

from app.models.tables import MarketRegimeSnapshot
from app.services.market_regime_repository import (
    MarketRegimeRepository,
    MarketRegimeSnapshotWrite,
)


def test_upsert_snapshot_adds_new_snapshot(monkeypatch) -> None:
    db = FakeDb()
    repo = MarketRegimeRepository()
    monkeypatch.setattr(repo, "_matching_snapshot", lambda *_args: None)

    snapshot = repo.upsert_snapshot(db, _snapshot_write(), run_id=7)

    assert db.added == [snapshot]
    assert db.flush_count == 1
    assert snapshot.run_id == 7
    assert snapshot.as_of_date == date(2026, 7, 28)
    assert snapshot.regime == "Bull pullback"
    assert snapshot.risk_state == "Yellow"
    assert snapshot.position_size_multiplier == 0.75
    assert snapshot.preferred_profiles_json == ["quality_momentum"]
    assert snapshot.reduced_profiles_json == ["early_rocket"]
    assert snapshot.input_symbols_json == {"primary_market": "SPY", "risk_proxy": "QQQ"}


def test_upsert_snapshot_returns_existing_matching_evidence_without_mutation(monkeypatch) -> None:
    db = FakeDb()
    repo = MarketRegimeRepository()
    existing = MarketRegimeSnapshot(
        run_id=7,
        as_of_date=date(2026, 7, 28),
        calculation_version="mrcc-1.0.0",
        config_version="2026-07-28",
        regime="Choppy",
        risk_state="Yellow",
        score=5.0,
        risk_off=False,
        gate_ok=True,
        confidence="normal",
        action_summary="Old summary",
        position_size_multiplier=0.5,
    )
    monkeypatch.setattr(repo, "_matching_snapshot", lambda *_args: existing)

    snapshot = repo.upsert_snapshot(
        db,
        _snapshot_write(regime="Bull trend", risk_state="Green", score=9.0),
        run_id=7,
    )

    assert snapshot is existing
    assert db.added == []
    assert db.flush_count == 0
    assert snapshot.regime == "Choppy"
    assert snapshot.risk_state == "Yellow"
    assert snapshot.score == 5.0
    assert snapshot.action_summary == "Old summary"


def test_upsert_snapshot_creates_revision_for_changed_evidence() -> None:
    existing = MarketRegimeSnapshot(
        id=55,
        run_id=7,
        as_of_date=date(2026, 7, 28),
        calculation_version="mrcc-1.0.0",
        config_version="2026-07-28",
        evidence_hash="old-hash",
        revision=1,
        is_current_revision=True,
        regime="Choppy",
        risk_state="Yellow",
        score=5.0,
        risk_off=False,
        gate_ok=True,
        confidence="normal",
        action_summary="Old summary",
        position_size_multiplier=0.5,
    )
    db = FakeDb(scalar_results=[None, existing])

    snapshot = MarketRegimeRepository().upsert_snapshot(
        db,
        _snapshot_write(regime="Bull trend", risk_state="Green", score=9.0),
        run_id=7,
    )

    assert snapshot is not existing
    assert db.added == [snapshot]
    assert snapshot.revision == 2
    assert snapshot.is_current_revision is True
    assert snapshot.evidence_hash != existing.evidence_hash
    assert snapshot.regime == "Bull trend"
    assert existing.is_current_revision is False
    assert existing.superseded_by_snapshot_id == snapshot.id
    assert existing.superseded_at is not None


def test_delete_for_run_executes_delete_and_flushes() -> None:
    db = FakeDb()

    MarketRegimeRepository().delete_for_run(db, run_id=7)

    assert db.executed
    assert "market_regime_snapshots" in str(db.executed[0])
    assert db.flush_count == 1


def test_latest_methods_return_scalar_result() -> None:
    expected = MarketRegimeSnapshot(
        run_id=None,
        as_of_date=date(2026, 7, 28),
        calculation_version="mrcc-1.0.0",
        config_version="2026-07-28",
        regime="Bull trend",
        risk_state="Green",
        score=9.0,
        risk_off=False,
        gate_ok=True,
        confidence="normal",
        action_summary="Risk-on",
        position_size_multiplier=1.0,
    )
    db = FakeDb(scalar_result=expected)
    repo = MarketRegimeRepository()

    assert repo.latest(db) is expected
    assert repo.latest_for_run(db, run_id=7) is expected
    assert len(db.scalar_statements) == 2


def test_history_clamps_limit_and_returns_rows() -> None:
    expected = [
        MarketRegimeSnapshot(
            run_id=None,
            as_of_date=date(2026, 7, 28),
            calculation_version="mrcc-1.0.0",
            config_version="2026-07-28",
            regime="Bull trend",
            risk_state="Green",
            score=9.0,
            risk_off=False,
            gate_ok=True,
            confidence="normal",
            action_summary="Risk-on",
            position_size_multiplier=1.0,
        )
    ]
    db = FakeDb(scalars_result=expected)

    assert MarketRegimeRepository().history(db, limit=0) == expected
    assert db.scalars_statements


def _snapshot_write(
    regime: str = "Bull pullback",
    risk_state: str = "Yellow",
    score: float = 6.8,
) -> MarketRegimeSnapshotWrite:
    return MarketRegimeSnapshotWrite(
        as_of_date=date(2026, 7, 28),
        calculation_version="mrcc-1.0.0",
        config_version="2026-07-28",
        regime=regime,
        risk_state=risk_state,
        score=score,
        risk_off=False,
        gate_ok=True,
        confidence="normal",
        action_summary="Prefer quality pullbacks.",
        position_size_multiplier=0.75,
        preferred_profiles=["quality_momentum"],
        allowed_profiles=["quality_momentum", "clean_compounder_pullback"],
        reduced_profiles=["early_rocket"],
        blocked_profiles=[],
        allowed_setups=["Clean bull pullback"],
        blocked_setups=["Failed breakout"],
        input_symbols={"primary_market": "SPY", "risk_proxy": "QQQ"},
        index_health={"SPY": {"above_sma200": True}},
        universe_participation={"ticker_count": 12},
        sector_leadership=[{"sector": "Technology"}],
        reasons=["missing_qqq_market_data"],
        warnings=["low_market_confidence"],
        debug={"source": "unit"},
    )


class FakeDb:
    def __init__(self, scalar_result=None, scalars_result=None, scalar_results=None) -> None:
        self.scalar_result = scalar_result
        self.scalars_result = scalars_result or []
        self.scalar_results = list(scalar_results or [])
        self.scalar_statements = []
        self.scalars_statements = []
        self.executed = []
        self.added = []
        self.flush_count = 0
        self.next_id = 101

    def add(self, model) -> None:
        self.added.append(model)

    def flush(self) -> None:
        for model in self.added:
            if isinstance(model, MarketRegimeSnapshot) and model.id is None:
                model.id = self.next_id
                self.next_id += 1
        self.flush_count += 1

    def scalar(self, statement):
        self.scalar_statements.append(statement)
        if self.scalar_results:
            return self.scalar_results.pop(0)
        return self.scalar_result

    def scalars(self, statement):
        self.scalars_statements.append(statement)
        return self.scalars_result

    def execute(self, statement):
        self.executed.append(statement)
