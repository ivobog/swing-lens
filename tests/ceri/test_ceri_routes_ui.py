from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import create_app
from app.routers import ceri_routes
from app.settings import Settings


def test_ceri_nav_is_feature_flagged() -> None:
    enabled = create_app(
        Settings(_env_file=None, job_worker_enabled=False, ceri_enabled=True, ceri_ui_enabled=True)
    )
    disabled = create_app(Settings(_env_file=None, job_worker_enabled=False, ceri_ui_enabled=False))
    enabled.dependency_overrides[get_db] = lambda: object()
    disabled.dependency_overrides[get_db] = lambda: object()

    assert 'href="/ceri"' in TestClient(enabled).get("/help").text
    assert 'href="/ceri"' not in TestClient(disabled).get("/help").text


def test_ceri_dashboard_renders_full_data_and_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_service = FakeCeriQueryService()
    monkeypatch.setattr(ceri_routes, "CeriQueryService", lambda: fake_service)
    monkeypatch.setattr(ceri_routes, "_provider_health_payload", lambda: [])
    app = _app(ceri_ui_enabled=True)

    response = TestClient(app).get(
        "/ceri?opportunity_min=7&risk_max=3&confidence=Low&has_warnings=true"
    )

    assert response.status_code == 200
    assert "CERI Dashboard" in response.text
    assert "MSFT" in response.text
    assert "Low" in response.text
    assert "Warnings present" in response.text
    assert "Provider Freshness" in response.text
    assert fake_service.latest_queries[-1].filters.opportunity_min == 7
    assert fake_service.latest_queries[-1].filters.risk_max == 3
    assert fake_service.latest_queries[-1].filters.has_warnings is True


def test_ceri_dashboard_renders_empty_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ceri_routes, "CeriQueryService", lambda: EmptyCeriQueryService())
    app = _app(ceri_ui_enabled=True)

    response = TestClient(app).get("/ceri")

    assert response.status_code == 200
    assert "No CERI candidates match this view." in response.text


def test_ceri_ticker_detail_renders_provenance_and_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ceri_routes, "CeriQueryService", lambda: FakeCeriQueryService())
    app = _app(ceri_ui_enabled=True)

    response = TestClient(app).get("/ceri/ticker/MSFT")

    assert response.status_code == 200
    assert "Score Components" in response.text
    assert "Estimate Revisions" in response.text
    assert "Up 6 / Down 1" in response.text
    assert "[101, 102]" in response.text
    assert "Revision 1" in response.text
    assert "Review Needs Review" in response.text
    assert "Cutoff 2026-08-02T12:00:00+00:00" in response.text


def test_ceri_changes_render_groups_and_alert_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ceri_routes, "CeriQueryService", lambda: FakeCeriQueryService())
    app = _app(ceri_ui_enabled=True, ceri_admin_enabled=True)

    response = TestClient(app).get("/ceri/changes?ticker=MSFT&status=UNREAD")

    assert response.status_code == 200
    assert "Upward revisions" in response.text
    assert "Risk changes" in response.text
    assert 'data-ceri-alert-action="/api/ceri/alerts/9/acknowledge"' in response.text
    assert "Alert actions update only alert state" in response.text


def test_ceri_operations_render_health_checkpoints_and_preview_only_purge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ceri_routes, "CeriQueryService", lambda: FakeCeriQueryService())
    monkeypatch.setattr(
        ceri_routes,
        "_provider_health_payload",
        lambda: [
            {
                "provider": "manual",
                "healthy": True,
                "quota_status": "manual",
                "capabilities": ["health", "estimates"],
                "datasets": ["estimates"],
                "message": "1 manual record loaded.",
            }
        ],
    )
    app = _app(ceri_ui_enabled=True, ceri_admin_enabled=True)

    response = TestClient(app).get("/ceri/operations")

    assert response.status_code == 200
    assert "Provider Health" in response.text
    assert "CERI_BACKFILL" in response.text
    assert "Queue backfill" in response.text
    assert "Preview purge" in response.text
    assert "/api/ceri/purge/execute" not in response.text


