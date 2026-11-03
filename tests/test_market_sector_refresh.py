"""Manual admission security and independent per-basis stored-evidence freshness."""

from contextlib import nullcontext
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import create_app
from app.routers import market_sector_intelligence_routes as routes
from app.security import http_mutation_route_registry
from app.services.market_clock_service import MarketClockService
from app.services.market_sector_intelligence_config import load_market_sector_intelligence_config
from app.services.market_sector_intelligence_coverage import MarketSectorIntelligenceCoverageService
from app.services.market_sector_refresh_service import BASES
from app.settings import RuntimeMode, Settings


def frame(sessions, closes=None):
    return pd.DataFrame(
        {"date": sessions, "close": [100] * len(sessions) if closes is None else closes}
    )


def freshness(loader, stamp=datetime(2026, 10, 26, 15, tzinfo=UTC)):
    cutoff = MarketClockService().cutoff_for(stamp, reason="REFRESH_TEST")
    service = MarketSectorIntelligenceCoverageService(
        frame_loader=lambda *_a, **_kw: (pd.DataFrame(), None),
        basis_loader=loader,
    )
    return service.build(object(), cutoff=cutoff)["freshness"]


@pytest.mark.parametrize(
    "stamp,expected",
    [
        (datetime(2026, 10, 24, 12, tzinfo=UTC), date(2026, 10, 23)),  # Saturday
        (datetime(2026, 10, 25, 12, tzinfo=UTC), date(2026, 10, 23)),
        (datetime(2026, 10, 26, 20, 14, tzinfo=UTC), date(2026, 10, 23)),
        (datetime(2026, 10, 26, 20, 15, tzinfo=UTC), date(2026, 10, 26)),
        (datetime(2026, 12, 25, 20, tzinfo=UTC), date(2026, 12, 24)),
        (datetime(2026, 7, 3, 19, tzinfo=UTC), date(2026, 7, 2)),
    ],
)
def test_expected_session_calendar_readiness_weekends_holidays_and_forming(stamp, expected):
    calls = []

    def load(*args, **kwargs):
        calls.append((args[1:], kwargs))
        return frame([expected])

    result = freshness(load, stamp)
    assert result["status"] == "CURRENT"
    assert result["calendar_version"] == "swinglens-us-equities-v2"
    assert result["expected_session"] == expected.isoformat()
    assert len(calls) == 22
    assert {key[1] for key, _ in calls} == set(BASES)
    assert all(
        kwargs["max_session"] == expected and kwargs["as_of"] == stamp for _, kwargs in calls
    )


def test_one_basis_lag_invalid_latest_close_and_missing_are_not_hidden():
    def load(_db, ticker, basis, **_kwargs):
        if ticker == "XLK" and basis == "ADJUSTED_LAST":
            return frame([date(2026, 10, 21), date(2026, 10, 23)], [99, 0])
        if ticker == "XLF" and basis == "TRADES":
            return frame([date(2026, 10, 22)])
        if ticker == "XLU" and basis == "ADJUSTED_LAST":
            return frame([])
        return frame([date(2026, 10, 23)])

    result = freshness(load)
    assert result["status"] == "STALE" and result["max_session_lag"] == 2
    rows = {(row["ticker"], row["price_basis"]): row for row in result["entries"]}
    assert rows["XLK", "ADJUSTED_LAST"]["last_session"] == "2026-10-21"
    assert rows["XLF", "TRADES"]["session_lag"] == 1
    assert rows["XLU", "ADJUSTED_LAST"]["status"] == "MISSING"


@pytest.mark.parametrize(
    "kind", ["absent", "invalid", "receipt", "duplicate", "future", "exception"]
)
def test_missing_or_unknown_never_invents_lag(kind):
    def load(*_a, **_kw):
        if kind == "exception":
            raise RuntimeError("private connection string")
        result = frame([]) if kind == "absent" else frame([date(2026, 10, 23)])
        if kind == "invalid":
            result["close"] = [float("nan")]
        if kind == "receipt":
            result["first_seen_at"] = [datetime(2026, 10, 27, tzinfo=UTC)]
        if kind == "duplicate":
            result = frame([date(2026, 10, 23)] * 2)
        if kind == "future":
            result = frame([date(2026, 10, 26)])
        return result

    result = freshness(load)
    assert result["status"] == ("MISSING" if kind in {"absent", "invalid"} else "UNKNOWN")
    assert result["max_session_lag"] is None
    assert all(row["session_lag"] is None for row in result["entries"])
    assert "private" not in str(result)


@pytest.fixture
def secured_client(monkeypatch):
    app = create_app(Settings(_env_file=None, job_worker_enabled=False))
    db = Mock()
    app.dependency_overrides[get_db] = lambda: db
    submit = Mock(return_value={"job_id": 71, "fetch_run_id": 28, "status": "QUEUED"})
    monkeypatch.setattr(routes.refresh, "enqueue_sector_refresh", submit)
    return app, TestClient(app), db, submit


