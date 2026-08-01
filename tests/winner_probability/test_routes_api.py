from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import create_app
from app.models.tables import BackgroundJob
from app.routers import winner_probability_routes
from app.services.background_job_service import JobStatus
from app.services.winner_probability.api_service import WinnerProbabilityApiError
from app.services.winner_probability.job_handlers import WINNER_COHORT_REFRESH
from app.settings import Settings


def test_run_api_builds_phase_7_query_filters_and_sort(monkeypatch) -> None:
    service = FakeApiService()
    monkeypatch.setattr(
        winner_probability_routes,
        "WinnerProbabilityApiService",
        lambda: service,
    )

    payload = winner_probability_routes.winner_probability_run(
        run_id=7,
        db=RouteFakeDb(run_exists=True),
        outcome_definition_id="T2_5_S2_0_H5_NEXT_OPEN",
        probability_min=0.6,
        lower_bound_min=0.45,
        interval_width_max=0.2,
        evidence_grade="High",
        effective_sample_size_min=50,
        ranking_profile="momentum_swing",
        market_risk_state="Green",
        sector_state="Leading",
        earnings_risk="low",
        data_quality="ok",
        sort="lower_bound",
        direction="desc",
    )

    query = service.last_query
    assert payload["run_id"] == 7
    assert query.outcome_definition_id == "T2_5_S2_0_H5_NEXT_OPEN"
    assert query.sort == "lower_bound"
    assert query.direction == "desc"
    assert query.filters.probability_min == 0.6
    assert query.filters.lower_bound_min == 0.45
    assert query.filters.interval_width_max == 0.2
    assert query.filters.evidence_grade == "High"
    assert query.filters.effective_sample_size_min == 50
    assert query.filters.ranking_profile == "momentum_swing"
    assert query.filters.market_risk_state == "Green"
    assert query.filters.sector_state == "Leading"
    assert query.filters.earnings_risk == "low"
    assert query.filters.data_quality == "ok"


def test_run_api_rejects_invalid_filters_with_structured_error() -> None:
    with pytest.raises(HTTPException) as exc:
        winner_probability_routes.winner_probability_run(
            run_id=7,
            db=RouteFakeDb(run_exists=True),
            evidence_grade="Excellent",
        )

    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "INVALID_FILTER"


def test_prediction_api_maps_not_found_to_structured_error(monkeypatch) -> None:
    class MissingPredictionApiService:
        def get_prediction_detail(self, *_args, **_kwargs):
            raise WinnerProbabilityApiError(
                "PREDICTION_NOT_FOUND",
                "Prediction was not found.",
                status_code=404,
            )

    monkeypatch.setattr(
        winner_probability_routes,
        "WinnerProbabilityApiService",
        lambda: MissingPredictionApiService(),
    )

    with pytest.raises(HTTPException) as exc:
        winner_probability_routes.winner_probability_prediction(
            prediction_id=404,
            db=RouteFakeDb(run_exists=True),
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == {
        "code": "PREDICTION_NOT_FOUND",
        "message": "Prediction was not found.",
    }


def test_reproduction_endpoint_returns_comparison_payload(monkeypatch) -> None:
    class FakeReproductionService:
        def reproduce_estimate(self, _db, *, estimate_id: int):
            assert estimate_id == 11
            return SimpleNamespace(
                estimate_id=11,
                matches=False,
                mismatches=("point_probability",),
                evidence_manifest_hash="manifest-hash",
                point_probability=Decimal("0.612345"),
                sample_n=42,
            )

    monkeypatch.setattr(
        winner_probability_routes,
        "ReproductionService",
        lambda: FakeReproductionService(),
    )

    payload = winner_probability_routes.winner_probability_estimate_reproduction(
        estimate_id=11,
        db=RouteFakeDb(run_exists=True),
    )

    assert payload == {
        "estimate_id": 11,
        "matches": False,
        "mismatches": ["point_probability"],
        "evidence_manifest_hash": "manifest-hash",
        "point_probability": 0.612345,
        "sample_n": 42,
    }


def test_run_export_route_returns_csv_attachment(monkeypatch) -> None:
    monkeypatch.setattr(
        winner_probability_routes,
        "winner_probability_run",
        lambda **_kwargs: {"items": []},
    )

    response = winner_probability_routes.export_winner_probability_run_csv(
        run_id=7,
        db=RouteFakeDb(run_exists=True),
    )

    assert response.media_type == "text/csv"
    assert response.body.startswith(b"ticker,prediction_id,prediction_as_of_date")
    assert "swinglens_run_7_owpe.csv" in response.headers["content-disposition"]


def test_cohort_refresh_admin_endpoint_queues_job() -> None:
    db = AdminRouteFakeDb(run_exists=True)
    app = create_app(
        Settings(
            _env_file=None,
            job_worker_enabled=False,
            winner_probability_admin_enabled=True,
        )
    )
    app.dependency_overrides[get_db] = lambda: db

    response = TestClient(app).post(
        "/api/winner-probability/cohorts/refresh"
        "?outcome_definition_id=T2_5_S2_0_H5_NEXT_OPEN"
    )

    assert response.status_code == 200
    assert response.json() == {
        "job_id": 1,
        "job_type": WINNER_COHORT_REFRESH,
        "status": JobStatus.QUEUED,
        "payload": {"outcome_definition_id": "T2_5_S2_0_H5_NEXT_OPEN"},
    }
    assert db.jobs[0].job_type == WINNER_COHORT_REFRESH
    assert db.commits == 1


def test_model_retire_admin_endpoint_returns_structured_block(monkeypatch) -> None:
    class BlockingApiService:
        def retire_model(self, *_args, **_kwargs):
            raise WinnerProbabilityApiError(
                "MODEL_RETIREMENT_BLOCKED",
                "Cannot retire the only active model.",
                status_code=409,
            )

    monkeypatch.setattr(
        winner_probability_routes,
        "WinnerProbabilityApiService",
        lambda: BlockingApiService(),
    )
    db = AdminRouteFakeDb(run_exists=True)
    app = create_app(
        Settings(
            _env_file=None,
            job_worker_enabled=False,
            winner_probability_admin_enabled=True,
        )
    )
    app.dependency_overrides[get_db] = lambda: db

    response = TestClient(app).post("/api/winner-probability/models/9/retire")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "MODEL_RETIREMENT_BLOCKED"
    assert db.rollbacks == 1


class FakeApiService:
    def __init__(self) -> None:
        self.last_query = None

    def get_run_evidence(self, _db, *, run_id: int, query):
        self.last_query = query
        return {
            "run_id": run_id,
            "items": [],
            "outcome_definition": {"definition_id": query.outcome_definition_id},
        }


class RouteFakeDb:
    def __init__(self, *, run_exists: bool) -> None:
        self.run_exists = run_exists

    def scalar(self, _statement):
        return 7 if self.run_exists else None


class AdminRouteFakeDb:
    def __init__(self, *, run_exists: bool) -> None:
        self.run_exists = run_exists
        self.jobs: list[BackgroundJob] = []
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0
        self._next_id = 1

    def scalar(self, _statement):
        return 7 if self.run_exists else None

    def add(self, row) -> None:
        if getattr(row, "id", None) is None:
            row.id = self._next_id
            self._next_id += 1
        if isinstance(row, BackgroundJob):
            self.jobs.append(row)

    def flush(self) -> None:
        self.flushes += 1

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1
