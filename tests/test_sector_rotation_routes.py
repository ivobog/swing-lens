from datetime import date

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import create_app
from app.models.tables import SectorRotationRow, SectorRotationSnapshot
from app.routers import sector_rotation_routes
from app.services.sector_rotation_dtos import (
    SectorRotationDecision,
    SectorRotationSnapshotDto,
    SectorUniverseMetrics,
)


def test_sector_rotation_dashboard_returns_html(monkeypatch) -> None:
    monkeypatch.setattr(
        sector_rotation_routes,
        "_run_payload_or_calculate",
        lambda _db, _run_id: _payload(),
    )

    response = sector_rotation_routes.sector_rotation_dashboard(run_id=7, db=RouteFakeDb())

    assert response.status_code == 200
    assert "Run 7 Sector Rotation" in response.body.decode()
    assert "Technology" in response.body.decode()


def test_sector_rotation_drilldown_returns_html(monkeypatch) -> None:
    monkeypatch.setattr(
        sector_rotation_routes,
        "_run_payload_or_calculate",
        lambda _db, _run_id: _payload(),
    )

    response = sector_rotation_routes.sector_rotation_drilldown(
        run_id=7,
        sector_slug="technology",
        db=RouteFakeDb(),
    )

    assert response.status_code == 200
    assert "Technology Sector Rotation" in response.body.decode()


def test_sector_rotation_drilldown_404s_missing_sector(monkeypatch) -> None:
    monkeypatch.setattr(
        sector_rotation_routes,
        "_run_payload_or_calculate",
        lambda _db, _run_id: _payload(),
    )

    with pytest.raises(HTTPException) as exc:
        sector_rotation_routes.api_sector_rotation_drilldown(
            run_id=7,
            sector_slug="utilities",
            db=RouteFakeDb(),
        )

    assert exc.value.status_code == 404


def test_sector_rotation_api_returns_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        sector_rotation_routes,
        "_run_payload_or_calculate",
        lambda _db, _run_id: _payload(),
    )

    payload = sector_rotation_routes.api_sector_rotation(run_id=7, db=RouteFakeDb())

    assert payload["snapshot"]["run_id"] == 7
    assert payload["rows"][0]["sector"] == "Technology"


def test_sector_rotation_snapshot_history_api(monkeypatch) -> None:
    fake_repo = FakeRepository(history=[_snapshot(id=11), _snapshot(id=12)])
    monkeypatch.setattr(sector_rotation_routes, "SectorRotationRepository", lambda: fake_repo)

    payload = sector_rotation_routes.api_sector_rotation_snapshots(
        db=RouteFakeDb(),
        limit=2,
        run_id=7,
    )

    assert [snapshot["id"] for snapshot in payload] == [11, 12]
    assert fake_repo.history_args == {"limit": 2, "run_id": 7}


def test_sector_rotation_snapshot_api_returns_rows(monkeypatch) -> None:
    fake_repo = FakeRepository(rows=[_row()])
    monkeypatch.setattr(sector_rotation_routes, "SectorRotationRepository", lambda: fake_repo)
    db = RouteFakeDb(snapshot=_snapshot(id=11))

    payload = sector_rotation_routes.api_sector_rotation_snapshot(snapshot_id=11, db=db)

    assert payload["snapshot"]["id"] == 11
    assert payload["rows"][0]["sector"] == "Technology"


def test_recalculate_api_commits_and_returns_dto(monkeypatch) -> None:
    fake_service = FakeSectorRotationService()
    monkeypatch.setattr(sector_rotation_routes, "SectorRotationService", lambda: fake_service)
    db = RouteFakeDb()

    payload = sector_rotation_routes.recalculate_run_sector_rotation_api(run_id=7, db=db)

    assert payload["snapshot"]["run_id"] == 7
    assert fake_service.calls == [{"run_id": 7, "persist": True}]
    assert db.commits == 1
    assert db.rollbacks == 0


def test_recalculate_api_rolls_back_value_error(monkeypatch) -> None:
    monkeypatch.setattr(
        sector_rotation_routes,
        "SectorRotationService",
        lambda: FakeSectorRotationService(error=ValueError("bad sector data")),
    )
    db = RouteFakeDb()

    with pytest.raises(HTTPException) as exc:
        sector_rotation_routes.recalculate_run_sector_rotation_api(run_id=7, db=db)

    assert exc.value.status_code == 400
    assert db.commits == 0
    assert db.rollbacks == 1


def test_export_routes_return_attachments(monkeypatch) -> None:
    monkeypatch.setattr(
        sector_rotation_routes,
        "_run_snapshot_and_rows_or_404",
        lambda _db, _run_id: (_snapshot(), [_row()]),
    )

    csv_response = sector_rotation_routes.export_sector_rotation_dashboard_csv(
        run_id=7,
        db=RouteFakeDb(),
    )
    json_response = sector_rotation_routes.export_sector_rotation_dashboard_json(
        run_id=7,
        db=RouteFakeDb(),
    )
    markdown_response = sector_rotation_routes.export_sector_rotation_dashboard_markdown(
        run_id=7,
        db=RouteFakeDb(),
    )

    assert csv_response.media_type == "text/csv"
    assert b"rank,sector,state" in csv_response.body
    assert "sector_rotation.csv" in csv_response.headers["content-disposition"]
    assert json_response.media_type == "application/json"
    assert b'"sector": "Technology"' in json_response.body
    assert markdown_response.media_type == "text/markdown"
    assert b"Sector Rotation Brief" in markdown_response.body


def test_sector_rotation_routes_404_missing_run() -> None:
    with pytest.raises(HTTPException) as exc:
        sector_rotation_routes.api_sector_rotation(run_id=404, db=RouteFakeDb(run_exists=False))

    assert exc.value.status_code == 404


