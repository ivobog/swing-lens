import json
from dataclasses import replace
from datetime import timedelta
from threading import Event, Thread
from time import perf_counter
from unittest.mock import Mock

import psutil
from fastapi.testclient import TestClient
from market_sector_technical_helpers import CUTOFF, NOW, frame, symbols
from sqlalchemy import JSON, Column, MetaData, Table, event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session
from test_market_sector_intelligence_rotation_storage import rotation_engine as rotation_engine

from app.db import get_db
from app.main import create_app
from app.models.tables import PriceBar, PriceBarRevision, UploadRun
from app.routers import market_sector_intelligence_routes as routes
from app.services.market_sector_intelligence_config import (
    MarketSectorIntelligenceConfigError,
    load_market_sector_intelligence_config,
)
from app.services.market_sector_intelligence_technical_service import MarketSectorTechnicalService
from app.settings import Settings

BASE = "/api/market-sector-intelligence/technicals/preview"


def test_validation_preserves_complete_receipts_and_caller_frame_ownership():
    from copy import deepcopy

    from market_sector_technical_helpers import receipt

    from app.services.market_sector_intelligence_technical_service import _validate_frame

    source = receipt(
        frame("XLB", count=756),
        "XLB",
        {"as_of": CUTOFF.cutoff_at, "max_session": CUTOFF.latest_completed_session},
    )
    before = deepcopy(source)
    normalized, material = _validate_frame(source, "XLB", "TRADES", "1 day", CUTOFF)
    assert source.equals(before) and source.attrs == before.attrs
    assert normalized.attrs == before.attrs
    assert len(material["bars"]) == 756
    assert [row["source_fingerprint"] for row in material["bars"]] == [
        row["fingerprint"] for row in before.attrs["pit_source_manifest"]["states"]
    ]
    normalized.loc[0, "open"] += 0.01
    normalized.attrs["pit_source_manifest"]["states"][0]["fingerprint"] = "changed by caller"
    assert source.equals(before) and source.attrs == before.attrs


def test_direct_fingerprints_match_reuse_and_restore_scope_on_error(rotation_engine):
    from app.services.market_sector_intelligence_read_window import batch_cached_read
    from app.services.price_bar_repository import (
        _immutable_fingerprint,
        load_preferred_ohlcv_frames,
    )

    seed_cache(rotation_engine)
    _immutable_fingerprint.cache_clear()
    try:
        with Session(rotation_engine) as db:

            def read():
                return load_preferred_ohlcv_frames(
                    db, "XLB", as_of=CUTOFF.cutoff_at, max_session=CUTOFF.latest_completed_session
                )[0]

            try:
                with batch_cached_read(
                    db, ["XLB"], timeframe="1 day", cutoff=CUTOFF, reuse_fingerprints=False
                ):
                    direct = read()
                    assert _immutable_fingerprint.cache_info().currsize == 0
                    raise RuntimeError("abandoned panel")
            except RuntimeError as error:
                assert str(error) == "abandoned panel"
            reused = read()
            assert direct.equals(reused) and direct.attrs == reused.attrs
            assert _immutable_fingerprint.cache_info().currsize > 0
    finally:
        _immutable_fingerprint.cache_clear()


def seed_cache(engine):
    for model in (PriceBar, PriceBarRevision, UploadRun):
        Table(
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
        ).create(engine)
    rows = []
    for ticker in symbols() + ["SPY", "QQQ"]:
        for _, bar in frame(ticker).iterrows():
            rows.append(
                dict(
                    id=len(rows) + 1,
                    ticker=ticker,
                    bar_date=bar["date"],
                    timeframe="1 day",
                    what_to_show="TRADES",
                    source="SYNTHETIC",
                    **{
                        column: float(bar[column])
                        for column in ("open", "high", "low", "close", "volume")
                    },
                    created_at=NOW - timedelta(minutes=5),
                    first_seen_at=NOW - timedelta(minutes=5),
                    last_seen_at=NOW - timedelta(minutes=5),
                    revision_count=0,
                )
            )
    with engine.begin() as connection:
        connection.execute(PriceBar.__table__.insert(), rows)


def service(cutoff=CUTOFF):
    clock = Mock()
    clock.cutoff_for.return_value = cutoff
    return MarketSectorTechnicalService(clock=clock)


