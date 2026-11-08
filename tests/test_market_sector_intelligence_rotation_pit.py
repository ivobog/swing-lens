import json
import tracemalloc
from dataclasses import replace
from datetime import timedelta
from time import perf_counter
from unittest.mock import Mock

from fastapi.testclient import TestClient
from market_sector_rotation_helpers import ANCHORS, CUTOFF, NOW, symbols, synthetic_frame
from sqlalchemy import JSON, Column, MetaData, Table, event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session, sessionmaker
from test_market_sector_intelligence_rotation_storage import capture
from test_market_sector_intelligence_rotation_storage import rotation_engine as rotation_engine

from app.db import get_db
from app.main import create_app
from app.models.tables import PriceBar, PriceBarRevision, UploadRun
from app.routers import market_sector_intelligence_routes as routes
from app.services.market_sector_intelligence_config import load_market_sector_intelligence_config
from app.services.market_sector_intelligence_rotation_repository import (
    MarketSectorRotationRepository,
)
from app.services.market_sector_intelligence_rotation_service import MarketSectorRotationService
from app.settings import Settings
from scripts.market_sector_intelligence_capture import run_command


def seed_cache(engine):
    for model in (PriceBar, PriceBarRevision, UploadRun):
        table = Table(
            model.__tablename__,
            MetaData(),
            *[
                Column(
                    column.name,
                    JSON() if isinstance(column.type, JSONB) else column.type,
                    primary_key=column.primary_key,
                )
                for column in model.__table__.columns
            ],
        )
        table.create(engine)
    rows = []
    for ticker in symbols():
        for _, data in synthetic_frame(ticker).iterrows():
            rows.append(
                dict(
                    id=len(rows) + 1,
                    ticker=ticker,
                    bar_date=data["date"],
                    timeframe="1 day",
                    what_to_show="TRADES",
                    source="SYNTHETIC",
                    close=data["close"],
                    open=data["close"],
                    high=data["close"],
                    low=data["close"],
                    volume=1000,
                    created_at=NOW - timedelta(minutes=5),
                    first_seen_at=NOW - timedelta(minutes=5),
                    last_seen_at=NOW - timedelta(minutes=5),
                    revision_count=0,
                )
            )
    with engine.begin() as connection:
        connection.execute(PriceBar.__table__.insert(), rows)


def service():
    clock = Mock()
    clock.cutoff_for.return_value = CUTOFF
    return MarketSectorRotationService(clock=clock)


def test_actual_pit_preview_http_dry_run_and_upload_independence(
    rotation_engine, monkeypatch, record_property
):
    seed_cache(rotation_engine)
    forbidden = Mock(side_effect=AssertionError("D2 must not acquire or queue"))
    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("ib_insync.IB.connect", forbidden)
    monkeypatch.setattr("app.services.background_job_service.enqueue_job", forbidden)
    app = create_app()

    def database():
        with Session(rotation_engine) as db:
            yield db

    app.dependency_overrides[get_db] = database
    monkeypatch.setattr(routes, "MarketSectorRotationService", service)
    statements = []
    event.listen(
        rotation_engine, "before_cursor_execute", lambda _c, _s, sql, *_a: statements.append(sql)
    )
    tracemalloc.start()
    start = perf_counter()
    response = TestClient(app).get("/api/market-sector-intelligence/rotation/preview")
    elapsed = perf_counter() - start
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert response.status_code == 200 and response.json()["cohort_status"] == "COMPLETE"
    assert len(statements) == 1
    assert all(
        sql.lstrip().upper().startswith("SELECT") and "price_bars" in sql for sql in statements
    )
    record_property("preview_sql_selects", len(statements))
    record_property("preview_seconds_with_tracemalloc", elapsed)
    record_property("preview_python_peak_bytes", peak)
    record_property("synthetic_preview_json_bytes", len(response.content))
    statements.clear()
    dry = run_command(
        capture=False,
        settings=Settings(_env_file=None),
        session_factory=sessionmaker(rotation_engine),
        service=service(),
    )
    assert dry["preview"] == response.json() and len(statements) == 1
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
    with rotation_engine.begin() as connection:
        connection.execute(
            UploadRun.__table__.insert(),
            [
                dict(id=1, filename="first.csv", status="UPLOADED"),
                dict(id=2, filename="unrelated.csv", status="UPLOADED"),
            ],
        )
    statements.clear()
    assert (
        TestClient(app).get("/api/market-sector-intelligence/rotation/preview").json()
        == response.json()
    )
    assert len(statements) == 1 and all("upload_runs" not in sql for sql in statements)
    assert forbidden.call_count == 0


