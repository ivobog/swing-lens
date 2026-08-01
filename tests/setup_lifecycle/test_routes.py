from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.main import create_app
from app.routers import setup_lifecycle_routes as routes
from app.services.setup_lifecycle.query_service import SetupLifecycleQueryError
from app.settings import Settings


def test_changes_route_forwards_dashboard_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_service = FakeQueryService()
    monkeypatch.setattr(routes, "SetupLifecycleQueryService", lambda: fake_service)

    payload = routes.setup_lifecycle_changes(
        db=object(),  # type: ignore[arg-type]
        ticker="msft",
        sector="Technology",
        setup_family="BREAKOUT",
        lifecycle_state="TRIGGERED",
        transition="STATE_TRANSITION",
        actionability="ACTIONABLE",
        confidence_min=80,
        confidence_max=100,
        state_age_min=1,
        state_age_max=5,
        setup_score_min=7.5,
        setup_score_max=10,
        trigger_distance_min=-2,
        trigger_distance_max=3,
        sector_rank_min=1,
        sector_rank_max=10,
        velocity_min=0.1,
        velocity_max=2,
        market_regime="RISK_ON",
        warning_flag="MISSING_SECTOR_ROTATION",
        sort="score",
        direction="asc",
        limit=25,
        cursor="50",
    )

    query = fake_service.last_changes_query
    assert payload["items"] == []
    assert query.filters.ticker == "msft"
    assert query.filters.sector == "Technology"
    assert query.filters.setup_score_min == 7.5
    assert query.filters.trigger_distance_max == 3
    assert query.filters.velocity_min == 0.1
    assert query.sort == "score"
    assert query.direction == "asc"
    assert query.limit == 25
    assert query.cursor == "50"


def test_alerts_route_uses_status_query_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_service = FakeQueryService()
    monkeypatch.setattr(routes, "SetupLifecycleQueryService", lambda: fake_service)

    routes.setup_lifecycle_alerts(
        db=object(),  # type: ignore[arg-type]
        ticker="msft",
        status="ACKNOWLEDGED",
        severity="RISK",
    )

    query = fake_service.last_alerts_query
    assert query.filters.alert_status == "ACKNOWLEDGED"
    assert query.filters.alert_severity == "RISK"


def test_query_errors_are_mapped_to_http_errors() -> None:
    with pytest.raises(HTTPException) as exc:
        routes._query_or_http(  # noqa: SLF001
            lambda: (_ for _ in ()).throw(
                SetupLifecycleQueryError("INVALID_CURSOR", "bad cursor", status_code=400)
            )
        )

    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "INVALID_CURSOR"


def test_export_routes_return_csv_and_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_service = FakeQueryService(
        changes_payload={"items": [{"id": 1, "ticker": "MSFT"}], "total": 1}
    )
    monkeypatch.setattr(routes, "SetupLifecycleQueryService", lambda: fake_service)

    csv_response = routes.export_setup_lifecycle_changes_csv(db=object())  # type: ignore[arg-type]
    json_response = routes.export_setup_lifecycle_changes_json(db=object())  # type: ignore[arg-type]

    assert csv_response.media_type == "text/csv"
    assert "id,ticker,effective_date" in csv_response.body.decode()
    assert json.loads(json_response.body)["total"] == 1


def test_evaluation_route_reads_requested_run(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_service = FakeQueryService()
    monkeypatch.setattr(routes, "SetupLifecycleQueryService", lambda: fake_service)

    payload = routes.setup_lifecycle_evaluation(
        evaluation_id=99,
        db=object(),  # type: ignore[arg-type]
    )

    assert payload == {"id": 99}
    assert fake_service.last_evaluation_id == 99


def test_evaluate_route_queues_background_job(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "_require_run", lambda _db, _run_id: None)
    monkeypatch.setattr(
        routes,
        "enqueue_job",
        lambda *_args, **_kwargs: SimpleNamespace(id=44, status="QUEUED"),
    )
    db = SimpleNamespace(commit=lambda: None)

    response = routes.evaluate_setup_lifecycle_run(
        db=db,  # type: ignore[arg-type]
        request=SimpleNamespace(query_params={"run_id": "7"}),
    )

    assert response.status_code == 202
    assert json.loads(response.body) == {
        "job_id": 44,
        "run_id": 7,
        "status": "QUEUED",
        "status_url": "/api/setup-lifecycle/evaluations/44",
    }


def test_create_app_registers_phase_9_routes() -> None:
    app = create_app(Settings(_env_file=None, job_worker_enabled=False))
    paths = {route.path for route in app.routes}

    assert "/api/setup-lifecycle/changes" in paths
    assert "/api/setup-lifecycle/replay" in paths
    assert "/setup-lifecycle" in paths


class FakeQueryService:
    def __init__(self, *, changes_payload: dict | None = None) -> None:
        self.changes_payload = changes_payload or {"items": [], "total": 0}
        self.last_changes_query = None
        self.last_alerts_query = None
        self.last_evaluation_id = None

    def changes(self, _db, query):
        self.last_changes_query = query
        return self.changes_payload

    def alerts(self, _db, query):
        self.last_alerts_query = query
        return {"items": [], "total": 0}

    def evaluation_run(self, _db, evaluation_id: int):
        self.last_evaluation_id = evaluation_id
        return {"id": evaluation_id}
