from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.tables import RankingResult
from app.routers import run_routes


def test_list_ranking_profiles_route_returns_enabled_profiles() -> None:
    payload = run_routes.list_ranking_profiles(run_id=7, db=RouteFakeDb())

    assert [profile["name"] for profile in payload] == [
        "momentum_swing",
        "quality_momentum",
        "early_rocket",
        "clean_compounder_pullback",
        "defensive_quality",
    ]
    assert payload[0]["weights"] == {"technical": 0.55, "fundamental": 0.45}


def test_refresh_all_ranking_profiles_route_commits(monkeypatch) -> None:
    monkeypatch.setattr(
        run_routes,
        "refresh_all_ranking_profiles",
        lambda _db, _run_id: [
            SimpleNamespace(ranking_profile="momentum_swing"),
            SimpleNamespace(ranking_profile="quality_momentum"),
        ],
    )
    db = RouteFakeDb()

    payload = run_routes.refresh_all_ranking_profiles_action(run_id=7, db=db)

    assert payload == {"run_id": 7, "profile_count": 2, "result_count": 2}
    assert db.commits == 1
    assert db.rollbacks == 0


def test_refresh_all_ranking_profiles_route_can_redirect_for_browser(monkeypatch) -> None:
    monkeypatch.setattr(
        run_routes,
        "refresh_all_ranking_profiles",
        lambda _db, _run_id: [
            SimpleNamespace(ranking_profile="momentum_swing"),
            SimpleNamespace(ranking_profile="quality_momentum"),
        ],
    )
    db = RouteFakeDb()

    response = run_routes.refresh_all_ranking_profiles_action(
        run_id=7,
        db=db,
        redirect=True,
    )

    assert db.commits == 1
    assert "ranking-profiles-refreshed" in response.headers["location"]
    assert "Refreshed+2+ranking+rows" in response.headers["location"]


def test_refresh_one_ranking_profile_route_commits(monkeypatch) -> None:
    monkeypatch.setattr(
        run_routes,
        "refresh_ranking_profile",
        lambda _db, _run_id, profile_name: [
            SimpleNamespace(ranking_profile=profile_name),
            SimpleNamespace(ranking_profile=profile_name),
        ],
    )
    db = RouteFakeDb()

    payload = run_routes.refresh_ranking_profile_action(
        run_id=7,
        profile_name="early_rocket",
        db=db,
    )

    assert payload == {
        "run_id": 7,
        "profile_name": "early_rocket",
        "result_count": 2,
    }
    assert db.commits == 1


def test_refresh_ranking_profile_route_rolls_back_service_error(monkeypatch) -> None:
    def fail_refresh(_db, _run_id, _profile_name):
        raise ValueError("unknown ranking profile")

    monkeypatch.setattr(run_routes, "refresh_ranking_profile", fail_refresh)
    db = RouteFakeDb()

    with pytest.raises(HTTPException) as exc:
        run_routes.refresh_ranking_profile_action(
            run_id=7,
            profile_name="unknown",
            db=db,
        )

    assert exc.value.status_code == 404
    assert db.commits == 0
    assert db.rollbacks == 1


def test_view_ranking_profile_results_route_returns_ranked_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        run_routes,
        "get_ranking_results",
        lambda _db, _run_id, _profile_name: [
            _ranking_result("MSFT", rank=1),
            _ranking_result("AAPL", rank=2, score="7.25"),
        ],
    )

    payload = run_routes.view_ranking_profile_results(
        run_id=7,
        profile_name="momentum_swing",
        db=RouteFakeDb(),
    )

    assert payload["run_id"] == 7
    assert payload["profile"]["name"] == "momentum_swing"
    assert [result["ticker"] for result in payload["results"]] == ["MSFT", "AAPL"]
    assert payload["results"][0]["profile_score"] == 8.75
    assert payload["results"][0]["warning_flags"] == ["earnings_medium_risk"]
    assert payload["results"][0]["earnings_date"] == "2026-07-14"


def test_view_ranking_profile_results_route_404s_unknown_profile() -> None:
    with pytest.raises(HTTPException) as exc:
        run_routes.view_ranking_profile_results(
            run_id=7,
            profile_name="unknown",
            db=RouteFakeDb(),
        )

    assert exc.value.status_code == 404


def test_export_ranking_profile_results_route_returns_csv(monkeypatch) -> None:
    monkeypatch.setattr(
        run_routes,
        "export_ranking_profile_csv",
        lambda _db, _run_id, _profile_name: "rank,ticker\n1,MSFT\n",
    )

    response = run_routes.export_ranking_profile_results(
        run_id=7,
        profile_name="momentum_swing",
        db=RouteFakeDb(),
    )

    assert response.media_type == "text/csv"
    assert response.body == b"rank,ticker\n1,MSFT\n"
    assert "momentum_swing_rankings.csv" in response.headers["content-disposition"]


def test_export_all_ranking_results_route_returns_csv(monkeypatch) -> None:
    monkeypatch.setattr(
        run_routes,
        "export_all_ranking_profiles_csv",
        lambda _db, _run_id: "rank,ticker\n1,MSFT\n",
    )

    response = run_routes.export_all_ranking_results(run_id=7, db=RouteFakeDb())

    assert response.media_type == "text/csv"
    assert response.body == b"rank,ticker\n1,MSFT\n"
    assert "ranking_profiles.csv" in response.headers["content-disposition"]


def test_ranking_routes_404_missing_run() -> None:
    with pytest.raises(HTTPException) as exc:
        run_routes.list_ranking_profiles(run_id=404, db=RouteFakeDb(run_exists=False))

    assert exc.value.status_code == 404


class RouteFakeDb:
    def __init__(self, run_exists: bool = True) -> None:
        self.run_exists = run_exists
        self.commits = 0
        self.rollbacks = 0

    def scalar(self, statement):
        if "upload_runs" in str(statement) and self.run_exists:
            return 7
        return None

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _ranking_result(
    ticker: str,
    *,
    rank: int,
    score: str = "8.75",
) -> RankingResult:
    return RankingResult(
        run_id=7,
        ticker=ticker,
        company_name=f"{ticker} Corp",
        sector="Technology",
        ranking_profile="momentum_swing",
        ranking_label="Momentum Swing",
        profile_rank=rank,
        profile_score=Decimal(score),
        technical_profile_score=Decimal("8.44"),
        fundamental_score=Decimal("7.90"),
        base_technical_score=Decimal("8.20"),
        technical_classification="Prime clean pullback",
        fundamental_label="High-quality quant",
        decision_label="Strong candidate",
        position_size_hint="Full starter",
        notes="aligned",
        warning_flags_json=["earnings_medium_risk"],
        penalties_json={},
        gates_json={},
        component_scores_json={},
        debug_json={},
        upcoming_earnings_date=date(2026, 7, 14),
        days_until_earnings=7,
        earnings_risk_level="medium",
        is_complete=True,
        has_warning=True,
        has_fundamental=True,
        has_technical=True,
        sort_bucket=0,
    )
