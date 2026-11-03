from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from time import perf_counter
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import JSON, Column, MetaData, Table, create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.main import create_app
from app.models.tables import PriceBar, PriceBarRevision, UploadRun
from app.routers import gui_routes
from app.routers import market_sector_intelligence_routes as routes
from app.services import market_sector_intelligence_coverage as coverage
from app.services.market_clock_service import MarketClockService
from app.services.market_sector_intelligence_config import (
    MarketSectorIntelligenceConfigError,
    load_market_sector_intelligence_config,
)
from app.services.price_bar_repository import load_preferred_ohlcv_frames

NOW = datetime(2026, 10, 21, 20, 30, tzinfo=UTC)
CUTOFF = MarketClockService().cutoff_for(NOW, reason="TEST")
URL = "/api/market-sector-intelligence/coverage"


@pytest.fixture
def cache_db():
    """Disposable in-memory SQL cache. No production connection or schema change."""
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    PriceBar.__table__.create(engine)
    # SQLite has JSON rather than PostgreSQL JSONB; only adapt this fixture's DDL.
    revision_table = Table(
        "price_bar_revisions",
        MetaData(),
        *[
            Column(
                column.name,
                JSON() if isinstance(column.type, JSONB) else column.type,
                primary_key=column.primary_key,
            )
            for column in PriceBarRevision.__table__.columns
        ],
    )
    revision_table.create(engine)
    UploadRun.__table__.create(engine)
    with Session(engine) as db:
        yield db, engine
    engine.dispose()


def client_for(db, monkeypatch):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    clock = Mock(wraps=MarketClockService())
    clock.cutoff_for.return_value = CUTOFF
    monkeypatch.setattr(coverage, "MarketClockService", lambda: clock)
    return TestClient(app), clock


def bar(identifier, ticker, session=date(2026, 10, 21), **overrides):
    values = dict(
        id=identifier,
        ticker=ticker,
        bar_date=session,
        timeframe="1 day",
        open=100,
        high=101,
        low=99,
        close=100,
        volume=1000,
        source="IB",
        what_to_show="TRADES",
        revision_count=0,
        created_at=NOW - timedelta(days=1),
        first_seen_at=NOW - timedelta(days=1),
        last_seen_at=NOW - timedelta(days=1),
    )
    return {**values, **overrides}


def assert_only_price_selects(statements):
    assert statements
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
    assert all("price_bars" in sql or "price_bar_revisions" in sql for sql in statements)
    assert all(
        "upload_runs" not in sql and "market_calculation_contexts" not in sql for sql in statements
    )


def test_empty_http_cache_is_read_only_and_independent_of_uploads(
    cache_db, monkeypatch, record_property
):
    db, engine = cache_db
    client, clock = client_for(db, monkeypatch)
    forbidden = Mock(side_effect=AssertionError("No acquisition or jobs during inspection"))
    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("ib_insync.IB.connect", forbidden)
    monkeypatch.setattr("app.services.background_job_service.enqueue_job", forbidden)
    statements = []
    event.listen(engine, "before_cursor_execute", lambda _c, _s, sql, *_a: statements.append(sql))

    start = perf_counter()
    first = client.get(URL)
    record_property("empty_coverage_seconds", perf_counter() - start)
    assert first.status_code == 200
    assert len(first.json()["symbols"]) == 12
    assert {row["status"] for row in first.json()["symbols"]} == {"MISSING"}
    # D1 preferred-basis reads plus independent freshness for both ETF bases.
    assert len(statements) == 46
    assert first.json()["freshness"]["status"] == "MISSING"
    assert len(first.json()["freshness"]["entries"]) == 22
    record_property("empty_coverage_selects", len(statements))
    assert_only_price_selects(statements)
    assert clock.cutoff_for.call_count == 1
    assert client.get("/settings").status_code == 200
    assert len(statements) == 46  # Settings needs no database reads at all

    # Only fixture setup writes occur between measured request boundaries.
    db.execute(
        UploadRun.__table__.insert(),
        [
            {"id": 1, "filename": "technology.csv", "status": "UPLOADED"},
            {"id": 2, "filename": "energy.csv", "status": "UPLOADED"},
        ],
    )
    db.commit()
    statements.clear()
    second = client.get(URL)
    assert second.json() == first.json()
    assert len(statements) == 46
    assert_only_price_selects(statements)
    assert forbidden.call_count == 0


