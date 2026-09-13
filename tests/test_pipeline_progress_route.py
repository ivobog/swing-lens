from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from app.db import get_db
from app.routers import run_routes
from app.services.pipeline_service import PipelineStatusDto


def test_active_pipeline_progress_refresh_handles_nullable_diagnostics(monkeypatch) -> None:
    run = SimpleNamespace(id=158, filename="run.csv")
    status = PipelineStatusDto(
        pipeline_run_id=148,
        upload_run_id=158,
        status="FETCHING_MARKET_DATA",
        current_step="FETCHING_MARKET_DATA",
        requested_by="local-ui",
        started_at=None,
        completed_at=None,
        created_at=None,
        message="Full pipeline is running.",
        error_message=None,
        background_job_id=None,
        steps=[],
        result_json={
            "blocked_diagnostics": None,
            "sec_repair": None,
            "blocked_reason": None,
        },
    )
    monkeypatch.setattr(run_routes, "_load_run", lambda _db, _run_id: run)
    monkeypatch.setattr(
        run_routes,
        "_require_pipeline_for_run",
        lambda _db, _pipeline_id, _run_id: status,
    )

    app = FastAPI()
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    app.include_router(run_routes.router)
    app.dependency_overrides[get_db] = lambda: object()

    response = TestClient(app, raise_server_exceptions=False).get("/runs/158/pipeline/148")

    assert response.status_code == 200
    assert "Pipeline 148" in response.text
    assert "Fetching Market Data" in response.text
    assert "Prerequisite Required" in response.text