def client_for(engine):
    app = create_app(Settings(_env_file=None, job_worker_enabled=False))

    def database():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = database
    return app, TestClient(app)


def test_real_cached_http_is_read_only_full_cohort_detail_and_upload_independent(
    rotation_engine,
    monkeypatch,
    record_property,
):
    seed_cache(rotation_engine)
    forbidden = Mock(side_effect=AssertionError("No D3 mutation, pipeline or provider work"))
    for name in (
        "socket.create_connection",
        "ib_insync.IB.connect",
        "app.services.background_job_service.enqueue_job",
        "app.services.market_calculation_context_service.create_pipeline_market_context",
        "app.services.technical_artifact_cache.upsert_local_artifact",
        "app.services.technical_score_service.build_technical_score",
        "app.services.technical_score_service._technical_v5_run_context",
    ):
        monkeypatch.setattr(name, forbidden)
    monkeypatch.setattr(routes, "MarketSectorTechnicalService", service)
    app, client = client_for(rotation_engine)
    statements = []
    event.listen(
        rotation_engine, "before_cursor_execute", lambda _c, _s, sql, *_a: statements.append(sql)
    )
    with Session(rotation_engine, autoflush=True) as db:
        pending = UploadRun(id=99, filename="pending.csv", status="UPLOADED")
        db.add(pending)

        def pending_database():
            yield db

        app.dependency_overrides[get_db] = pending_database
        process = psutil.Process()
        baseline_rss = process.memory_info().rss
        samples = [baseline_rss]
        finished = Event()

        def sample_rss():
            while not finished.wait(0.05):
                samples.append(process.memory_info().rss)

        sampler = Thread(target=sample_rss, daemon=True)
        sampler.start()
        start = perf_counter()
        try:
            response = client.get(BASE)
        finally:
            finished.set()
            sampler.join()
        elapsed = perf_counter() - start
        peak_rss = max(*samples, process.memory_info().rss)
        assert response.status_code == 200
        payload = response.json()
        assert payload["v4_ready_count"] == payload["v5_ready_count"] == 11
        assert len(statements) == 1
        assert all(
            sql.lstrip().upper().startswith("SELECT") and "price_bars" in sql for sql in statements
        )
        assert pending in db.new
        assert forbidden.call_count == 0
        record_property("preview_sql_selects", len(statements))
        record_property("preview_seconds", elapsed)
        record_property("preview_process_baseline_rss_bytes", baseline_rss)
        record_property("preview_process_sampled_peak_rss_bytes", peak_rss)
        record_property("preview_json_bytes", len(response.content))
        record_property(
            "synthetic_first_scores",
            json.dumps(
                {
                    "etf": payload["rows"][0]["etf_symbol"],
                    "v4": payload["rows"][0]["v4"]["final_v4_score"],
                    "v5": payload["rows"][0]["v5"]["technical_composite_score"],
                    "adjusted": payload["rows"][0]["v5"]["confidence_adjusted_score"],
                    "delta": payload["rows"][0]["comparison"]["raw_score_delta"],
                }
            ),
        )
        db.rollback()
    with rotation_engine.begin() as connection:
        connection.execute(
            UploadRun.__table__.insert(),
            [
                dict(id=1, filename="arbitrary.csv", status="UPLOADED"),
                dict(id=2, filename="unrelated.csv", status="UPLOADED"),
            ],
        )
    app, client = client_for(rotation_engine)
    statements.clear()
    assert client.get(BASE).json() == payload
    assert len(statements) == 1 and all("upload_runs" not in sql for sql in statements)
    statements.clear()
    detail = client.get(BASE + "/xlk")
    assert detail.status_code == 200
    assert detail.json()["row"] == next(
        row for row in payload["rows"] if row["etf_symbol"] == "XLK"
    )
    assert detail.json()["evaluation_digest"] == payload["evaluation_digest"]
    assert len(statements) == 1  # detail really evaluates all eleven members
    statements.clear()
    assert client.get(BASE + "/MSFT").status_code == 404
    assert client.get(BASE + "/QQQ").json()["detail"]["code"] == "UNSUPPORTED_SECTOR_ETF"
    assert statements == [] and forbidden.call_count == 0


