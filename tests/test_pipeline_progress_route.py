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


def test_pipeline_progress_shows_prerequisite_wait_without_inventing_processing(monkeypatch):
    from datetime import UTC, datetime, timedelta

    from app.models.tables import BackgroundJob

    now = datetime.now(UTC)
    child = BackgroundJob(
        id=1902, job_type="CERI_NORMALIZE_BATCH", status="QUEUED",
        created_at=now - timedelta(minutes=10), payload_json={}, progress_sequence=0,
        operational_metadata_json={"dependency_wait": {
            "deferral_count": 12, "progress_sequence": 0,
            "unchanged_since": (now - timedelta(minutes=10)).isoformat(),
        }},
    )
    status = PipelineStatusDto(
        pipeline_run_id=59, upload_run_id=57, status="WAITING_FOR_CERI_COMPLETION",
        current_step="CERI_PROVIDER_INGEST", requested_by="local-ui", started_at=now,
        completed_at=None, created_at=now, message="Waiting for CERI.", error_message=None,
        background_job_id=None, steps=[], result_json={},
    )
    monkeypatch.setattr(
        run_routes, "_load_run", lambda *_: SimpleNamespace(id=57, filename="run.csv"),
    )
    monkeypatch.setattr(run_routes, "_require_pipeline_for_run", lambda *_: status)
    monkeypatch.setattr(run_routes, "_pipeline_observability_job", lambda *_: child)
    monkeypatch.setattr(
        "app.services.ceri.batched_claims.ceri_dependency_wait",
        lambda *_: {
            "prerequisite": "CERI_PROVIDER_INGEST_BATCH", "label": "provider ingestion",
            "unfinished_batches": 38, "waiting_since": child.created_at,
        },
    )
    app = FastAPI()
    app.include_router(run_routes.router)
    app.dependency_overrides[get_db] = lambda: object()
    client = TestClient(app)
    payload = client.get("/runs/57/pipeline/59/status").json()
    assert payload["last_progress_at"] is None
    assert payload["progress_sequence"] == 0
    assert payload["dependency_wait"]["unfinished_batches"] == 38
    assert payload["dependency_wait"]["age_seconds"] >= 600
    assert payload["dependency_deferral_count"] == 12
    assert payload["dependency_deferral_warning"] is True
    # The page route also renders the same status to cover the non-JS view.
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    page = client.get("/runs/57/pipeline/59")
    assert page.status_code == 200
    assert "provider ingestion" in page.text
    assert 'data-pipeline-prerequisite-unfinished>38<' in page.text