def test_run_detail_renders_ceri_status_when_snapshots_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.routers.run_routes._load_run",
        lambda _db, _run_id: _run(),
    )
    monkeypatch.setattr(
        "app.routers.run_routes._ceri_context",
        lambda _db, _run_id: _ceri_context(),
    )
    monkeypatch.setattr(
        "app.routers.run_routes._winner_probability_context",
        lambda _db, _run_id: {
            "prediction_count": 0,
            "estimate_count": 0,
            "insufficient_count": 0,
            "run_url": "/runs/7/winner-probability",
            "operations_url": "/winner-probability/operations",
            "prediction_by_ticker": {},
        },
    )
    monkeypatch.setattr(
        "app.routers.run_routes._setup_lifecycle_context",
        lambda _db, _run_id: {
            "change_count": 0,
            "latest_status": None,
            "active_episode_count": 0,
            "run_url": "/runs/7/setup-lifecycle",
            "operations_url": "/setup-lifecycle/operations",
        },
    )
    monkeypatch.setattr(
        "app.routers.run_routes.summarize_run_ohlcv_coverage",
        lambda *_args: SimpleNamespace(
            ready_count=0,
            insufficient_count=0,
            stale_count=0,
            missing_volume_count=0,
            failed_contract_count=0,
            missing_count=0,
            required_rows=0,
            total_tickers=0,
            benchmark_spy_ready=False,
            benchmark_qqq_ready=False,
            items=[],
        ),
    )
    monkeypatch.setattr("app.routers.run_routes.latest_ib_fetch_for_run", lambda *_args: None)
    monkeypatch.setattr("app.routers.run_routes._pipeline_status_for_run", lambda *_args: None)
    monkeypatch.setattr("app.routers.run_routes._latest_run_market_snapshot", lambda *_args: None)
    monkeypatch.setattr(
        "app.routers.run_routes._latest_sector_rotation_context",
        lambda *_args: None,
    )
    app = _app(ceri_ui_enabled=True)

    response = TestClient(app).get("/runs/7")

    assert response.status_code == 200
    assert "CERI Evidence" in response.text
    assert "3</strong> snapshots" in response.text
    assert 'href="/runs/7/ceri"' in response.text


def _app(**settings):
    app = create_app(
        Settings(
            _env_file=None,
            job_worker_enabled=False,
            ceri_enabled=True,
            **settings,
        )
    )
    app.dependency_overrides[get_db] = lambda: object()
    return app


def _run():
    return SimpleNamespace(
        id=7,
        filename="run.csv",
        status="COMPLETED",
        uploaded_at="2026-08-02",
        processed_at=None,
        notes=None,
        error_message=None,
        row_count=0,
        raw_company_rows=[],
        fundamental_scores=[],
        technical_scores=[],
        combined_results=[],
        ranking_results=[],
    )


def _ceri_context():
    return {
        "snapshot_count": 3,
        "low_confidence_count": 1,
        "warning_count": 1,
        "high_opportunity_low_risk_count": 2,
        "latest_status": "Captured",
        "run_url": "/runs/7/ceri",
        "dashboard_url": "/ceri",
        "operations_url": "/ceri/operations",
    }


