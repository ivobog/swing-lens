from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import create_app
from app.routers import setup_lifecycle_routes as routes
from app.services.setup_lifecycle.query_service import (
    SetupLifecycleQueryError,
    SetupLifecycleViewScope,
)
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
        sector_rank_change_min=-2,
        sector_rank_change_max=4,
        velocity_window=5,
        velocity_min=0.1,
        velocity_max=2,
        market_regime="RISK_ON",
        blocker="MARKET_GATE_BLOCKED",
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
    assert query.filters.velocity_window == 5
    assert query.filters.sector_rank_change_max == 4
    assert query.filters.blocker == "MARKET_GATE_BLOCKED"
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


def test_alerts_route_forwards_semantic_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_service = FakeQueryService()
    monkeypatch.setattr(routes, "SetupLifecycleQueryService", lambda: fake_service)

    routes.setup_lifecycle_alerts(
        db=object(),  # type: ignore[arg-type]
        as_of=date(2026, 8, 1),
        alert_type="SCORE_ACCELERATION",
        source_type="SIGNAL_CHANGE_EVENT",
        date_from=date(2026, 7, 1),
        date_to=date(2026, 8, 1),
        actionability="ACTIONABLE",
        blocker="EARNINGS_BLOCKED",
    )

    query = fake_service.last_alerts_query
    assert query.filters.as_of_date == date(2026, 8, 1)
    assert query.filters.alert_type == "SCORE_ACCELERATION"
    assert query.filters.source_type == "SIGNAL_CHANGE_EVENT"
    assert query.filters.date_from == date(2026, 7, 1)
    assert query.filters.actionability == "ACTIONABLE"
    assert query.filters.blocker == "EARNINGS_BLOCKED"


def test_export_page_collection_follows_all_cursors() -> None:
    pages = {
        "1": {"items": [{"id": 2}], "total": 3, "next_cursor": "2"},
        "2": {"items": [{"id": 3}], "total": 3, "next_cursor": None},
    }
    first = {"items": [{"id": 1}], "total": 3, "next_cursor": "1"}

    payload = routes._collect_export_pages(  # noqa: SLF001
        first,
        lambda cursor: pages[cursor],
        resource="test rows",
    )

    assert [item["id"] for item in payload["items"]] == [1, 2, 3]
    assert payload["next_cursor"] is None


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
    assert "id,source_type,lifecycle_event_id" in csv_response.body.decode()
    assert "technical_score_delta" in csv_response.body.decode()
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


def test_market_changes_page_renders_full_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        routes,
        "SetupLifecycleQueryService",
        lambda: FakeQueryService(changes_payload=_changes_payload()),
    )
    app = create_app(Settings(_env_file=None, job_worker_enabled=False))
    app.dependency_overrides[get_db] = lambda: object()

    response = TestClient(app).get("/setup-lifecycle?quick_filter=newly-triggered")

    assert response.status_code == 200
    assert "Market Changes" in response.text
    assert "Newly Triggered" in response.text
    assert "MSFT" in response.text
    assert "Stale-system warning active" in response.text
    assert "Expand" in response.text


def test_global_changes_page_does_not_inject_latest_snapshot_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_service = FakeQueryService(changes_payload=_changes_payload())
    monkeypatch.setattr(routes, "SetupLifecycleQueryService", lambda: fake_service)
    app = create_app(Settings(_env_file=None, job_worker_enabled=False))
    app.dependency_overrides[get_db] = lambda: object()

    response = TestClient(app).get(
        "/setup-lifecycle?date_from=2026-07-01&date_to=2026-08-10"
    )

    assert response.status_code == 200
    query = fake_service.last_changes_query
    assert query.view_scope == SetupLifecycleViewScope.CURRENT_MARKET
    assert query.filters.as_of_date is None
    assert query.filters.date_from == date(2026, 7, 1)
    assert query.filters.date_to == date(2026, 8, 10)
    assert 'name="as_of" type="date" value=""' in response.text


