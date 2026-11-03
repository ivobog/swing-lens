import json
from dataclasses import replace
from datetime import timedelta
from time import perf_counter
from unittest.mock import Mock

from fastapi.testclient import TestClient
from market_sector_technical_helpers import CUTOFF, NOW
from sqlalchemy import event, select
from sqlalchemy.orm import Session
from test_market_sector_intelligence_rotation_storage import rotation_engine as rotation_engine
from test_market_sector_intelligence_technical_pit import seed_cache

from app.db import get_db
from app.main import create_app
from app.models.tables import PriceBar, PriceBarRevision, UploadRun
from app.routers import gui_routes
from app.routers import market_sector_intelligence_routes as routes
from app.services.market_sector_intelligence_chart_service import MarketSectorChartService
from app.services.market_sector_intelligence_config import load_market_sector_intelligence_config
from app.services.price_bar_repository import load_preferred_ohlcv_frames
from app.settings import Settings

PATH = "/api/market-sector-intelligence/charts/price-series"


def chart_service(cutoff=CUTOFF):
    return MarketSectorChartService(clock=Mock(cutoff_for=Mock(return_value=cutoff)))


def client_for(engine):
    app = create_app(Settings(_env_file=None, job_worker_enabled=False))

    def database():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = database
    return app, TestClient(app)


def test_actual_chart_get_bounded_selects_no_mutations_autoflush_or_providers(
    rotation_engine,
    monkeypatch,
    record_property,
):
    seed_cache(rotation_engine)
    forbidden = Mock(side_effect=AssertionError("No D4 mutation or acquisition"))
    for path in (
        "socket.create_connection",
        "ib_insync.IB.connect",
        "app.services.background_job_service.enqueue_job",
        "app.services.market_calculation_context_service.create_pipeline_market_context",
        "app.services.technical_artifact_cache.upsert_local_artifact",
        "app.services.technical_score_service.build_technical_score",
        "app.services.market_sector_intelligence_rotation_repository.MarketSectorRotationRepository.capture",
    ):
        monkeypatch.setattr(path, forbidden)
    monkeypatch.setattr(routes, "MarketSectorChartService", chart_service)
    app, client = client_for(rotation_engine)
    statements = []
    event.listen(
        rotation_engine, "before_cursor_execute", lambda _c, _s, sql, *_a: statements.append(sql)
    )
    with Session(rotation_engine, autoflush=True) as db:
        pending = UploadRun(id=991, filename="unflushed.csv", status="UPLOADED")
        db.add(pending)

        def database():
            yield db

        app.dependency_overrides[get_db] = database
        start = perf_counter()
        response = client.get(PATH + "?horizon=126")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["cohort_status"] == "COMPLETE"
        assert len(payload["rows"]) == 11 and len(statements) == 24
        assert all(
            sql.lstrip().upper().startswith("SELECT") and "price_bars" in sql for sql in statements
        )
        assert all("bar_date >=" in sql and "bar_date <=" in sql for sql in statements)
        assert all(row["bar_count"] == 127 for row in payload["rows"])
        assert pending in db.new and forbidden.call_count == 0
        record_property("chart_sql_selects", len(statements))
        record_property("chart_seconds", perf_counter() - start)
        record_property("chart_response_bytes", len(response.content))
        record_property("chart_max_sources", 12)
        db.rollback()
        # The scoped minimum-date predicate is removed after D4; the canonical reader is intact.
        original, _ = load_preferred_ohlcv_frames(
            db, "XLK", max_session=CUTOFF.latest_completed_session, as_of=CUTOFF.cutoff_at
        )
        assert len(original) == 320
    with rotation_engine.begin() as connection:
        connection.execute(
            UploadRun.__table__.insert().values(id=1, filename="unrelated.csv", status="UPLOADED")
        )
    app, client = client_for(rotation_engine)
    assert client.get(PATH).json() == payload
    assert forbidden.call_count == 0