class FakeCeriQueryService:
    def __init__(self) -> None:
        self.latest_queries = []

    def latest(self, _db, query):
        self.latest_queries.append(query)
        return {
            "items": [_snapshot()],
            "total": 1,
            "limit": query.limit,
            "offset": query.offset,
        }

    def run(self, _db, _run_id, query):
        return {
            "items": [_snapshot()],
            "total": 1,
            "limit": query.limit,
            "offset": query.offset,
        }

    def ticker(self, _db, ticker):
        return {
            "ticker": ticker.upper(),
            "latest": _snapshot(),
            "revision_features": [
                {
                    "metric": "EPS_DILUTED",
                    "period_key": "FY2026",
                    "window_days": 30,
                    "actual_elapsed_days": 29,
                    "pct_change": 5.0,
                    "net_breadth": 0.714286,
                    "raw_breadth_counts": {"upward_count": 6, "downward_count": 1},
                    "dispersion": 0.2,
                    "revision_confidence_label": "High",
                    "source_observation_ids": [101, 102],
                }
            ],
            "events": [
                {
                    "category": "REGULATORY",
                    "status": "SCHEDULED",
                    "canonical_text": "FDA decision",
                    "expected_date": "2026-08-15",
                    "direction": "NEGATIVE",
                    "current_revision": {
                        "revision_number": 1,
                        "source_record_id": 33,
                        "review_state": "Needs Review",
                    },
                }
            ],
            "alerts": [],
        }

    def changes(self, _db, query):
        return {
            "items": [
                {
                    "id": 5,
                    "ticker": "MSFT",
                    "change_type": "REVISION_UP",
                    "severity": "NOTABLE",
                    "created_at": "2026-08-02T12:00:00+00:00",
                    "from_snapshot_id": 1,
                    "to_snapshot_id": 2,
                    "catalyst_revision_id": None,
                },
                {
                    "id": 6,
                    "ticker": "MSFT",
                    "change_type": "RISK_ESCALATED",
                    "severity": "RISK",
                    "created_at": "2026-08-02T12:05:00+00:00",
                    "from_snapshot_id": 2,
                    "to_snapshot_id": 3,
                    "catalyst_revision_id": 4,
                },
            ],
            "total": 2,
            "limit": query.limit,
            "offset": query.offset,
        }

    def alerts(self, _db, _query):
        return {
            "items": [
                {
                    "id": 9,
                    "ticker": "MSFT",
                    "severity": "RISK",
                    "status": "UNREAD",
                    "evidence": {"change_type": "RISK_ESCALATED"},
                }
            ],
            "total": 1,
        }

    def operations_status(self, _db):
        return {
            "dataset_freshness": [
                {
                    "provider": "manual",
                    "dataset": "estimates",
                    "age_days": 9,
                    "fresh": False,
                }
            ],
            "quota_state": [],
            "errors": [],
            "quarantined_count": 1,
            "conflicted_count": 1,
            "stale_count": 1,
            "processing_runs": [
                {
                    "id": 1,
                    "job_type": "CERI_BACKFILL",
                    "status": "RUNNING",
                    "counts": {"read": 10},
                    "checkpoint": {"ticker": "MSFT"},
                    "retry_count": 0,
                    "duration_ms": None,
                }
            ],
            "provider_terms_version": "manual-fixture-1.0",
            "retention": {
                "retain_source_evidence_indefinitely": True,
                "provider_license_purge_enabled": False,
            },
            "export_restrictions": {"raw_payload": "restricted"},
            "purge_previews": [],
        }

    def operations_quarantine(self, _db, _query):
        return {
            "items": [
                {
                    "provider": "manual",
                    "dataset": "estimates",
                    "quarantine_reason": "missing_provider_record_id",
                    "provider_record_id": "q-1",
                }
            ]
        }

    def operations_conflicts(self, _db, _query):
        return {"items": [{"id": 2, "conflict_flags": ["provider_disagreement"]}]}

    def operations_stale(self, _db, _query):
        return {
            "items": [
                {
                    "provider": "manual",
                    "dataset": "estimates",
                    "stale_days": 9,
                    "max_stale_days": 7,
                }
            ]
        }


class EmptyCeriQueryService(FakeCeriQueryService):
    def latest(self, _db, query):
        return {"items": [], "total": 0, "limit": query.limit, "offset": query.offset}


def _snapshot():
    return {
        "id": 1,
        "run_id": 7,
        "ticker": "MSFT",
        "as_of_session": "2026-08-02",
        "cutoff_at": "2026-08-02T12:00:00+00:00",
        "opportunity_score": 8.2,
        "event_risk_score": 2.5,
        "data_confidence": "Low",
        "coverage_pct": 80.0,
        "posture": "Positive",
        "earnings_proximity_risk": None,
        "components": {
            "eps_revision_30d": 5.0,
            "revision_breadth": 0.71,
            "guidance_direction": "Raised",
            "next_event": "FDA decision",
        },
        "top_positive_contributors": [{"label": "EPS revision", "value": 2.1}],
        "top_negative_contributors": [{"label": "Binary risk", "value": -1.4}],
        "warnings": ["estimate_data_stale"],
        "config_version": "2026-07-31",
        "evidence_hash": "evidence-MSFT",
    }
