from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import create_app
from app.models.ceri_tables import CeriProcessingRun
from app.models.tables import BackgroundJob
from app.routers import ceri_provider_routes, ceri_routes
from app.services.ceri.query_service import CeriQueryError
from app.settings import Settings


def test_latest_route_forwards_filters_to_query_service(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_service = FakeQueryService()
    monkeypatch.setattr(ceri_routes, "CeriQueryService", lambda: fake_service)

    payload = ceri_routes.ceri_latest(
        db=object(),  # type: ignore[arg-type]
        opportunity_min=7.5,
        risk_max=3.0,
        confidence="High",
        posture="Positive",
        alignment_flag="technicals",
        has_warnings=False,
        sort="ticker",
        direction="asc",
        limit=25,
        offset=10,
    )

    query = fake_service.last_latest_query
    assert payload["items"] == []
    assert query.filters.opportunity_min == 7.5
    assert query.filters.risk_max == 3.0
    assert query.filters.confidence == "High"
    assert query.filters.alignment_flag == "technicals"
    assert query.filters.has_warnings is False
    assert query.sort == "ticker"
    assert query.direction == "asc"
    assert query.limit == 25
    assert query.offset == 10


def test_query_errors_are_mapped_to_structured_http_errors() -> None:
    with pytest.raises(HTTPException) as exc:
        ceri_routes._query_or_http(  # noqa: SLF001
            lambda: (_ for _ in ()).throw(
                CeriQueryError("INVALID_FILTER", "bad filter", status_code=400)
            )
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == {"code": "INVALID_FILTER", "message": "bad filter"}


def test_history_route_requires_explicit_mode_and_as_of(monkeypatch: pytest.MonkeyPatch) -> None:
    class RejectingService:
        def ticker_history(self, *_args, **_kwargs):
            raise CeriQueryError("INVALID_FILTER", "Historical CERI endpoints require cutoff.")

    monkeypatch.setattr(ceri_routes, "CeriQueryService", lambda: RejectingService())

    with pytest.raises(HTTPException) as exc:
        ceri_routes.ceri_ticker_history(
            ticker="MSFT",
            db=object(),  # type: ignore[arg-type]
            mode=None,
            as_of=None,
        )

    assert exc.value.detail["code"] == "INVALID_FILTER"


def test_export_routes_return_downloadable_csv_and_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_export = SimpleNamespace(
        to_csv=lambda: "ticker,opportunity_score\nMSFT,8.0\n",
        to_json=lambda: json.dumps([{"ticker": "MSFT"}]),
    )
    monkeypatch.setattr(
        ceri_routes,
        "CeriExportService",
        lambda: SimpleNamespace(current_view=lambda *_args, **_kwargs: fake_export),
    )

    csv_response = ceri_routes.export_ceri_csv(db=object())  # type: ignore[arg-type]
    json_response = ceri_routes.export_ceri_json(db=object())  # type: ignore[arg-type]

    assert csv_response.media_type == "text/csv"
    assert "MSFT" in csv_response.body.decode()
    assert json.loads(json_response.body) == [{"ticker": "MSFT"}]


def test_provider_health_route_exposes_provider_capabilities() -> None:
    payload = ceri_provider_routes.ceri_provider_health()

    assert payload["total"] >= 1
    assert payload["items"][0]["provider"] == "manual"
    assert "health" in payload["items"][0]["capabilities"]


def test_create_app_registers_phase_8_routes() -> None:
    app = create_app(
        Settings(_env_file=None, job_worker_enabled=False, ceri_enabled=True, ceri_ui_enabled=True)
    )
    ceri_paths = {route.path for route in ceri_routes.router.routes}
    provider_paths = {route.path for route in ceri_provider_routes.router.routes}

    assert app.title == "SwingLens"
    assert "/api/ceri/latest" in ceri_paths
    assert "/api/ceri/ticker/{ticker}/history" in ceri_paths
    assert "/api/ceri/providers/health" in provider_paths
    assert "/api/ceri/backfills" in ceri_paths
    assert "/ceri/export.csv" in ceri_paths


def test_ceri_latest_endpoint_round_trips_through_app(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ceri_routes, "CeriQueryService", lambda: FakeQueryService())
    app = create_app(
        Settings(_env_file=None, job_worker_enabled=False, ceri_enabled=True, ceri_ui_enabled=True)
    )
    app.dependency_overrides[get_db] = lambda: object()

    response = TestClient(app).get("/api/ceri/latest?limit=5")

    assert response.status_code == 200
    assert response.json()["limit"] == 5


class FakeQueryService:
    def __init__(self) -> None:
        self.last_latest_query = None

    def latest(self, _db, query):
        self.last_latest_query = query
        return {"items": [], "total": 0, "limit": query.limit, "offset": query.offset}


class FakeDb:
    def __init__(self, *, processing_runs=None, jobs=None) -> None:
        self.processing_runs = list(processing_runs or [])
        self.jobs = list(jobs or [])
        self.added = []
        self.next_id = 1
        self.commits = 0

    def add(self, row) -> None:
        self.added.append(row)
        if isinstance(row, BackgroundJob):
            self.jobs.append(row)

    def flush(self) -> None:
        for row in self.added + self.jobs + self.processing_runs:
            if getattr(row, "id", None) is None:
                row.id = self.next_id
                self.next_id += 1

    def commit(self) -> None:
        self.commits += 1

    def scalars(self, statement):
        model = statement.column_descriptions[0]["entity"]
        if model is BackgroundJob:
            return FakeScalarResult(self.jobs)
        if model is CeriProcessingRun:
            return FakeScalarResult(self.processing_runs)
        return FakeScalarResult([])


class FakeScalarResult:
    def __init__(self, rows) -> None:
        self.rows = rows

    def all(self):
        return self.rows


def _admin_request(*, csrf: bool = True):
    csrf_token = "secure-test-token"
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                local_admin_csrf_token=csrf_token,
                settings=Settings(
                    _env_file=None,
                    job_worker_enabled=False,
                    ceri_admin_enabled=True,
                ),
            ),
        ),
        client=SimpleNamespace(host="testclient"),
        headers={"x-csrf-token": csrf_token} if csrf else {},
        query_params={},
    )


def _processing_run(request_key: str) -> CeriProcessingRun:
    return CeriProcessingRun(
        id=9,
        job_type="CERI_BACKFILL",
        status="RUNNING",
        deterministic_request_key=request_key,
        started_at=datetime(2026, 8, 2, tzinfo=UTC),
    )
