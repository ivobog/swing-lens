from datetime import date

import pytest
from fastapi import HTTPException

from app.models.tables import MarketRegimeSnapshot
from app.routers import market_regime_routes


def test_market_regime_page_returns_html(monkeypatch) -> None:
    monkeypatch.setattr(
        market_regime_routes,
        "_latest_or_calculate",
        lambda _db: _snapshot(regime="Bull trend", risk_state="Green"),
    )

    response = market_regime_routes.market_regime_page(request=object(), db=RouteFakeDb())

    assert response.media_type == "text/html"
    assert b"Market Regime Command Center" in response.body
    assert b"Bull trend" in response.body


def test_latest_api_returns_snapshot_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        market_regime_routes,
        "_latest_snapshot_or_404",
        lambda _db: _snapshot(),
    )

    payload = market_regime_routes.latest_market_regime_api(db=RouteFakeDb())

    assert payload["regime"] == "Bull pullback"
    assert payload["calculation_version"] == "mrcc-1.0.0"
    assert payload["as_of_date"] == "2026-07-28"


def test_latest_api_raises_404_without_snapshot() -> None:
    with pytest.raises(HTTPException) as exc:
        market_regime_routes._latest_snapshot_or_404(RouteFakeDb(snapshot=None))

    assert exc.value.status_code == 404


def test_history_api_returns_ordered_payload(monkeypatch) -> None:
    fake_repo = FakeRepository(history=[_snapshot(regime="Bull trend"), _snapshot(regime="Choppy")])
    monkeypatch.setattr(market_regime_routes, "MarketRegimeRepository", lambda: fake_repo)

    payload = market_regime_routes.market_regime_history_api(db=RouteFakeDb(), limit=2)

    assert [row["regime"] for row in payload] == ["Bull trend", "Choppy"]
    assert fake_repo.history_limit == 2


def test_run_api_returns_run_snapshot(monkeypatch) -> None:
    fake_repo = FakeRepository(run_snapshot=_snapshot(run_id=7))
    monkeypatch.setattr(market_regime_routes, "MarketRegimeRepository", lambda: fake_repo)

    payload = market_regime_routes.run_market_regime_api(run_id=7, db=RouteFakeDb())

    assert payload["run_id"] == 7
    assert payload["regime"] == "Bull pullback"


def test_run_api_404s_missing_snapshot(monkeypatch) -> None:
    fake_repo = FakeRepository(run_snapshot=None)
    monkeypatch.setattr(market_regime_routes, "MarketRegimeRepository", lambda: fake_repo)

    with pytest.raises(HTTPException) as exc:
        market_regime_routes.run_market_regime_api(run_id=7, db=RouteFakeDb())

    assert exc.value.status_code == 404


def test_recalculate_run_api_commits_and_returns_snapshot(monkeypatch) -> None:
    fake_repo = FakeRepository(run_snapshot=_snapshot(run_id=7))
    fake_service = FakeCommandCenterService()
    monkeypatch.setattr(market_regime_routes, "MarketRegimeRepository", lambda: fake_repo)
    monkeypatch.setattr(
        market_regime_routes,
        "MarketRegimeCommandCenterService",
        lambda: fake_service,
    )
    db = RouteFakeDb()

    payload = market_regime_routes.recalculate_run_market_regime_api(run_id=7, db=db)

    assert payload["run_id"] == 7
    assert fake_service.calls == [7]
    assert db.commits == 1
    assert db.rollbacks == 0


def test_recalculate_run_api_rolls_back_service_error(monkeypatch) -> None:
    monkeypatch.setattr(
        market_regime_routes,
        "MarketRegimeCommandCenterService",
        lambda: FakeCommandCenterService(error=ValueError("bad market data")),
    )
    db = RouteFakeDb()

    with pytest.raises(HTTPException) as exc:
        market_regime_routes.recalculate_run_market_regime_api(run_id=7, db=db)

    assert exc.value.status_code == 400
    assert db.commits == 0
    assert db.rollbacks == 1


def test_export_latest_json_returns_attachment(monkeypatch) -> None:
    monkeypatch.setattr(
        market_regime_routes,
        "_latest_snapshot_or_404",
        lambda _db: _snapshot(),
    )

    response = market_regime_routes.export_latest_market_regime_json(db=RouteFakeDb())

    assert response.media_type == "application/json"
    assert b'"regime": "Bull pullback"' in response.body
    assert "market_regime_latest.json" in response.headers["content-disposition"]


def test_export_run_csv_returns_attachment(monkeypatch) -> None:
    monkeypatch.setattr(market_regime_routes, "_require_run", lambda _db, _run_id: None)
    monkeypatch.setattr(
        market_regime_routes,
        "_run_snapshot_or_404",
        lambda _db, _run_id: _snapshot(run_id=7),
    )

    response = market_regime_routes.export_run_market_regime_csv(
        run_id=7,
        db=RouteFakeDb(),
    )

    assert response.media_type == "text/csv"
    assert b"as_of_date,regime,risk_state" in response.body
    assert "swinglens_run_7_market_regime.csv" in response.headers["content-disposition"]


def test_run_routes_404_missing_run() -> None:
    with pytest.raises(HTTPException) as exc:
        market_regime_routes.run_market_regime_api(run_id=404, db=RouteFakeDb(run_exists=False))

    assert exc.value.status_code == 404


class RouteFakeDb:
    def __init__(
        self,
        run_exists: bool = True,
        snapshot: MarketRegimeSnapshot | None = None,
    ) -> None:
        self.run_exists = run_exists
        self.snapshot = snapshot
        self.commits = 0
        self.rollbacks = 0

    def scalar(self, statement):
        if "upload_runs" in str(statement):
            return 7 if self.run_exists else None
        return self.snapshot

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class FakeRepository:
    def __init__(
        self,
        history: list[MarketRegimeSnapshot] | None = None,
        run_snapshot: MarketRegimeSnapshot | None = None,
    ) -> None:
        self.history_rows = history or []
        self.run_snapshot = run_snapshot
        self.history_limit = None

    def history(self, _db, limit=30):
        self.history_limit = limit
        return self.history_rows

    def latest_for_run(self, _db, _run_id):
        return self.run_snapshot


class FakeCommandCenterService:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = []

    def build_snapshot(self, _db, run_id=None):
        if self.error:
            raise self.error
        self.calls.append(run_id)


def _snapshot(
    regime: str = "Bull pullback",
    risk_state: str = "Yellow",
    run_id: int | None = None,
) -> MarketRegimeSnapshot:
    return MarketRegimeSnapshot(
        id=3,
        run_id=run_id,
        as_of_date=date(2026, 7, 28),
        calculation_version="mrcc-1.0.0",
        config_version="2026-07-28",
        regime=regime,
        risk_state=risk_state,
        score=6.8,
        risk_off=False,
        gate_ok=True,
        confidence="normal",
        action_summary="Prefer quality pullbacks.",
        position_size_multiplier=0.75,
        preferred_profiles_json=["quality_momentum"],
        allowed_profiles_json=["quality_momentum", "defensive_quality"],
        reduced_profiles_json=["early_rocket"],
        blocked_profiles_json=[],
        allowed_setups_json=["Clean bull pullback"],
        blocked_setups_json=["Failed breakout"],
        input_symbols_json={"primary_market": "SPY"},
        index_health_json={"SPY": {"above_sma200": True}},
        universe_participation_json={"ticker_count": 42},
        sector_leadership_json=[{"sector": "Technology"}],
        reasons_json=["missing_qqq_market_data"],
        warnings_json=["low_market_confidence"],
        debug_json={"source": "unit"},
    )
