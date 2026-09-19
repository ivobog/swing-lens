from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.tables import BackgroundJob, FundamentalScore, RawCompanyRow
from app.routers import run_routes
from app.services.fundamental_score_service import recalculate_run_fundamentals
from app.services.pipeline_service import (
    PipelineStatusDto,
    PipelineStepStatusDto,
)


def _ready_ib_status() -> SimpleNamespace:
    return SimpleNamespace(
        status="READY",
        message="ready",
        to_dict=lambda: {
            "status": "READY",
            "api_connected": True,
            "checked_at": "2026-08-16T10:00:00+00:00",
            "host": "127.0.0.1",
            "port": 4002,
        },
    )


def _offline_ib_status() -> SimpleNamespace:
    return SimpleNamespace(
        status="NOT_RUNNING_OR_UNREACHABLE",
        message="offline",
        to_dict=lambda: {
            "status": "NOT_RUNNING_OR_UNREACHABLE",
            "api_connected": False,
            "checked_at": "2026-08-16T10:00:00+00:00",
            "host": "127.0.0.1",
            "port": 4002,
        },
    )


def test_recalculate_run_fundamentals_replaces_scores_from_stored_raw_rows() -> None:
    raw_row = RawCompanyRow(
        run_id=7,
        row_number=1,
        ticker="MSFT",
        company_name="Microsoft",
        sector="Technology",
        raw_json={
            "Symbol": "MSFT",
            "Description": "Microsoft",
            "Sector": "Technology",
            "Market capitalization": "3000000000000",
            "Free cash flow TTM": "70000000000",
            "Net income TTM": "80000000000",
        },
    )
    original_raw_json = deepcopy(raw_row.raw_json)
    db = FundamentalFakeDb([raw_row])

    scores = recalculate_run_fundamentals(db, run_id=7)

    assert db.deleted_fundamentals is True
    assert db.flushes == 1
    assert len(scores) == 1
    assert isinstance(db.added[0], FundamentalScore)
    assert db.added[0].ticker == "MSFT"
    assert db.added[0].scoring_model_version == "fundamentals_v2.1"
    assert raw_row.raw_json == original_raw_json


@pytest.mark.parametrize(
    "operation",
    [
        run_routes.refresh_combined_results_action,
        run_routes.recalculate_fundamentals_action,
        run_routes.refresh_technicals_action,
    ],
)
def test_unbound_standalone_routes_are_intentionally_retired(operation) -> None:
    db = RouteFakeDb()
    with pytest.raises(HTTPException) as error:
        operation(run_id=7, db=db)
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "STANDALONE_MUTATION_RETIRED"
    assert db.commits == 0


def test_full_pipeline_retires_reduced_path_before_work(monkeypatch) -> None:
    monkeypatch.setattr(
        run_routes, "get_settings", lambda: SimpleNamespace(use_durable_pipeline=False)
    )
    monkeypatch.setattr(
        run_routes, "check_status", lambda **_: pytest.fail("retired path probed IB")
    )
    db = RouteFakeDb()
    with pytest.raises(HTTPException) as error:
        run_routes.run_full_pipeline_action(run_id=7, db=db)
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "REDUCED_PIPELINE_RETIRED"
    assert db.commits == 0


def test_full_pipeline_uses_durable_pipeline_when_feature_flag_enabled(monkeypatch) -> None:
    calls = {}
    monkeypatch.setattr(
        run_routes,
        "get_settings",
        lambda: SimpleNamespace(use_durable_pipeline=True),
    )
    monkeypatch.setattr(run_routes, "check_status", lambda **_kwargs: _ready_ib_status())
    monkeypatch.setattr(run_routes, "has_live_worker_for_job", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        run_routes,
        "start_pipeline",
        lambda _db, run_id, **kwargs: (
            calls.update({"run_id": run_id, **kwargs}) or SimpleNamespace(id=99)
        ),
    )
    db = RouteFakeDb()

    response = run_routes.run_full_pipeline_action(run_id=7, db=db)

    assert calls["run_id"] == 7
    assert calls["market_data_policy"] == "REQUIRE_IB"
    assert db.commits == 1
    assert response.headers["location"] == "/runs/7/pipeline/99"


def test_full_pipeline_default_policy_rejects_unavailable_ib_before_enqueue(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        run_routes,
        "get_settings",
        lambda: SimpleNamespace(use_durable_pipeline=True),
    )
    monkeypatch.setattr(run_routes, "check_status", lambda **_kwargs: _offline_ib_status())
    monkeypatch.setattr(run_routes, "start_pipeline", lambda *args, **kwargs: calls.append(kwargs))

    with pytest.raises(HTTPException) as exc:
        run_routes.run_full_pipeline_action(run_id=7, db=RouteFakeDb())

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "IB_GATEWAY_UNAVAILABLE"
    assert calls == []


def test_full_pipeline_explicit_cache_fallback_enqueues_degraded_policy(monkeypatch) -> None:
    calls = {}
    monkeypatch.setattr(
        run_routes,
        "get_settings",
        lambda: SimpleNamespace(use_durable_pipeline=True),
    )
    monkeypatch.setattr(run_routes, "check_status", lambda **_kwargs: _offline_ib_status())
    monkeypatch.setattr(run_routes, "has_live_worker_for_job", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        run_routes,
        "start_pipeline",
        lambda _db, run_id, **kwargs: (
            calls.update({"run_id": run_id, **kwargs}) or SimpleNamespace(id=100)
        ),
    )
    db = RouteFakeDb()

    response = run_routes.run_full_pipeline_action(
        run_id=7,
        db=db,
        market_data_policy="ALLOW_CACHE_FALLBACK",
    )

    assert response.headers["location"] == "/runs/7/pipeline/100"
    assert calls["market_data_policy"] == "ALLOW_CACHE_FALLBACK"
    assert calls["ib_preflight_status"]["api_connected"] is False