def test_actual_loader_late_receipt_future_bar_and_revision_projection(rotation_engine):
    seed_cache(rotation_engine)
    with rotation_engine.begin() as connection:
        connection.execute(
            PriceBar.__table__.update()
            .where(PriceBar.ticker == "XLF", PriceBar.bar_date == ANCHORS[0])
            .values(first_seen_at=NOW + timedelta(seconds=1))
        )
        key = connection.execute(
            select(PriceBar.id).where(PriceBar.ticker == "XLE", PriceBar.bar_date == ANCHORS[0])
        ).scalar_one()
        connection.execute(
            PriceBar.__table__.update()
            .where(PriceBar.id == key)
            .values(close=0, revised_at=NOW + timedelta(minutes=1), revision_count=1)
        )
        connection.execute(
            PriceBarRevision.__table__.insert().values(
                id=1,
                price_bar_id=key,
                ticker="XLE",
                bar_date=ANCHORS[0],
                timeframe="1 day",
                what_to_show="TRADES",
                revision_number=1,
                new_data_hash="after",
                previous_data_hash="before",
                previous_values_json=dict(
                    open="120", high="120", low="120", close="120", volume="1000"
                ),
                new_values_json=dict(close="0"),
                observed_at=NOW + timedelta(minutes=1),
            )
        )
        connection.execute(
            PriceBar.__table__.insert().values(
                id=99999,
                ticker="XLK",
                bar_date=ANCHORS[0] + timedelta(days=1),
                timeframe="1 day",
                what_to_show="TRADES",
                source="SYNTHETIC",
                close=999,
                created_at=NOW - timedelta(minutes=5),
                first_seen_at=NOW - timedelta(minutes=5),
                last_seen_at=NOW - timedelta(minutes=5),
                revision_count=0,
            )
        )
    with Session(rotation_engine) as db:
        result = service().preview(db)
    rows = {row["etf_symbol"]: row for row in result["sectors"]}
    assert result["eligible_count"] == 10 and rows["XLF"]["reasons"] == ["STALE"]
    assert rows["XLE"]["quality_status"] == rows["XLK"]["quality_status"] == "ELIGIBLE"
    assert rows["XLE"]["anchor_evidence"]["0"]["close"] == 120
    assert rows["XLK"]["anchor_evidence"]["0"]["close"] == 120
    with Session(rotation_engine) as db:
        assert db.scalar(select(PriceBar.close).where(PriceBar.id == key)) == 0


def test_saved_actual_cache_snapshot_does_not_change_after_price_mutation(rotation_engine):
    seed_cache(rotation_engine)
    with Session(rotation_engine) as db:
        preview = service().preview(db)
    first = capture(rotation_engine, preview)
    with Session(rotation_engine) as db:
        saved = MarketSectorRotationRepository().detail(db, first["snapshot_id"])
    with rotation_engine.begin() as connection:
        connection.execute(
            PriceBar.__table__.update()
            .where(PriceBar.ticker == "XLK", PriceBar.bar_date == ANCHORS[0])
            .values(close=125)
        )
    with Session(rotation_engine) as db:
        revised = service().preview(db)
        assert MarketSectorRotationRepository().detail(db, first["snapshot_id"]) == saved
    assert revised["snapshot_digest"] != preview["snapshot_digest"]
    assert capture(rotation_engine, revised)["status"] == "EVIDENCE_CONFLICT"
    assert json.dumps(saved, allow_nan=False)


def test_empty_cache_disabled_preview_and_pending_changes_are_read_only(
    rotation_engine, monkeypatch
):
    seed_cache(rotation_engine)
    with rotation_engine.begin() as connection:
        connection.execute(PriceBar.__table__.delete())
    statements = []
    event.listen(
        rotation_engine, "before_cursor_execute", lambda _c, _s, sql, *_a: statements.append(sql)
    )
    with Session(rotation_engine, autoflush=True) as db:
        pending = UploadRun(id=99, filename="pending.csv", status="UPLOADED")
        db.add(pending)
        result = service().preview(db)
        assert result["cohort_status"] == "UNAVAILABLE" and len(result["sectors"]) == 11
        assert all(row["rs_63_pct"] is None for row in result["sectors"])
        assert pending in db.new
        db.rollback()
    assert len(statements) == 1 and all(sql.startswith("SELECT") for sql in statements)
    app = create_app()

    def database():
        with Session(rotation_engine) as db:
            yield db

    app.dependency_overrides[get_db] = database
    monkeypatch.setattr(
        routes,
        "load_market_sector_intelligence_config",
        lambda: replace(load_market_sector_intelligence_config(), enabled=False),
    )
    statements.clear()
    payload = TestClient(app).get("/api/market-sector-intelligence/rotation/preview").json()
    assert payload["cohort_status"] == "DISABLED" and statements == []