def test_run_changes_page_forwards_filters_and_keeps_run_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {**_changes_payload(), "next_cursor": "next-token"}
    fake_service = FakeQueryService(changes_payload=payload)
    monkeypatch.setattr(routes, "SetupLifecycleQueryService", lambda: fake_service)
    monkeypatch.setattr(routes, "_require_run", lambda _db, _run_id: None)
    app = create_app(Settings(_env_file=None, job_worker_enabled=False))
    app.dependency_overrides[get_db] = lambda: object()

    response = TestClient(app).get(
        "/runs/101/setup-lifecycle?run_id=999&ticker=fix&sort=score"
        "&direction=asc&limit=1&date_from=2026-08-01&date_to=2026-08-10"
    )

    assert response.status_code == 200
    query = fake_service.last_changes_query
    assert query.view_scope == SetupLifecycleViewScope.HISTORICAL_RUN
    assert query.filters.run_id == 101
    assert query.filters.ticker == "fix"
    assert query.filters.date_from == date(2026, 8, 1)
    assert query.filters.date_to == date(2026, 8, 10)
    assert query.sort == "score"
    assert query.direction == "asc"
    assert query.limit == 1
    assert "/runs/101/setup-lifecycle?" in response.text
    assert "/runs/101/setup-lifecycle?quick_filter=newly-ready" in response.text
    assert 'href="/runs/101/setup-lifecycle">Clear</a>' in response.text
    assert "run_id=101" in response.text
    assert "view_scope=HISTORICAL_RUN" in response.text
    assert "run_id=999" not in response.text
    assert "Historical evidence" in response.text


def test_quick_filters_use_event_semantics_and_real_bounds() -> None:
    assert routes._quick_filter_values("newly-ready") == {"transition": "TO_READY"}
    assert routes._quick_filter_values("newly-triggered") == {
        "transition": "TO_TRIGGERED"
    }


@pytest.mark.parametrize(
    ("scope", "kwargs", "expected_job", "expected_safety"),
    [
        (
            "RUN",
            {"run_id": 7},
            routes.SETUP_LIFECYCLE_EVALUATE_RUN,
            "PERSIST_DERIVED_CURRENT_VERSION",
        ),
        (
            "TICKER",
            {"ticker": "msft"},
            routes.SETUP_LIFECYCLE_REPLAY,
            "READ_ONLY_DRY_RUN",
        ),
        (
            "DATE_RANGE",
            {"date_from": date(2026, 7, 1), "date_to": date(2026, 8, 1)},
            routes.SETUP_LIFECYCLE_REPLAY,
            "READ_ONLY_DRY_RUN",
        ),
        (
            "ALL_ELIGIBLE",
            {},
            routes.SETUP_LIFECYCLE_REPLAY,
            "READ_ONLY_DRY_RUN",
        ),
        (
            "SINGLE_TICKER_RETRY",
            {"ticker": "msft"},
            routes.SETUP_LIFECYCLE_REPAIR_TICKER,
            "TARGETED_IDEMPOTENT_REPAIR",
        ),
    ],
)
def test_operator_evaluation_scopes_are_explicit_and_versionable(
    scope: str,
    kwargs: dict,
    expected_job: str,
    expected_safety: str,
) -> None:
    job_type, payload, request_key, safety = routes._evaluation_scope_job(  # noqa: SLF001
        scope=scope,
        requester="operator",
        reason="closure test",
        run_id=kwargs.get("run_id"),
        ticker=kwargs.get("ticker"),
        date_from=kwargs.get("date_from"),
        date_to=kwargs.get("date_to"),
        as_of_date=None,
    )

    assert job_type == expected_job
    assert safety == expected_safety
    assert request_key.startswith("setup-lifecycle:")
    assert payload["requester"] == "operator"


def test_operator_scope_rejects_unsafe_ambiguity() -> None:
    with pytest.raises(HTTPException, match="DATE_RANGE"):
        routes._evaluation_scope_job(  # noqa: SLF001
            scope="DATE_RANGE",
            requester="operator",
            reason="closure test",
            run_id=None,
            ticker=None,
            date_from=None,
            date_to=None,
            as_of_date=None,
        )
    assert routes._quick_filter_values("improving-fast") == {
        "sort": "velocity",
        "velocity_min": 0.5,
    }
    assert routes._quick_filter_values("low-confidence") == {"confidence_max": 69}


def test_ticker_lifecycle_page_renders_timeline_and_source_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routes, "SetupLifecycleQueryService", lambda: FakeQueryService())
    app = create_app(Settings(_env_file=None, job_worker_enabled=False))
    app.dependency_overrides[get_db] = lambda: object()

    response = TestClient(app).get("/setup-lifecycle/ticker/MSFT")

    assert response.status_code == 200
    assert "MSFT Lifecycle" in response.text
    assert "Primary Episode" in response.text
    assert "/runs/7/sector-rotation" in response.text
    assert "Timeline" in response.text


def test_episode_page_labels_superseded_records(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "SetupLifecycleQueryService", lambda: FakeQueryService())
    app = create_app(Settings(_env_file=None, job_worker_enabled=False))
    app.dependency_overrides[get_db] = lambda: object()

    response = TestClient(app).get("/setup-lifecycle/episodes/12")

    assert response.status_code == 200
    assert "Setup Episode 12" in response.text
    assert "Superseded" in response.text
    assert "Terminal FAILED_RULE" in response.text