def test_full_pipeline_rejects_enqueue_when_durable_worker_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        run_routes,
        "get_settings",
        lambda: SimpleNamespace(
            use_durable_pipeline=True,
            job_worker_heartbeat_timeout_seconds=30,
        ),
    )
    monkeypatch.setattr(run_routes, "check_status", lambda **_kwargs: _ready_ib_status())
    monkeypatch.setattr(run_routes, "has_live_worker_for_job", lambda *_args, **_kwargs: False)
    calls = []
    monkeypatch.setattr(run_routes, "start_pipeline", lambda *args, **kwargs: calls.append(kwargs))

    with pytest.raises(HTTPException) as raised:
        run_routes.run_full_pipeline_action(run_id=7, db=RouteFakeDb())

    assert raised.value.status_code == 503
    assert raised.value.detail["code"] == "DURABLE_WORKER_UNAVAILABLE"
    assert calls == []


def test_pipeline_status_route_returns_progress_payload(monkeypatch) -> None:
    status = PipelineStatusDto(
        pipeline_run_id=99,
        upload_run_id=7,
        status="RUNNING",
        current_step="FETCHING_MARKET_DATA",
        requested_by=None,
        started_at=None,
        completed_at=None,
        created_at=None,
        message="working",
        error_message=None,
        background_job_id=42,
        steps=[
            PipelineStepStatusDto(
                step_name="VALIDATING_RUN",
                step_order=1,
                status="COMPLETED",
                started_at=None,
                completed_at=None,
                message=None,
                error_message=None,
                retry_count=0,
            ),
            PipelineStepStatusDto(
                step_name="FETCHING_MARKET_DATA",
                step_order=2,
                status="RUNNING",
                started_at=None,
                completed_at=None,
                message=None,
                error_message=None,
                retry_count=0,
            ),
        ],
    )
    monkeypatch.setattr(run_routes, "get_pipeline_status", lambda _db, _pipeline_id: status)
    db = RouteFakeDb(job=BackgroundJob(id=42, job_type="FULL_PIPELINE", status="RUNNING"))

    payload = run_routes.run_pipeline_status(run_id=7, pipeline_id=99, db=db)

    assert payload["pipeline_run_id"] == 99
    assert payload["status"] == "RUNNING"
    assert payload["current_step_label"] == "Fetching Market Data"
    assert payload["job_status"] == "RUNNING"
    assert payload["completed_steps"] == 1
    assert payload["total_steps"] == 2
    assert payload["percentage"] == 50.0


def test_cancel_pipeline_route_requests_cancel_and_redirects(monkeypatch) -> None:
    status = PipelineStatusDto(
        pipeline_run_id=99,
        upload_run_id=7,
        status="RUNNING",
        current_step="FETCHING_MARKET_DATA",
        requested_by=None,
        started_at=None,
        completed_at=None,
        created_at=None,
        message=None,
        error_message=None,
        background_job_id=42,
        steps=[],
    )
    calls = {}
    monkeypatch.setattr(run_routes, "get_pipeline_status", lambda _db, _pipeline_id: status)
    monkeypatch.setattr(
        run_routes,
        "cancel_pipeline",
        lambda _db, pipeline_id: calls.setdefault("pipeline_id", pipeline_id),
    )
    db = RouteFakeDb()

    response = run_routes.cancel_run_pipeline_action(run_id=7, pipeline_id=99, db=db)

    assert calls["pipeline_id"] == 99
    assert db.commits == 1
    assert response.headers["location"] == "/runs/7/pipeline/99"


def test_resume_pipeline_route_queues_checkpoint_and_redirects(monkeypatch) -> None:
    status = PipelineStatusDto(
        pipeline_run_id=99,
        upload_run_id=7,
        status="BLOCKED",
        current_step="CERI_PROVIDER_INGEST",
        requested_by=None,
        started_at=None,
        completed_at=None,
        created_at=None,
        message="blocked",
        error_message=None,
        background_job_id=42,
        steps=[],
    )
    calls = {}
    monkeypatch.setattr(run_routes, "get_pipeline_status", lambda _db, _pipeline_id: status)
    monkeypatch.setattr(
        run_routes,
        "resume_pipeline",
        lambda _db, pipeline_id: calls.setdefault("pipeline_id", pipeline_id),
    )
    db = RouteFakeDb()

    response = run_routes.resume_run_pipeline_action(run_id=7, pipeline_id=99, db=db)

    assert calls["pipeline_id"] == 99
    assert db.commits == 1
    assert response.headers["location"] == "/runs/7/pipeline/99"


class FundamentalFakeDb:
    def __init__(self, raw_rows: list[RawCompanyRow]) -> None:
        self.raw_rows = raw_rows
        self.added = []
        self.deleted_fundamentals = False
        self.flushes = 0

    def scalars(self, statement):
        if "raw_company_rows" in str(statement):
            return FakeScalarResult(self.raw_rows)
        return FakeScalarResult([])

    def execute(self, statement):
        if "DELETE FROM fundamental_scores" in str(statement):
            self.deleted_fundamentals = True

    def add_all(self, rows) -> None:
        self.added.extend(rows)

    def flush(self) -> None:
        self.flushes += 1


class RouteFakeDb:
    def __init__(self, job: BackgroundJob | None = None) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.job = job

    def scalar(self, statement):
        if "upload_runs" in str(statement):
            return 7
        return None

    def get(self, model, row_id):
        if model is BackgroundJob:
            return self.job
        return None

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class FakeScalarResult:
    def __init__(self, rows) -> None:
        self.rows = rows

    def all(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)
