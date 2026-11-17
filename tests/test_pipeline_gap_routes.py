"""Operator actions preserve run ownership and commit only accepted requests."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.db import get_db
from app.routers import run_routes
from app.services import pipeline_market_data_recovery
from app.templates import templates


def _client(monkeypatch, *, mismatch=False):
    state = []
    db = SimpleNamespace(
        commit=lambda: state.append("commit"), rollback=lambda: state.append("rollback"),
    )
    monkeypatch.setattr(run_routes, "_require_run", lambda *_args: None)

    def require_owner(*_args):
        if mismatch:
            raise HTTPException(status_code=404, detail="Pipeline run not found for this run.")
        return SimpleNamespace(upload_run_id=57)

    monkeypatch.setattr(run_routes, "_require_pipeline_for_run", require_owner)
    app = FastAPI()
    app.include_router(run_routes.router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app), state, db


def test_full_population_recovery_redirects_to_derived_upload(monkeypatch):
    client, state, db = _client(monkeypatch)
    received = []

    def recover(selected_db, **kwargs):
        assert selected_db is db
        received.append(kwargs)
        return SimpleNamespace(id=59, upload_run_id=58)

    monkeypatch.setattr(pipeline_market_data_recovery, "start_market_data_recovery", recover)
    response = client.post("/runs/57/pipeline/58/recover-with-gaps", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/runs/58/pipeline/59"
    assert received == [{"source_pipeline_id": 58, "upload_run_id": 57}]
    assert state == ["commit"]


def test_recovery_rejects_cross_run_pipeline_before_admission(monkeypatch):
    client, state, _db = _client(monkeypatch, mismatch=True)
    monkeypatch.setattr(
        pipeline_market_data_recovery, "start_market_data_recovery",
        lambda *_args, **_kwargs: pytest.fail("Wrong owner must not reach admission"),
    )
    response = client.post("/runs/99/pipeline/58/recover-with-gaps", follow_redirects=False)
    assert response.status_code == 404
    assert state == []


def test_recovery_conflict_rolls_back_and_does_not_redirect(monkeypatch):
    client, state, _db = _client(monkeypatch)

    def changed_source(*_args, **_kwargs):
        raise ValueError("MARKET_DATA_RECOVERY_SOURCE_ROWS_CHANGED")

    monkeypatch.setattr(pipeline_market_data_recovery, "start_market_data_recovery", changed_source)
    response = client.post("/runs/57/pipeline/58/recover-with-gaps", follow_redirects=False)
    assert response.status_code == 409
    assert state == ["rollback"]


def test_gap_repair_requires_reviewed_manifest_and_rolls_back_stale_selection(monkeypatch):
    from app.services import ib_gap_repair_service

    client, state, _db = _client(monkeypatch)
    seen = []

    def stale_selection(*_args, **kwargs):
        seen.append(kwargs)
        raise ValueError("GAP_REPAIR_MANIFEST_CHANGED")

    monkeypatch.setattr(ib_gap_repair_service, "submit_gap_repair", stale_selection)
    missing = client.post("/runs/57/pipeline/58/repair-history-gaps", follow_redirects=False)
    assert missing.status_code == 422
    assert seen == []
    stale = client.post(
        "/runs/57/pipeline/58/repair-history-gaps",
        data={"manifest_fingerprint": "a" * 64}, follow_redirects=False,
    )
    assert stale.status_code == 409
    assert seen == [{"pipeline_run_id": 58, "expected_manifest_fingerprint": "a" * 64}]
    assert state == ["rollback"]


def test_repair_review_shows_missing_days_without_internal_receipts(monkeypatch):
    monkeypatch.setitem(templates.env.globals, "url_for", lambda _name, path: path)
    html = templates.get_template("history_gap_repair.html").render(
        run=SimpleNamespace(id=57), pipeline_id=58,
        preview={"manifest_fingerprint": "a" * 64, "manifest": {
            "pipeline_run_id": 58, "configuration_reference": "internal-config-address",
            "obligations": [{
                "ticker": "HLT", "what_to_show": "TRADES",
                "missing_sessions": ["2026-11-04"],
                "contract_identity": {"conId": 257944570},
                "source_receipt_hashes": ["internal-acceptance-receipt"],
            }],
        }},
    )
    assert "HLT" in html and "2026-11-04" in html
    assert 'name="manifest_fingerprint" value="' + "a" * 64 in html
    assert "internal-config-address" not in html
    assert "internal-acceptance-receipt" not in html
    assert "257944570" not in html


def test_partial_progress_distinguishes_unavailable_from_historical_gap(monkeypatch):
    monkeypatch.setitem(templates.env.globals, "url_for", lambda _name, path: path)
    html = templates.get_template("pipeline_progress.html").render(
        run=SimpleNamespace(id=57, filename="companies.csv"),
        pipeline={"pipeline_run_id": 59, "status": "PARTIAL", "steps": [],
                  "percentage": 100, "completed_steps": 13, "total_steps": 13, "result": {
            "market_data_history_gap_count": 1,
            "market_data_availability": {
                "unavailable_tickers": ["CLBK"], "history_gap_tickers": ["HLT"],
                "continued_with_gaps": True,
            },
            "market_data_session_readiness": {"tickers": {
                "HLT": {
                    "disposition": "READY", "history_gap_count": 1,
                    "missing_sessions": ["2026-11-04"],
                    "latest_bar_session": "2026-11-09",
                    "reason_message": "Current session available with historical gaps.",
                    "provider_results": [{"actual_end_date": "2026-11-09"}],
                },
                "CLBK": {
                    "disposition": "REQUIRED_DATA_UNAVAILABLE", "latest_bar_session": None,
                    "reason_message": "Provider returned no historical data.",
                },
            }},
        }},
        terminal_statuses=["PARTIAL", "COMPLETED", "BLOCKED", "FAILED", "CANCELLED"],
        status_url="/runs/57/pipeline/59/status",
    )
    assert "Other companies continue." in html
    assert "Current session available with historical gaps." in html
    assert "Provider returned no historical data." in html
    assert "Missing days: 2026-11-04." in html
    assert "Returned through 2026-11-09" in html
    assert "available through 2026-11-09" in html
