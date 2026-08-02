from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.ceri_tables import CeriAlertEvent, CeriProcessingRun
from app.models.tables import BackgroundJob
from app.routers import ceri_routes
from app.services.ceri.backfill_service import CeriBackfillRequest, CeriBackfillService
from app.services.ceri.job_handlers import CERI_BACKFILL, CERI_PROVIDER_INGEST
from app.settings import Settings


def test_admin_routes_require_csrf_token() -> None:
    with pytest.raises(HTTPException) as exc:
        ceri_routes.create_ceri_ingestion_run(
            request=_admin_request(csrf=False),
            db=FakeDb(),  # type: ignore[arg-type]
            payload={"ticker": "MSFT", "dataset": "estimates"},
        )

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "ADMIN_FORBIDDEN"


def test_ingestion_admin_route_enqueues_idempotent_job() -> None:
    db = FakeDb()
    request = _admin_request()
    payload = {"ticker": "MSFT", "dataset": "estimates", "request_key": "ingest-msft"}

    first = ceri_routes.create_ceri_ingestion_run(
        request=request,
        db=db,  # type: ignore[arg-type]
        payload=payload,
    )
    second = ceri_routes.create_ceri_ingestion_run(
        request=request,
        db=db,  # type: ignore[arg-type]
        payload=payload,
    )

    assert first.status_code == 202
    assert json.loads(second.body)["coalesced"] is True
    assert db.jobs[0].job_type == CERI_PROVIDER_INGEST
    assert len(db.jobs) == 1


def test_backfill_route_blocks_matching_active_processing_run() -> None:
    request_payload = {"provider": "manual", "dataset": "estimates", "ticker": "MSFT"}
    request_key = CeriBackfillService().request_key(
        CeriBackfillRequest(provider="manual", dataset="estimates", ticker="MSFT")
    )
    db = FakeDb(processing_runs=[_processing_run(request_key)])

    with pytest.raises(HTTPException) as exc:
        ceri_routes.create_ceri_backfill(
            request=_admin_request(),
            db=db,  # type: ignore[arg-type]
            payload=request_payload,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "BACKFILL_ALREADY_ACTIVE"


def test_backfill_route_enqueues_when_no_active_match() -> None:
    db = FakeDb()

    response = ceri_routes.create_ceri_backfill(
        request=_admin_request(),
        db=db,  # type: ignore[arg-type]
        payload={"provider": "manual", "dataset": "estimates", "ticker": "MSFT"},
    )

    assert response.status_code == 202
    assert db.jobs[0].job_type == CERI_BACKFILL
    assert db.commits == 1


def test_alert_state_routes_do_not_mutate_change_events() -> None:
    alert = CeriAlertEvent(
        id=5,
        event_key="event",
        ticker="MSFT",
        severity="RISK",
        status="UNREAD",
    )
    db = FakeDb(alerts=[alert])

    payload = ceri_routes.acknowledge_ceri_alert(
        alert_id=5,
        request=_admin_request(),
        db=db,  # type: ignore[arg-type]
    )

    assert payload == {"id": 5, "status": "ACKNOWLEDGED"}
    assert alert.status == "ACKNOWLEDGED"
    assert db.commits == 1


def test_purge_execute_requires_confirmation_token() -> None:
    with pytest.raises(HTTPException) as exc:
        ceri_routes.execute_ceri_purge(
            request=_admin_request(),
            db=FakeDb(),  # type: ignore[arg-type]
            payload={"provider": "manual", "license_scope": "estimates"},
        )

    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "PURGE_CONFIRMATION_REQUIRED"


def _admin_request(*, csrf: bool = True, enabled: bool = True):
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=Settings(
                    _env_file=None,
                    job_worker_enabled=False,
                    ceri_admin_enabled=enabled,
                )
            )
        ),
        client=SimpleNamespace(host="testclient"),
        headers={"x-csrf-token": "ceri-local-admin"} if csrf else {},
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


class FakeDb:
    def __init__(self, *, processing_runs=None, jobs=None, alerts=None) -> None:
        self.processing_runs = list(processing_runs or [])
        self.jobs = list(jobs or [])
        self.alerts = list(alerts or [])
        self.added = []
        self.next_id = 1
        self.commits = 0

    def add(self, row) -> None:
        self.added.append(row)
        if isinstance(row, BackgroundJob):
            self.jobs.append(row)

    def flush(self) -> None:
        for row in self.added + self.jobs + self.processing_runs + self.alerts:
            if getattr(row, "id", None) is None:
                row.id = self.next_id
                self.next_id += 1

    def commit(self) -> None:
        self.commits += 1

    def get(self, model, row_id):
        if model is CeriAlertEvent:
            return next((alert for alert in self.alerts if alert.id == row_id), None)
        if model is BackgroundJob:
            return next((job for job in self.jobs if job.id == row_id), None)
        return None

    def scalar(self, _statement):
        return None

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