def test_sector_rotation_router_is_registered_in_app(monkeypatch) -> None:
    monkeypatch.setattr(
        sector_rotation_routes,
        "_run_payload_or_calculate",
        lambda _db, _run_id: _payload(),
    )
    app = create_app()
    app.dependency_overrides[get_db] = lambda: RouteFakeDb()
    client = TestClient(app)

    response = client.get("/api/runs/7/sector-rotation")

    assert response.status_code == 200
    assert response.json()["rows"][0]["sector"] == "Technology"


class RouteFakeDb:
    def __init__(
        self,
        run_exists: bool = True,
        snapshot: SectorRotationSnapshot | None = None,
    ) -> None:
        self.run_exists = run_exists
        self.snapshot = snapshot
        self.commits = 0
        self.rollbacks = 0

    def scalar(self, statement):
        if "upload_runs" in str(statement):
            return 7 if self.run_exists else None
        return None

    def get(self, model, _id):
        if model is SectorRotationSnapshot:
            return self.snapshot
        return None

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class FakeRepository:
    def __init__(
        self,
        history: list[SectorRotationSnapshot] | None = None,
        rows: list[SectorRotationRow] | None = None,
    ) -> None:
        self.history_rows = history or []
        self.rows = rows or []
        self.history_args = {}

    def history(self, _db, limit=30, run_id=None):
        self.history_args = {"limit": limit, "run_id": run_id}
        return self.history_rows

    def get_snapshot_rows(self, _db, _snapshot_id):
        return self.rows


class FakeSectorRotationService:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = []

    def build_sector_rotation_snapshot(self, _db, run_id=None, persist=True):
        if self.error:
            raise self.error
        self.calls.append({"run_id": run_id, "persist": persist})
        return _snapshot_dto()


def _payload() -> dict:
    return {
        "snapshot": {
            "id": None,
            "run_id": 7,
            "as_of_date": "2026-07-28",
            "mode": "universe_only",
            "summary": {"leading_sector": "Technology"},
            "warnings": [],
        },
        "rows": [
            {
                "rank": 1,
                "sector": "Technology",
                "sector_slug": "technology",
                "rotation_state": "Leading",
                "permission": "full_allowed",
                "sector_final_score": 8.1,
                "warnings": [],
                "reasons": [],
                "confidence": "high",
                "position_size_multiplier": 1.0,
            }
        ],
    }


def _snapshot(id: int = 5) -> SectorRotationSnapshot:
    return SectorRotationSnapshot(
        id=id,
        run_id=7,
        as_of_date=date(2026, 7, 28),
        calculation_version="sector-rotation-1.0.0",
        config_version="1.0.0",
        config_hash="hash-a",
        mode="universe_only",
        default_ranking_profile="momentum_swing",
        benchmark_ticker="SPY",
        sector_count=1,
        ticker_count=14,
        leading_sector="Technology",
        weakest_sector="Utilities",
        riskiest_sector="Utilities",
        summary_json={"leading_sector": "Technology"},
        warning_flags_json=[],
        debug_json={},
    )


def _row() -> SectorRotationRow:
    return SectorRotationRow(
        id=1,
        snapshot_id=5,
        sector="Technology",
        sector_slug="technology",
        ticker_count=14,
        top_25_count=6,
        top_25_share=0.4286,
        universe_leadership_score=8.1,
        sector_final_score=8.1,
        rotation_state="Leading",
        sector_permission="full_allowed",
        position_size_multiplier=1.0,
        confidence="high",
        current_rank=1,
        component_scores_json={"risk_control": 9.0},
        reason_codes_json=["top_candidate_overrepresentation"],
        warning_flags_json=[],
    )


def _snapshot_dto() -> SectorRotationSnapshotDto:
    universe = _universe_metrics()
    decision = SectorRotationDecision(
        sector="Technology",
        sector_slug="technology",
        final_score=8.1,
        universe_score=8.1,
        etf_score=None,
        rotation_state="Leading",
        permission="full_allowed",
        position_size_multiplier=1.0,
        confidence="high",
        rank=1,
        previous_rank=None,
        rank_change=None,
        score_change=None,
        reasons=["top_candidate_overrepresentation"],
        warnings=[],
        debug={},
    )
    return SectorRotationSnapshotDto(
        run_id=7,
        as_of_date="2026-07-28",
        mode="universe_only",
        calculation_version="sector-rotation-1.0.0",
        config_version="1.0.0",
        config_hash="hash-a",
        default_ranking_profile="momentum_swing",
        rows=[decision],
        universe_rows=[universe],
        summary={"leading_sector": "Technology"},
        warnings=[],
        debug={},
    )


def _universe_metrics() -> SectorUniverseMetrics:
    return SectorUniverseMetrics(
        sector="Technology",
        sector_slug="technology",
        ticker_count=14,
        universe_share=1.0,
        average_fundamental_score=7.0,
        average_technical_score=8.1,
        average_final_score=8.1,
        average_profile_score=8.1,
        top_counts={"top_10": 3, "top_25": 6, "top_50": 10},
        setup_distribution={},
        warning_distribution={},
        buyable_count=3,
        watch_count=0,
        danger_count=0,
        buyable_share=0.2143,
        watch_share=0.0,
        danger_share=0.0,
        clean_pullback_count=1,
        breakout_count=1,
        vcp_count=0,
        tight_base_breakout_count=0,
        extended_or_overheated_count=0,
        missing_fundamental_count=0,
        missing_technical_count=0,
        component_scores={"risk_control": 9.0},
        universe_leadership_score=8.1,
        confidence="high",
        reason_codes=[],
        warnings=[],
        debug={},
    )