def test_alert_center_renders_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "SetupLifecycleQueryService", lambda: FakeQueryService())
    app = create_app(Settings(_env_file=None, job_worker_enabled=False))
    app.dependency_overrides[get_db] = lambda: object()

    response = TestClient(app).get("/setup-lifecycle/alerts?status=UNREAD")

    assert response.status_code == 200
    assert "Alert Center" in response.text
    assert "Alert Type" in response.text
    assert "NEW_TRIGGER" in response.text
    assert "NOTABLE" in response.text
    assert "WARNING" not in response.text
    assert "LIFECYCLE_EVENT" in response.text
    assert "Acknowledge" in response.text
    assert "data-slse-alert-action" in response.text


def test_operations_page_renders_replay_form(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "SetupLifecycleQueryService", lambda: FakeQueryService())
    app = create_app(Settings(_env_file=None, job_worker_enabled=False))
    app.dependency_overrides[get_db] = lambda: object()

    response = TestClient(app).get("/setup-lifecycle/operations")

    assert response.status_code == 200
    assert "Setup Lifecycle Operations" in response.text
    assert 'action="/api/setup-lifecycle/replay"' in response.text
    assert 'action="/api/setup-lifecycle/evaluations"' in response.text
    assert "SINGLE_TICKER_RETRY" in response.text
    assert "slse-1.0.0" in response.text


def test_phase_10_navigation_and_run_detail_links_are_present() -> None:
    nav = Path("app/templates/partials/_nav.html").read_text(encoding="utf-8")
    run_detail = Path("app/templates/run_detail.html").read_text(encoding="utf-8")

    assert "/setup-lifecycle" in nav
    assert "/setup-lifecycle/alerts" in nav
    assert "setup_lifecycle_context.run_url" in run_detail


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
        return _alerts_payload()

    def evaluation_run(self, _db, evaluation_id: int):
        self.last_evaluation_id = evaluation_id
        return {"id": evaluation_id}

    def diagnostics(self, _db):
        return _diagnostics_payload()

    def ticker_timeline(
        self,
        _db,
        *,
        ticker: str,
        timeframe: str = "1d",
        limit: int = 100,
        cursor: str | None = None,
    ):
        return _ticker_payload(ticker)

    def episode_detail(self, _db, episode_id: int):
        payload = _episode_payload()
        payload["episode"]["id"] = episode_id
        return payload

    def operations(self, _db):
        return _operations_payload()


def _changes_payload() -> dict:
    return {
        "items": [
            {
                "id": 1,
                "episode_id": 12,
                "evaluation_run_id": 20,
                "snapshot_id": 30,
                "ticker": "MSFT",
                "timeframe": "1d",
                "setup_family": "BREAKOUT",
                "effective_date": "2026-08-01",
                "event_type": "STATE_TRANSITION",
                "from_state": "READY",
                "to_state": "TRIGGERED",
                "from_phase": "PIVOT_READY",
                "to_phase": "BREAKOUT_TRIGGERED",
                "actionability_before": "WATCH_ONLY",
                "actionability_after": "ACTIONABLE",
                "confidence_score": 91,
                "confidence_label": "HIGH",
                "severity": "ACTIONABLE",
                "source_event_key": "event-key",
                "is_current_version": True,
                "reason_codes": ["PRICE_TRIGGER_CONFIRMED"],
                "evidence": {"setup_score": 8.2, "velocity": 0.4, "sector_rank": 1},
            }
        ],
        "total": 1,
        "limit": 50,
        "cursor": None,
        "next_cursor": None,
        "sort": "latest_event_time",
        "direction": "desc",
    }


def _diagnostics_payload() -> dict:
    return {
        "latest_canonical_date": "2026-08-01",
        "latest_successful_evaluation": {"id": 20, "status": "COMPLETED"},
        "active_episode_count": 3,
        "pending_jobs": 1,
        "stale_lease_count": 1,
        "low_confidence_share": 0.25,
        "stale_system_warning": True,
    }


