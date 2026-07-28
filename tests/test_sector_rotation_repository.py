from datetime import date

from app.models.tables import SectorRotationRow, SectorRotationSnapshot
from app.services.sector_rotation_repository import (
    SectorRotationRepository,
    SectorRotationRowWrite,
    SectorRotationSnapshotWrite,
)


def test_save_snapshot_adds_snapshot_and_rows(monkeypatch) -> None:
    db = FakeDb()
    repo = SectorRotationRepository()
    monkeypatch.setattr(repo, "_matching_snapshot", lambda *_args: None)

    snapshot = repo.save_snapshot(db, _snapshot_write())

    assert db.added == [snapshot]
    assert len(db.added_all) == 1
    assert db.added_all[0][0].snapshot_id == 101
    assert db.flush_count == 3
    assert snapshot.run_id == 7
    assert snapshot.as_of_date == date(2026, 7, 28)
    assert snapshot.mode == "universe_only"
    assert snapshot.leading_sector == "Technology"
    assert snapshot.warning_flags_json == ["missing_etf_confirmation"]


def test_save_snapshot_replaces_existing_rows(monkeypatch) -> None:
    db = FakeDb()
    repo = SectorRotationRepository()
    existing = SectorRotationSnapshot(
        id=55,
        run_id=7,
        as_of_date=date(2026, 7, 28),
        calculation_version="sector-rotation-1.0.0",
        config_hash="hash-a",
        mode="universe_only",
        sector_count=1,
        ticker_count=10,
    )
    monkeypatch.setattr(repo, "_matching_snapshot", lambda *_args: existing)

    snapshot = repo.save_snapshot(
        db,
        _snapshot_write(leading_sector="Healthcare", rows=[_row_write("Healthcare")]),
    )

    assert snapshot is existing
    assert db.added == []
    assert db.executed
    assert "sector_rotation_rows" in str(db.executed[0])
    assert db.added_all[0][0].snapshot_id == 55
    assert snapshot.leading_sector == "Healthcare"


def test_latest_previous_history_and_row_queries_return_results() -> None:
    snapshot = SectorRotationSnapshot(
        id=10,
        run_id=7,
        as_of_date=date(2026, 7, 28),
        calculation_version="sector-rotation-1.0.0",
        config_hash="hash-a",
        mode="universe_only",
    )
    row = SectorRotationRow(
        id=20,
        snapshot_id=10,
        sector="Technology",
        sector_slug="technology",
        rotation_state="Leading",
        sector_permission="full_allowed",
        confidence="high",
    )
    db = FakeDb(
        scalar_results=[snapshot, snapshot, row],
        scalars_results=[[row], [snapshot]],
    )
    repo = SectorRotationRepository()

    assert repo.latest_for_run(db, run_id=7) is snapshot
    assert repo.get_previous_snapshot(
        db,
        as_of_date=date(2026, 7, 28),
        mode="universe_only",
        config_hash="hash-a",
        run_id=7,
    ) is snapshot
    assert repo.get_sector_row(db, snapshot_id=10, sector_slug="technology") is row
    assert repo.get_snapshot_rows(db, snapshot_id=10) == [row]
    assert repo.history(db, limit=0, run_id=7) == [snapshot]
    assert len(db.scalar_statements) == 3
    assert len(db.scalars_statements) == 2


def test_to_row_model_maps_write_payload() -> None:
    db = FakeDb()
    repo = SectorRotationRepository()

    repo.save_snapshot(db, _snapshot_write(rows=[_row_write("Technology")]))

    row = db.added_all[0][0]
    assert row.sector == "Technology"
    assert row.sector_slug == "technology"
    assert row.top_25_count == 6
    assert row.top_25_share == 0.4286
    assert row.sector_final_score == 8.1
    assert row.rotation_state == "Leading"
    assert row.sector_permission == "full_allowed"
    assert row.profile_distribution_json == {"momentum_swing": {"top_25_count": 6}}
    assert row.reason_codes_json == ["top_candidate_overrepresentation"]


def _snapshot_write(
    leading_sector: str = "Technology",
    rows: list[SectorRotationRowWrite] | None = None,
) -> SectorRotationSnapshotWrite:
    return SectorRotationSnapshotWrite(
        run_id=7,
        market_regime_snapshot_id=3,
        as_of_date=date(2026, 7, 28),
        calculation_version="sector-rotation-1.0.0",
        config_version="1.0.0",
        config_hash="hash-a",
        mode="universe_only",
        default_ranking_profile="momentum_swing",
        benchmark_ticker="SPY",
        sector_count=1,
        ticker_count=14,
        leading_sector=leading_sector,
        weakest_sector="Utilities",
        riskiest_sector="Energy",
        summary={"leading_sector": leading_sector},
        warning_flags=["missing_etf_confirmation"],
        debug={"source": "unit"},
        rows=rows if rows is not None else [_row_write(leading_sector)],
    )


def _row_write(sector: str) -> SectorRotationRowWrite:
    return SectorRotationRowWrite(
        sector=sector,
        sector_slug=sector.lower(),
        sector_proxy_ticker="XLK",
        ticker_count=14,
        universe_share=0.3333,
        average_fundamental_score=7.1,
        average_technical_score=8.2,
        average_final_score=7.8,
        average_profile_score=7.5,
        top_10_count=3,
        top_25_count=6,
        top_50_count=10,
        top_25_share=0.4286,
        buyable_count=4,
        watch_count=2,
        danger_count=1,
        buyable_share=0.2857,
        watch_share=0.1429,
        danger_share=0.0714,
        clean_pullback_count=1,
        breakout_count=2,
        vcp_count=1,
        tight_base_breakout_count=1,
        extended_or_overheated_count=0,
        missing_fundamental_count=0,
        missing_technical_count=0,
        universe_leadership_score=8.1,
        sector_final_score=8.1,
        rotation_state="Leading",
        sector_permission="full_allowed",
        position_size_multiplier=1.0,
        confidence="high",
        current_rank=1,
        profile_distribution={"momentum_swing": {"top_25_count": 6}},
        setup_distribution={"Fresh breakout": 2},
        warning_distribution={"liquidity_warning": 1},
        component_scores={"risk_control": 9.0},
        reason_codes=["top_candidate_overrepresentation"],
        warning_flags=[],
        debug={"source": "unit"},
    )


class FakeDb:
    def __init__(
        self,
        scalar_result=None,
        scalars_result=None,
        scalar_results=None,
        scalars_results=None,
    ) -> None:
        self.scalar_result = scalar_result
        self.scalars_result = scalars_result or []
        self.scalar_results = list(scalar_results or [])
        self.scalars_results = list(scalars_results or [])
        self.scalar_statements = []
        self.scalars_statements = []
        self.executed = []
        self.added = []
        self.added_all = []
        self.flush_count = 0
        self.next_id = 101

    def add(self, model) -> None:
        self.added.append(model)

    def add_all(self, models) -> None:
        self.added_all.append(list(models))

    def flush(self) -> None:
        for model in self.added:
            if isinstance(model, SectorRotationSnapshot) and model.id is None:
                model.id = self.next_id
        self.flush_count += 1

    def scalar(self, statement):
        self.scalar_statements.append(statement)
        if self.scalar_results:
            return self.scalar_results.pop(0)
        return self.scalar_result

    def scalars(self, statement):
        self.scalars_statements.append(statement)
        if self.scalars_results:
            return self.scalars_results.pop(0)
        return self.scalars_result

    def execute(self, statement):
        self.executed.append(statement)
