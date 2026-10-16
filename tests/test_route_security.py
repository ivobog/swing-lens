from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import create_app
from app.routers import ceri_routes
from app.routers import setup_lifecycle_routes as setup_routes
from app.security import (
    ROUTE_CLASS_LOCAL_ADMIN,
    ROUTE_CLASS_PUBLIC_LOCAL,
    RuntimeMutationContextMiddleware,
    http_mutation_route_registry,
    unsafe_route,
)
from app.services.runtime_mutation_authority import MutationCapability
from app.settings import RuntimeMode, Settings


def test_unsafe_route_inventory_requires_classification() -> None:
    app = create_app(Settings(_env_file=None, job_worker_enabled=False))
    registry = http_mutation_route_registry(app)

    assert len(app.routes) == 197
    assert len(registry) == 52
    assert [row for row in registry if not row.classified] == []
    assert _capability_counts(registry) == {
        MutationCapability.NORMAL_ONLY: 48,
        MutationCapability.CERTIFICATION_CONTROL: 0,
        MutationCapability.CERTIFICATION_SESSION_SCOPED: 2,
        MutationCapability.READ_ONLY: 2,
    }


def test_normal_runtime_allows_classified_normal_mutation() -> None:
    app, mutations = _authority_test_app(RuntimeMode.NORMAL)

    response = TestClient(app).post("/normal")

    assert response.status_code == 200
    assert mutations == ["normal"]


def test_certification_denies_unclassified_and_normal_only_before_mutation() -> None:
    app, mutations = _authority_test_app(RuntimeMode.CERTIFICATION)
    client = TestClient(app)

    unclassified = client.post("/unclassified")
    normal_only = client.post("/normal")

    assert unclassified.status_code == 409
    assert unclassified.json()["detail"]["code"] == "UNCLASSIFIED_CERTIFICATION_MUTATION"
    assert normal_only.status_code == 409
    assert normal_only.json()["detail"]["code"] == "CERTIFICATION_MUTATION_FORBIDDEN"
    assert mutations == []


def test_certification_control_requires_current_session_and_approved_operation() -> None:
    app, mutations = _authority_test_app(RuntimeMode.CERTIFICATION)
    client = TestClient(app)

    missing = client.post("/control")
    wrong = client.post(
        "/control", headers={"x-swinglens-certification-session": "wrong-session"}
    )
    allowed = client.post(
        "/control", headers={"x-swinglens-certification-session": "cert-session"}
    )
    forbidden_operation = client.post(
        "/unapproved-control",
        headers={"x-swinglens-certification-session": "cert-session"},
    )

    assert missing.json()["detail"]["code"] == "CERTIFICATION_SESSION_REQUIRED"
    assert wrong.json()["detail"]["code"] == "CERTIFICATION_SESSION_MISMATCH"
    assert allowed.status_code == 200
    assert forbidden_operation.json()["detail"]["code"] == (
        "CERTIFICATION_CONTROL_OPERATION_FORBIDDEN"
    )
    assert mutations == ["control"]


def test_certification_session_scoped_root_creation_requires_current_session() -> None:
    app, mutations = _authority_test_app(RuntimeMode.CERTIFICATION)
    client = TestClient(app)

    missing = client.post("/session-scoped")
    wrong = client.post(
        "/session-scoped",
        headers={"x-swinglens-certification-session": "wrong-session"},
    )
    allowed = client.post(
        "/session-scoped",
        headers={"x-swinglens-certification-session": "cert-session"},
    )

    assert missing.json()["detail"]["code"] == "CERTIFICATION_SESSION_REQUIRED"
    assert wrong.json()["detail"]["code"] == "CERTIFICATION_SESSION_MISMATCH"
    assert allowed.status_code == 200
    assert mutations == ["session-scoped"]


def test_certification_blocks_real_setup_writer_before_dependency_or_database_work() -> None:
    settings = _certification_settings()
    app = create_app(settings)

    response = TestClient(app).post("/api/setup-lifecycle/alerts/777/acknowledge")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CERTIFICATION_MUTATION_FORBIDDEN"