def test_actual_chart_pit_revision_receipt_and_future_exclusion(rotation_engine, monkeypatch):
    seed_cache(rotation_engine)
    monkeypatch.setattr(routes, "MarketSectorChartService", chart_service)
    _, client = client_for(rotation_engine)
    before = client.get(PATH + "?symbols=XLE,XLK&horizon=21").json()
    assert before.get("cohort_status") == "COMPLETE", before
    with rotation_engine.begin() as connection:
        row = (
            connection.execute(
                select(PriceBar.__table__).where(
                    PriceBar.ticker == "XLE", PriceBar.bar_date == CUTOFF.latest_completed_session
                )
            )
            .mappings()
            .one()
        )
        connection.execute(
            PriceBar.__table__.update()
            .where(PriceBar.id == row["id"])
            .values(close=0, revision_count=1, revised_at=NOW + timedelta(minutes=1))
        )
        connection.execute(
            PriceBarRevision.__table__.insert().values(
                id=1,
                price_bar_id=row["id"],
                ticker="XLE",
                bar_date=row["bar_date"],
                timeframe="1 day",
                what_to_show="TRADES",
                revision_number=1,
                previous_data_hash="before",
                new_data_hash="after",
                previous_values_json={
                    key: str(row[key]) for key in ("open", "high", "low", "close", "volume")
                },
                new_values_json={"close": "0"},
                observed_at=NOW + timedelta(minutes=1),
            )
        )
        connection.execute(
            PriceBar.__table__.update()
            .where(PriceBar.ticker == "XLK", PriceBar.bar_date == CUTOFF.latest_completed_session)
            .values(first_seen_at=NOW + timedelta(minutes=1))
        )
        connection.execute(
            PriceBar.__table__.insert().values(
                id=99999,
                ticker="XLE",
                bar_date=CUTOFF.latest_completed_session + timedelta(days=1),
                timeframe="1 day",
                what_to_show="TRADES",
                source="SYNTHETIC",
                open=999,
                high=1000,
                low=998,
                close=999,
                volume=1e6,
                created_at=NOW - timedelta(minutes=5),
                first_seen_at=NOW - timedelta(minutes=5),
                last_seen_at=NOW - timedelta(minutes=5),
                revision_count=0,
            )
        )
    projected = client.get(PATH + "?symbols=XLE,XLK&horizon=21").json()
    assert projected["rows"][0]["returns"] == before["rows"][0]["returns"]
    assert projected["rows"][1]["status"] == "STALE"
    monkeypatch.setattr(
        routes,
        "MarketSectorChartService",
        lambda: chart_service(replace(CUTOFF, cutoff_at=NOW + timedelta(minutes=2))),
    )
    later = client.get(PATH + "?symbols=XLE,XLK&horizon=21").json()
    assert later["rows"][0]["returns"]["21"]["absolute_pct"] is None
    assert later["evidence_digest"] != projected["evidence_digest"]


def test_page_nav_validation_disabled_and_configuration_errors_are_sql_free(
    rotation_engine, monkeypatch
):
    app, client = client_for(rotation_engine)
    statements = []
    event.listen(
        rotation_engine, "before_cursor_execute", lambda _c, _s, sql, *_a: statements.append(sql)
    )
    response = client.get("/sector-rotation?etf=XLK&horizon=21&snapshot_id=99")
    assert response.status_code == 200, response.text
    assert response.text.count('href="/sector-rotation"') == 1
    assert "aria-current=page" in response.text and "Market Sector Intelligence" in response.text
    assert "No CSV or run required" in response.text and "not proprietary JdK RRG" in response.text
    assert "CHART_PRICE_HISTORY" not in response.text  # No fabricated server-rendered data.
    assert client.get("/settings").status_code == 200
    assert 'id="market-sector-heading"' in client.get("/settings").text
    for suffix in (
        "?horizon=7",
        "?symbols=QQQ",
        "?symbols=XLK,XLK",
        "?symbols=",
        "?symbols=XLK,",
        "?symbols=" + "X" * 129,
    ):
        assert client.get(PATH + suffix).status_code in (400, 422)
    for suffix in (
        "?etf=QQQ",
        "?etf=%3Cscript%3E",
        "?snapshot_id=-1",
        "?snapshot_id=9007199254740992",
        "?horizon=7",
        "?symbols=",
        "?mode=invalid",
    ):
        assert client.get("/sector-rotation" + suffix).status_code in (400, 422)
    monkeypatch.setattr(
        routes,
        "load_market_sector_intelligence_config",
        lambda: replace(load_market_sector_intelligence_config(), enabled=False),
    )
    disabled = client.get(PATH).json()
    assert disabled["cohort_status"] == "DISABLED" and len(disabled["rows"]) == 11
    assert statements == []
    from app.services.market_sector_intelligence_config import MarketSectorIntelligenceConfigError

    def invalid():
        raise MarketSectorIntelligenceConfigError("private filesystem or password")

    monkeypatch.setattr(routes, "load_market_sector_intelligence_config", invalid)
    monkeypatch.setattr(gui_routes, "load_market_sector_intelligence_config", invalid)
    assert client.get(PATH).status_code == 503
    shell = client.get("/sector-rotation")
    assert shell.status_code == 200 and "configuration is unavailable" in shell.text
    assert "private filesystem" not in shell.text + json.dumps(client.get(PATH).json())
    assert statements == []