def headers(app, **kwargs):
    return {
        "origin": "http://testserver",
        "x-csrf-token": app.state.local_admin_csrf_token,
        **kwargs,
    }


@pytest.mark.parametrize(
    "case",
    [
        "csrf",
        "origin",
        "cross_site",
        "nonlocal",
        "arbitrary_symbols",
        "arbitrary_host",
        "certification",
    ],
)
def test_unauthorized_requests_never_reach_admission(secured_client, case):
    app, client, db, submit = secured_client
    if case == "nonlocal":
        client = TestClient(app, client=("192.0.2.1", 12345))
    request_headers = headers(app)
    body = {}
    if case == "csrf":
        request_headers.pop("x-csrf-token")
    if case == "origin":
        request_headers["origin"] = "https://evil.example"
    if case == "cross_site":
        request_headers["sec-fetch-site"] = "cross-site"
    if case == "arbitrary_symbols":
        body["tickers"] = ["TSLA"]
    if case == "arbitrary_host":
        body["host"] = "evil.example"
    if case == "certification":
        app.state.settings = app.state.settings.model_copy(
            update={"runtime_mode": RuntimeMode.CERTIFICATION}
        )
    response = client.post(
        "/api/market-sector-intelligence/refresh", json=body, headers=request_headers
    )
    assert response.status_code in {403, 409, 422}
    submit.assert_not_called()
    db.commit.assert_not_called()


def test_authorized_post_atomic_commit_and_inventory(secured_client):
    app, client, db, submit = secured_client
    response = client.post("/api/market-sector-intelligence/refresh", json={}, headers=headers(app))
    assert response.status_code == 202 and response.json()["job_id"] == 71
    submit.assert_called_once_with(db, settings=app.state.settings)
    db.commit.assert_called_once()
    classification = next(
        row.classification
        for row in http_mutation_route_registry(app)
        if row.path == "/api/market-sector-intelligence/refresh"
    )
    assert classification.csrf_required and classification.local_admin_required


def test_preflight_failure_rolls_back_without_echoing_diagnostics(secured_client):
    app, client, db, submit = secured_client
    submit.side_effect = routes.refresh.SectorRefreshRejected(
        "IB_GATEWAY_NOT_READY", "Sign in to IB Gateway.", 503
    )
    response = client.post("/api/market-sector-intelligence/refresh", json={}, headers=headers(app))
    assert response.status_code == 503
    db.rollback.assert_called_once()
    db.commit.assert_not_called()


def test_repeated_preflight_failure_is_rate_limited_without_writing(monkeypatch):
    service = routes.refresh
    monkeypatch.setattr(service, "_last_probe", float("-inf"))
    monkeypatch.setattr(service, "monotonic", lambda: 100)
    probe = Mock(return_value=SimpleNamespace(status="IB_PROCESS_NOT_RUNNING"))
    monkeypatch.setattr(service, "check_status", probe)
    with pytest.raises(service.SectorRefreshRejected) as first:
        service._preflight(Settings(_env_file=None))
    assert first.value.code == "IB_GATEWAY_NOT_READY"
    with pytest.raises(service.SectorRefreshRejected) as second:
        service._preflight(Settings(_env_file=None))
    assert second.value.code == "REFRESH_RATE_LIMITED" and second.value.status_code == 429
    probe.assert_called_once()


@pytest.mark.parametrize(
    "job_status,fetch_status,expected",
    [
        ("COMPLETED", "COMPLETED", "COMPLETED"),
        ("COMPLETED", "PARTIAL", "PARTIAL"),
        ("COMPLETED", "FAILED", "FAILED"),
        ("FAILED", "RUNNING", "FAILED"),
        ("RUNNING", "COMPLETED", "RUNNING"),
        ("QUEUED", "PARTIAL", "QUEUED"),
    ],
)
def test_status_uses_both_durable_job_and_exact_fetch_run(
    monkeypatch, job_status, fetch_status, expected
):
    service = routes.refresh
    read = Mock(
        return_value={
            "fetch_run_id": 28,
            "status": fetch_status,
            "completed_items": 3,
            "total_items": 7,
            "success_count": 2,
            "failure_count": 1,
            "items": [{"ticker": "XLK", "what_to_show": "TRADES", "status": "FAILED"}],
        }
    )
    monkeypatch.setattr(service, "get_fetch_progress", read)
    result = service.sector_refresh_status(
        SimpleNamespace(no_autoflush=nullcontext()),
        SimpleNamespace(id=71, status=job_status, payload_json={"fetch_run_id": 28}),
    )
    assert result["status"] == expected
    assert result["total_items"] == 7 and result["completed_items"] == 3
    assert result["failures"] == [{"ticker": "XLK", "basis": "TRADES"}]
    assert result["status_url"].endswith("/71")
    assert set(load_market_sector_intelligence_config().sectors)