def test_certification_blocks_real_ceri_writer_before_database_work() -> None:
    db = FakeDb()
    request = _admin_request(csrf_token="secure-test-token")
    request.app.state.settings = _certification_settings()
    request.headers["x-swinglens-certification-session"] = "cert-session"

    with pytest.raises(HTTPException) as exc:
        ceri_routes.create_ceri_ingestion_run(
            request=request,
            db=db,  # type: ignore[arg-type]
            payload={"ticker": "MSFT", "dataset": "estimates"},
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "CERTIFICATION_MUTATION_FORBIDDEN"
    assert db.added == []
    assert db.commits == 0


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
            request=_admin_request(csrf_token="secure-test-token"),
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
                    setup_lifecycle_enabled=True,
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


def _certification_settings() -> Settings:
    return Settings(
        _env_file=None,
        runtime_mode=RuntimeMode.CERTIFICATION,
        use_durable_pipeline=True,
        durable_worker_process_enabled=True,
        winner_probability_auto_maturation_enabled=False,
        winner_probability_auto_cohort_refresh_enabled=False,
        market_data_prewarm_enabled=False,
        job_worker_enabled=False,
        runtime_instance_id="cert-session",
    )


def _authority_test_app(mode: RuntimeMode) -> tuple[FastAPI, list[str]]:
    app = FastAPI()
    app.state.settings = (
        _certification_settings()
        if mode is RuntimeMode.CERTIFICATION
        else Settings(_env_file=None, job_worker_enabled=False)
    )
    app.add_middleware(RuntimeMutationContextMiddleware)
    app.state.local_admin_csrf_token = "secure-test-token"
    mutations: list[str] = []

    @app.post("/unclassified")
    def unclassified() -> dict[str, bool]:
        mutations.append("unclassified")
        return {"ok": True}

    @app.post("/normal")
    @unsafe_route(
        ROUTE_CLASS_PUBLIC_LOCAL,
        reason="test normal mutation",
        mutation_capability=MutationCapability.NORMAL_ONLY,
        operation="test.http.normal",
    )
    def normal() -> dict[str, bool]:
        mutations.append("normal")
        return {"ok": True}

    @app.post("/control")
    @unsafe_route(
        ROUTE_CLASS_PUBLIC_LOCAL,
        reason="test approved certification control mutation",
        mutation_capability=MutationCapability.CERTIFICATION_CONTROL,
        operation="worker_heartbeat",
    )
    def control() -> dict[str, bool]:
        mutations.append("control")
        return {"ok": True}

    @app.post("/unapproved-control")
    @unsafe_route(
        ROUTE_CLASS_PUBLIC_LOCAL,
        reason="test unapproved certification control mutation",
        mutation_capability=MutationCapability.CERTIFICATION_CONTROL,
        operation="test.http.unapproved_control",
    )
    def unapproved_control() -> dict[str, bool]:
        mutations.append("unapproved-control")
        return {"ok": True}

    @app.post("/session-scoped")
    @unsafe_route(
        ROUTE_CLASS_PUBLIC_LOCAL,
        reason="test certification root creation",
        mutation_capability=MutationCapability.CERTIFICATION_SESSION_SCOPED,
        operation="test.http.session_scoped",
        certification_root_creation=True,
    )
    def session_scoped() -> dict[str, bool]:
        mutations.append("session-scoped")
        return {"ok": True}

    @app.post("/admin")
    @unsafe_route(
        ROUTE_CLASS_LOCAL_ADMIN,
        reason="test local administrator mutation",
        mutation_capability=MutationCapability.NORMAL_ONLY,
        operation="test.http.admin",
        local_admin_required=True,
        csrf_required=True,
    )
    def admin() -> dict[str, bool]:
        mutations.append("admin")
        return {"ok": True}

    return app, mutations


@pytest.mark.parametrize("headers", [{}, {"x-csrf-token": "wrong"}])
def test_declared_admin_mutation_rejects_missing_or_invalid_csrf_without_mutating(
    headers: dict[str, str],
) -> None:
    app, mutations = _authority_test_app(RuntimeMode.NORMAL)

    response = TestClient(app).post("/admin", headers=headers)

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ADMIN_FORBIDDEN"
    assert mutations == []


def test_declared_admin_mutation_rejects_non_loopback_without_mutating() -> None:
    app, mutations = _authority_test_app(RuntimeMode.NORMAL)

    response = TestClient(app, client=("203.0.113.7", 41000)).post(
        "/admin",
        headers={"x-csrf-token": "secure-test-token"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ADMIN_FORBIDDEN"
    assert mutations == []


def test_declared_admin_mutation_accepts_valid_loopback_csrf() -> None:
    app, mutations = _authority_test_app(RuntimeMode.NORMAL)

    response = TestClient(app).post(
        "/admin",
        headers={"x-csrf-token": "secure-test-token"},
    )

    assert response.status_code == 200
    assert mutations == ["admin"]


def test_target_admin_routes_declare_both_local_and_csrf_policy() -> None:
    app = create_app(Settings(_env_file=None, job_worker_enabled=False))
    records = {
        row.path: row.classification for row in http_mutation_route_registry(app)
    }

    for path in (
        "/api/setup-lifecycle/evaluations",
        "/api/setup-lifecycle/replay",
        "/api/winner-probability/outcomes/process",
        "/api/winner-probability/cohorts/refresh",
    ):
        assert records[path] is not None
        assert records[path].local_admin_required is True
        assert records[path].csrf_required is True


def _capability_counts(registry) -> dict[MutationCapability, int]:
    return {
        capability: sum(
            row.classification is not None
            and row.classification.mutation_capability is capability
            for row in registry
        )
        for capability in MutationCapability
    }