def _ticker_payload(ticker: str = "MSFT") -> dict:
    episode = _episode_payload()["episode"]
    return {
        "ticker": ticker,
        "timeframe": "1d",
        "snapshots": [
            {
                "id": 30,
                "run_id": 7,
                "ticker": ticker,
                "data_as_of_date": "2026-08-01",
                "origin_type": "RUN_CAPTURE",
                "is_canonical": True,
                "data_quality_label": "HIGH",
                "setup_score": 8.2,
                "distance_to_pivot_pct": 1.2,
                "technical_classification": "Breakout",
                "warning_flags": [],
            },
            {
                "id": 29,
                "run_id": 7,
                "ticker": ticker,
                "data_as_of_date": "2026-07-31",
                "origin_type": "RUN_CAPTURE",
                "is_canonical": True,
                "data_quality_label": "HIGH",
                "setup_score": 7.6,
                "distance_to_pivot_pct": 3.1,
                "technical_classification": "Breakout",
                "warning_flags": ["STALE_PRICE_BAR"],
            },
        ],
        "episodes": [episode],
        "lifecycle_events": _changes_payload()["items"],
        "signal_changes": [
            {
                "id": 2,
                "effective_date": "2026-08-01",
                "signal_key": "setup_score",
                "direction": "UP",
                "threshold_name": "ready",
            }
        ],
        "alerts": _alerts_payload()["items"],
        "source_links": {
            "source_run": "/runs/7",
            "technical_score_card": "/runs/7#ticker-MSFT",
            "market_regime": "/runs/7/market-regime",
            "sector_rotation": "/runs/7/sector-rotation",
            "owpe": "/winner-probability/tickers/MSFT",
        },
    }


def _episode_payload() -> dict:
    return {
        "episode": {
            "id": 12,
            "ticker": "MSFT",
            "timeframe": "1d",
            "setup_family": "BREAKOUT",
            "status": "CLOSED",
            "opened_on": "2026-07-31",
            "current_as_of_date": "2026-08-01",
            "last_observed_on": "2026-08-01",
            "closed_on": "2026-08-01",
            "missing_observation_sessions": 0,
            "current_state": "FAILED",
            "current_phase": "FAILED_SUPPORT",
            "state_age_sessions": 1,
            "current_actionability": "BLOCKED",
            "confidence_score": 70,
            "confidence_label": "LOW",
            "terminal_state": "FAILED",
            "terminal_reason_code": "FAILED_RULE",
            "is_primary": True,
            "primary_rank": 1,
            "metadata": {"setup_score": 8.2},
        },
        "snapshots": _ticker_payload("MSFT")["snapshots"] if False else [],
        "lifecycle_events": [
            _changes_payload()["items"][0],
            {**_changes_payload()["items"][0], "id": 2, "is_current_version": False},
        ],
        "signal_changes": [
            {
                "id": 3,
                "effective_date": "2026-08-01",
                "signal_key": "close_trigger_cross",
                "direction": "DOWN",
                "severity": "RISK",
            }
        ],
    }


def _alerts_payload() -> dict:
    return {
        "items": [
            {
                "id": 7,
                "alert_rule_id": 1,
                "lifecycle_event_id": 1,
                "signal_change_event_id": None,
                "evaluation_run_id": 20,
                "ticker": "MSFT",
                "timeframe": "1d",
                "effective_date": "2026-08-01",
                "event_key": "alert-key",
                "source_event_key": "event-key",
                "alert_type": "NEW_TRIGGER",
                "review_status": "UNREAD",
                "status": "UNREAD",
                "severity": "ACTIONABLE",
                "source_type": "LIFECYCLE_EVENT",
                "episode_id": 12,
                "lifecycle_state": "TRIGGERED",
                "actionability": "ACTIONABLE",
                "confidence": 86,
                "blockers": [],
                "source_url": "/setup-lifecycle/episodes/12",
                "reason_codes": ["BECAME_ACTIONABLE"],
                "evidence": {},
            }
        ],
        "total": 1,
        "limit": 50,
        "cursor": None,
        "next_cursor": None,
        "sort": "latest_event_time",
        "direction": "desc",
        "summary": {
            "unread": 1,
            "acknowledged": 0,
            "dismissed": 0,
            "info": 0,
            "notable": 0,
            "actionable": 1,
            "risk": 0,
        },
    }


def _operations_payload() -> dict:
    return {
        "summary": {
            "latest_status": "COMPLETED",
            "latest_evaluation_id": 20,
            "active_episodes": 3,
        },
        "runs": [
            {
                "id": 20,
                "source_run_id": 7,
                "mode": "LIVE",
                "status": "COMPLETED",
                "current_phase": "finalize",
                "engine_version": "slse-1.0.0",
                "config_version": "2026-07-31",
                "output_evaluation_version": "slse-1.0.0:live",
                "date_from": None,
                "date_to": None,
                "dry_run": False,
                "read_count": 10,
                "captured_count": 10,
                "canonical_count": 10,
                "changed_count": 4,
                "transitioned_count": 2,
                "alerted_count": 1,
                "warning_count": 0,
                "failed_count": 0,
                "created_at": "2026-08-01T21:00:00+00:00",
                "completed_at": "2026-08-01T21:00:03+00:00",
                "duration_ms": 3000,
                "errors": {},
            }
        ],
    }