def test_real_loader_obeys_session_receipt_revision_and_basis_boundaries(cache_db, monkeypatch):
    db, engine = cache_db
    client, _clock = client_for(db, monkeypatch)
    rows = [
        bar(1, "XLK"),
        bar(2, "XLF", first_seen_at=NOW + timedelta(seconds=1)),
        bar(3, "XLU", session=date(2026, 10, 22)),
        bar(4, "XLE", close=0, revised_at=NOW + timedelta(minutes=1), revision_count=1),
        bar(5, "XLY", close=0, revised_at=NOW + timedelta(minutes=1), revision_count=1),
        bar(6, "SPY", session=date(2026, 10, 20)),
        bar(7, "SPY"),
        bar(8, "SPY", what_to_show="ADJUSTED_LAST"),  # incomplete basis must use TRADES
        bar(9, "XLK", what_to_show="ADJUSTED_LAST"),
    ]
    # Core fixture inserts intentionally bypass production mutation workflows.
    for values in rows:
        db.execute(PriceBar.__table__.insert().values(**values))
    db.execute(
        PriceBarRevision.__table__.insert().values(
            id=1,
            price_bar_id=4,
            ticker="XLE",
            bar_date=date(2026, 10, 21),
            timeframe="1 day",
            what_to_show="TRADES",
            revision_number=1,
            previous_data_hash="before",
            new_data_hash="after",
            previous_values_json={
                "open": "100",
                "high": "101",
                "low": "99",
                "close": "100",
                "volume": "1000",
            },
            new_values_json={"close": "0"},
            observed_at=NOW + timedelta(minutes=1),
        )
    )
    db.commit()
    statements = []
    event.listen(engine, "before_cursor_execute", lambda _c, _s, sql, *_a: statements.append(sql))
    response = client.get(URL)
    assert response.status_code == 200
    by_ticker = {row["ticker"]: row for row in response.json()["symbols"]}
    assert by_ticker["XLK"]["status"] == "CURRENT"
    assert by_ticker["XLK"]["price_basis"] == "ADJUSTED_LAST"
    assert by_ticker["XLF"]["status"] == "MISSING"  # received after frozen cutoff
    assert by_ticker["XLU"]["status"] == "MISSING"  # future market session
    assert by_ticker["XLE"]["status"] == "CURRENT"  # original valid close, not later zero
    assert by_ticker["XLY"]["status"] == "MISSING"  # missing revision history fails closed
    assert by_ticker["SPY"]["bar_count"] == 2
    assert by_ticker["SPY"]["price_basis"] == "TRADES"
    assert_only_price_selects(statements)
    selected, _ = load_preferred_ohlcv_frames(
        db, "XLE", max_session=CUTOFF.latest_completed_session, as_of=NOW
    )
    assert selected.iloc[-1]["close"] == 100
    # The historical projection never changes the persisted latest close.
    assert db.get(PriceBar, 4).close == 0


def test_disabled_http_does_not_read_prices_and_crosswalk_stays_visible(cache_db, monkeypatch):
    db, engine = cache_db
    client, _clock = client_for(db, monkeypatch)
    config = replace(load_market_sector_intelligence_config(), enabled=False)
    monkeypatch.setattr(routes, "load_market_sector_intelligence_config", lambda: config)
    monkeypatch.setattr(gui_routes, "load_market_sector_intelligence_config", lambda: config)
    loader = Mock(side_effect=AssertionError("Disabled must skip loader"))
    monkeypatch.setattr(coverage, "load_preferred_ohlcv_frames", loader)
    statements = []
    event.listen(engine, "before_cursor_execute", lambda _c, _s, sql, *_a: statements.append(sql))
    result = client.get(URL)
    assert result.status_code == 200
    assert {row["status"] for row in result.json()["symbols"]} == {"DISABLED"}
    settings = client.get("/settings")
    assert "Finance" in settings.text and "Consumer Staples" in settings.text
    assert statements == []
    assert loader.call_count == 0


def test_service_does_not_autoflush_pending_caller_changes(cache_db):
    db, engine = cache_db
    db.autoflush = True
    pending = UploadRun(id=99, filename="pending.csv", status="UPLOADED")
    db.add(pending)
    statements = []
    event.listen(engine, "before_cursor_execute", lambda _c, _s, sql, *_a: statements.append(sql))
    result = coverage.MarketSectorIntelligenceCoverageService().build(db, cutoff=CUTOFF)
    assert len(result["symbols"]) == 12
    assert pending in db.new
    assert_only_price_selects(statements)
    db.rollback()


def test_invalid_config_is_explicit_and_isolated_from_old_settings(cache_db, monkeypatch):
    db, _engine = cache_db
    client, _clock = client_for(db, monkeypatch)

    def bad_config():
        raise MarketSectorIntelligenceConfigError(
            "Exactly eleven market-sector ETF proxies are required."
        )

    monkeypatch.setattr(routes, "load_market_sector_intelligence_config", bad_config)
    monkeypatch.setattr(gui_routes, "load_market_sector_intelligence_config", bad_config)
    response = client.get(URL)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "MARKET_SECTOR_CONFIGURATION_INVALID"
    html = client.get("/settings")
    assert html.status_code == 200
    for label in (
        "App",
        "Storage",
        "IB Gateway",
        "IB Fetch Planning",
        "Scoring",
        "Independent sector configuration unavailable",
        "Exactly eleven",
    ):
        assert label in html.text


def test_settings_contains_accessible_crosswalk_and_original_groups():
    html = TestClient(create_app()).get("/settings")
    assert html.status_code == 200
    for value in (
        "Yes (default)",
        "SPY",
        "1 day",
        "11 sector ETFs",
        "1.0.0",
        "Finance",
        "Financial Services",
        "Financials",
        "XLF",
        "Consumer non-durables",
        "Consumer Defensive",
        "Consumer Staples",
        "XLP",
        "Government / Other",
        "ETF / Fund",
        "Unknown",
        "Off (separate policy)",
        URL,
        "<caption>",
        'scope="col"',
        'scope="row"',
        "App",
        "Storage",
        "IB Fetch Planning",
        "Scoring",
    ):
        assert value in html.text
    assert "<form" not in html.text
    assert "Save" not in html.text


def test_route_registration_is_unique_read_only_and_keeps_run_routes():
    app = create_app()
    matching = [route for route in app.routes if getattr(route, "path", None) == URL]
    assert len(matching) == 1
    assert matching[0].methods == {"GET"}
    assert any(
        getattr(route, "path", "") == "/runs/{run_id}/sector-rotation" for route in app.routes
    )
    assert any(
        getattr(route, "path", "") == "/api/runs/{run_id}/sector-rotation" for route in app.routes
    )
    assert TestClient(app).get(URL, headers={"host": "untrusted.example"}).status_code == 400