def test_actual_pit_loader_replays_late_revision_excludes_future_and_late_receipt(
    rotation_engine, monkeypatch
):
    seed_cache(rotation_engine)
    with Session(rotation_engine) as db:
        before = service().preview(db)
    with rotation_engine.begin() as connection:
        connection.execute(
            PriceBar.__table__.update()
            .where(
                PriceBar.ticker == "XLK",
                PriceBar.bar_date == CUTOFF.latest_completed_session,
            )
            .values(first_seen_at=NOW + timedelta(seconds=1))
        )
        current = (
            connection.execute(
                PriceBar.__table__.select().where(
                    PriceBar.ticker == "XLE",
                    PriceBar.bar_date == CUTOFF.latest_completed_session,
                )
            )
            .mappings()
            .one()
        )
        connection.execute(
            PriceBar.__table__.update()
            .where(PriceBar.id == current["id"])
            .values(
                close=0,
                revision_count=1,
                revised_at=NOW + timedelta(minutes=1),
            )
        )
        connection.execute(
            PriceBarRevision.__table__.insert().values(
                id=1,
                price_bar_id=current["id"],
                ticker="XLE",
                bar_date=CUTOFF.latest_completed_session,
                timeframe="1 day",
                what_to_show="TRADES",
                revision_number=1,
                previous_data_hash="before",
                new_data_hash="after",
                previous_values_json={
                    k: str(current[k]) for k in ("open", "high", "low", "close", "volume")
                },
                new_values_json={"close": "0"},
                observed_at=NOW + timedelta(minutes=1),
            )
        )
        connection.execute(
            PriceBar.__table__.insert().values(
                id=99999,
                ticker="XLU",
                bar_date=CUTOFF.latest_completed_session + timedelta(days=1),
                timeframe="1 day",
                what_to_show="TRADES",
                source="SYNTHETIC",
                open=999,
                high=1000,
                low=998,
                close=999,
                volume=2_000_000,
                created_at=NOW - timedelta(minutes=5),
                first_seen_at=NOW - timedelta(minutes=5),
                last_seen_at=NOW - timedelta(minutes=5),
                revision_count=0,
            )
        )
    with Session(rotation_engine) as db:
        projected = service().preview(db)
        assert db.scalar(select(PriceBar.close).where(PriceBar.id == current["id"])) == 0
    original = {row["etf_symbol"]: row for row in before["rows"]}
    rows = {row["etf_symbol"]: row for row in projected["rows"]}
    assert projected["v4_ready_count"] == 10 and projected["v5_ready_count"] == 0
    assert rows["XLK"]["data_reasons"] == ["STALE"]
    assert rows["XLE"]["v4"] == original["XLE"]["v4"]
    assert rows["XLU"]["v4"] == original["XLU"]["v4"]
    later = replace(CUTOFF, cutoff_at=NOW + timedelta(minutes=2))
    monkeypatch.setattr(routes, "MarketSectorTechnicalService", lambda: service(later))
    _, client = client_for(rotation_engine)
    response = client.get(BASE)
    assert response.status_code == 200 and len(response.json()["rows"]) == 11
    revised = response.json()
    assert next(row for row in revised["rows"] if row["etf_symbol"] == "XLE")["data_reasons"] == [
        "INVALID_OHLCV"
    ]
    assert revised["evaluation_digest"] != projected["evaluation_digest"]


def test_disabled_http_and_invalid_configuration_issue_zero_sql(rotation_engine, monkeypatch):
    app, client = client_for(rotation_engine)
    sql = []
    event.listen(
        rotation_engine,
        "before_cursor_execute",
        lambda _c, _s, statement, *_a: sql.append(statement),
    )
    config = replace(load_market_sector_intelligence_config(), enabled=False)
    monkeypatch.setattr(routes, "load_market_sector_intelligence_config", lambda: config)
    response = client.get(BASE)
    assert response.status_code == 200 and response.json()["cohort_status"] == "DISABLED"
    assert len(response.json()["rows"]) == 11 and sql == []

    def invalid():
        raise MarketSectorIntelligenceConfigError("private configuration location")

    monkeypatch.setattr(routes, "load_market_sector_intelligence_config", invalid)
    response = client.get(BASE)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "MARKET_SECTOR_CONFIGURATION_INVALID"
    assert "private" not in response.text and sql == []
