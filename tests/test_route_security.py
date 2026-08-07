from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import create_app
from app.routers import ceri_routes
from app.routers import setup_lifecycle_routes as setup_routes
from app.security import UNSAFE_HTTP_METHODS
from app.settings import Settings


def test_unsafe_route_inventory_requires_classification() -> None:
    app = create_app(Settings(_env_file=None, job_worker_enabled=False))
    unclassified = []

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not UNSAFE_HTTP_METHODS.intersection(route.methods):
            continue
        classification = getattr(route.endpoint, "swinglens_unsafe_route", None)
        if classification is None:
            unclassified.append(f"{','.join(sorted(route.methods))} {route.path}")

    assert unclassified == []


def test_ceri_admin_rejects_static_csrf_token() -> None:
    request = _admin_request(csrf_token="ceri-local-admin")

    with pytest.raises(HTTPException) as exc:
        ceri_routes.create_ceri_ingestion_run(
            request=request,
            db=FakeDb(),  # type: ignore[arg-type]
            payload={"ticker": "MSFT", "dataset": "estimates"},
        )

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "ADMIN_FORBIDDEN"


def test_ceri_admin_rejects_query_string_csrf_token() -> None:
    request = _admin_request(csrf_token=None, query_csrf_token="secure-test-token")

    with pytest.raises(HTTPException) as exc:
        ceri_routes.create_ceri_ingestion_run(
            request=request,
            db=FakeDb(),  # type: ignore[arg-type]
            payload={"ticker": "MSFT", "dataset": "estimates"},
        )

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "ADMIN_FORBIDDEN"


def test_ceri_admin_accepts_current_header_csrf_token() -> None:
    db = FakeDb()

    response = ceri_routes.create_ceri_ingestion_run(
        request=_admin_request(csrf_token="secure-test-token"),
        db=db,  # type: ignore[arg-type]
        payload={"ticker": "MSFT", "dataset": "estimates"},
    )

    assert response.status_code == 202
    assert db.commits == 1


def test_persisted_setup_lifecycle_replay_requires_confirmation_reason_and_requester() -> None:
    with pytest.raises(HTTPException) as exc:
        setup_routes.replay_setup_lifecycle(
            request=SimpleNamespace(client=SimpleNamespace(host="testclient")),
            db=FakeDb(),  # type: ignore[arg-type]
            persist=True,
        )

    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "INVALID_CONFIGURATION"


def test_host_spoof_is_rejected_by_trusted_host_middleware() -> None:
    app = create_app(Settings(_env_file=None, job_worker_enabled=False))
    response = TestClient(app).get("/health", headers={"host": "evil.example"})

    assert response.status_code == 400


def test_public_debug_bind_is_rejected() -> None:
    with pytest.raises(ValidationError, match="debug mode is not allowed"):
        Settings(
            _env_file=None,
            app_host="0.0.0.0",
            debug=True,
            allow_public_bind=True,
        )


def test_public_bind_requires_explicit_override() -> None:
    with pytest.raises(ValidationError, match="public bind requires"):
        Settings(_env_file=None, app_host="0.0.0.0", debug=False)


def _admin_request(*, csrf_token: str | None, query_csrf_token: str | None = None):
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                local_admin_csrf_token="secure-test-token",
                settings=Settings(
                    _env_file=None,
                    job_worker_enabled=False,
                    ceri_enabled=True,
                    ceri_admin_enabled=True,
                    ceri_provider_ingest_enabled=True,
                ),
            )
        ),
        client=SimpleNamespace(host="testclient"),
        headers={"x-csrf-token": csrf_token} if csrf_token is not None else {},
        query_params={"csrf_token": query_csrf_token} if query_csrf_token is not None else {},
    )


class FakeDb:
    def __init__(self) -> None:
        self.added = []
        self.jobs = []
        self.commits = 0
        self.next_id = 1

    def add(self, row) -> None:
        self.added.append(row)
        self.jobs.append(row)

    def flush(self) -> None:
        for row in self.added:
            if getattr(row, "id", None) is None:
                row.id = self.next_id
                self.next_id += 1

    def commit(self) -> None:
        self.commits += 1

    def scalar(self, _statement):
        return None

    def scalars(self, _statement):
        return FakeScalarResult([])


class FakeScalarResult:
    def __init__(self, rows) -> None:
        self.rows = rows

    def all(self):
        return self.rows
