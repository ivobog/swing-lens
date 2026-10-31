import importlib.util
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from threading import Barrier

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from market_sector_rotation_helpers import (
    CUTOFF,
    NOW,
    complete_preview,
    loader_with,
    symbols,
    synthetic_frame,
)
from sqlalchemy import create_engine, event, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db import get_db
from app.main import create_app
from app.models.market_sector_intelligence_tables import MarketSectorRotationRow as Row
from app.models.market_sector_intelligence_tables import MarketSectorRotationSnapshot as Snapshot
from app.routers import market_sector_intelligence_routes as routes
from app.services.market_clock_service import MarketClockService
from app.services.market_sector_intelligence_capture_authority import MarketSectorCaptureAuthority
from app.services.market_sector_intelligence_config import load_market_sector_intelligence_config
from app.services.market_sector_intelligence_rotation_repository import (
    MarketSectorRotationRepository,
)
from app.services.market_sector_intelligence_rotation_service import MarketSectorRotationService
from app.services.runtime_mutation_authority import (
    RuntimeMutationAuthority,
    RuntimeMutationAuthorityError,
)
from app.settings import ProcessRole, RuntimeMode, Settings
from scripts.market_sector_intelligence_capture import run_command


def revision():
    path = Path("alembic/versions/20261022_0093_market_sector_rotation.py")
    spec = importlib.util.spec_from_file_location("rotation_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def apply_migration(engine, direction="upgrade"):
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            getattr(revision(), direction)()


@pytest.fixture
def rotation_engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'rotation.sqlite3').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE legacy_marker (id INTEGER PRIMARY KEY, value TEXT)"
        )
        connection.exec_driver_sql("INSERT INTO legacy_marker VALUES (1, 'unchanged')")
    apply_migration(engine)
    yield engine
    engine.dispose()


def authority():
    return MarketSectorCaptureAuthority.from_settings(
        Settings(
            _env_file=None,
            runtime_mode=RuntimeMode.NORMAL,
            process_role=ProcessRole.CLI_OR_MAINTENANCE,
        )
    )


def capture(engine, preview=None, *, now=NOW):
    with Session(engine) as db:
        return MarketSectorRotationRepository(now=lambda: now).capture(
            db, preview or complete_preview(), authority=authority()
        )


def counts(engine):
    with Session(engine) as db:
        return db.scalar(select(func.count()).select_from(Snapshot)), db.scalar(
            select(func.count()).select_from(Row)
        )


def test_additive_migration_roundtrip_preserves_old_tables(rotation_engine):
    inspector = inspect(rotation_engine)
    assert set(inspector.get_table_names()) == {
        "legacy_marker",
        Snapshot.__tablename__,
        Row.__tablename__,
    }
    assert {c["name"] for c in inspector.get_columns(Snapshot.__tablename__)} == set(
        Snapshot.__table__.columns.keys()
    )
    assert {c["name"] for c in inspector.get_columns(Row.__tablename__)} == set(
        Row.__table__.columns.keys()
    )
    assert revision().down_revision == "0092_phase2_eligibility_ledgers"
    assert inspector.get_foreign_keys(Row.__tablename__)[0]["options"]["ondelete"] == "RESTRICT"
    apply_migration(rotation_engine, "downgrade")
    assert inspect(rotation_engine).get_table_names() == ["legacy_marker"]
    apply_migration(rotation_engine)
    with rotation_engine.connect() as connection:
        assert connection.scalar(text("SELECT value FROM legacy_marker")) == "unchanged"


def test_complete_capture_is_atomic_and_exact_retry_has_no_dml(rotation_engine):
    statements, commits = [], []
    event.listen(
        rotation_engine, "before_cursor_execute", lambda _c, _s, sql, *_a: statements.append(sql)
    )
    event.listen(rotation_engine, "commit", lambda _c: commits.append(1))
    first = complete_preview()
    result = capture(rotation_engine, first)
    assert result["status"] == "CREATED" and counts(rotation_engine) == (1, 11)
    assert len(commits) == 1
    assert (
        sum(sql.startswith("INSERT INTO market_sector_rotation_snapshots") for sql in statements)
        == 1
    )
    assert (
        sum(sql.startswith("INSERT INTO market_sector_rotation_rows") for sql in statements) == 11
    )
    statements.clear()
    later = replace(CUTOFF, cutoff_at=CUTOFF.cutoff_at + timedelta(hours=1))
    second = MarketSectorRotationService(frame_loader=loader_with()).preview(object(), cutoff=later)
    retry = capture(rotation_engine, second, now=NOW + timedelta(hours=1))
    assert retry["status"] == "ALREADY_EXISTS" and retry["snapshot_id"] == result["snapshot_id"]
    assert not any(
        sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for sql in statements
    )


def test_partial_capture_writes_nothing(rotation_engine):
    frame = synthetic_frame("XLK").iloc[:0]
    partial = MarketSectorRotationService(frame_loader=loader_with({"XLK": frame})).preview(
        object(), cutoff=CUTOFF
    )
    assert partial["eligible_count"] == 10
    outcome = capture(rotation_engine, partial)
    assert outcome["status"] == "NOT_READY" and counts(rotation_engine) == (0, 0)


def test_injected_row_failure_rolls_back_header_and_every_row(rotation_engine):
    def fail_row(_mapper, _connection, target):
        if target.etf_symbol == "XLE":
            raise RuntimeError("injected fixture failure")

    event.listen(Row, "before_insert", fail_row)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            capture(rotation_engine)
    finally:
        event.remove(Row, "before_insert", fail_row)
    assert counts(rotation_engine) == (0, 0)


def test_changed_evidence_conflicts_and_saved_history_stays_frozen(rotation_engine):
    first = capture(rotation_engine)
    with Session(rotation_engine) as db:
        saved = MarketSectorRotationRepository().detail(db, first["snapshot_id"])
    changed = synthetic_frame("XLK")
    changed.loc[changed.index[-1], "close"] = 125.0
    revised = MarketSectorRotationService(frame_loader=loader_with({"XLK": changed})).preview(
        object(), cutoff=CUTOFF
    )
    assert capture(rotation_engine, revised)["status"] == "EVIDENCE_CONFLICT"
    with Session(rotation_engine) as db:
        assert MarketSectorRotationRepository().detail(db, first["snapshot_id"]) == saved
    assert counts(rotation_engine) == (1, 11)


def test_concurrent_captures_share_one_snapshot(rotation_engine):
    payload = complete_preview()
    start = Barrier(2)

    def invoke(_):
        start.wait(timeout=5)
        return capture(rotation_engine, payload)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(invoke, range(2)))
    assert {result["status"] for result in results} == {"CREATED", "ALREADY_EXISTS"}
    assert len({result["snapshot_id"] for result in results}) == 1
    assert counts(rotation_engine) == (1, 11)


def test_orm_insert_guard_and_database_immutability(rotation_engine):
    with Session(rotation_engine) as db:
        db.add(Snapshot(market_session=CUTOFF.latest_completed_session))
        with pytest.raises(ValueError, match="CAPTURE_TRANSACTION_REQUIRED"):
            db.flush()
        db.rollback()
    result = capture(rotation_engine)
    with Session(rotation_engine) as db:
        header = db.get(Snapshot, result["snapshot_id"])
        header.eligible_count = 10
        with pytest.raises(ValueError, match="IMMUTABLE"):
            db.flush()
        db.rollback()
    for table in (Snapshot.__tablename__, Row.__tablename__):
        for action in (f"UPDATE {table} SET id=id", f"DELETE FROM {table}"):
            with (
                pytest.raises(IntegrityError, match="IMMUTABLE"),
                rotation_engine.begin() as connection,
            ):
                connection.exec_driver_sql(action)
    assert counts(rotation_engine) == (1, 11)


def test_database_constraints_and_natural_key(rotation_engine):
    capture(rotation_engine)
    with rotation_engine.connect() as connection:
        header = dict(connection.execute(Snapshot.__table__.select()).mappings().one())
        row = dict(connection.execute(Row.__table__.select().limit(1)).mappings().one())
    header.pop("id")
    row.pop("id")
    with pytest.raises(IntegrityError), rotation_engine.begin() as connection:
        connection.execute(Snapshot.__table__.insert().values(**header))
    for change in (
        {"rank": 12},
        {"quality_status": "INSUFFICIENT_DATA"},
        {"rs_63_pct": float("inf")},
        {"benchmark_price_basis": "ADJUSTED_LAST"},
        {"snapshot_id": 99999},
    ):
        with pytest.raises(IntegrityError), rotation_engine.begin() as connection:
            connection.execute(Row.__table__.insert().values(**{**row, **change}))


@pytest.mark.parametrize(
    "fault", ["authority", "web", "worker", "certification", "operation", "tamper", "backdate"]
)
def test_capture_authority_and_material_cannot_be_bypassed(rotation_engine, fault):
    payload = complete_preview()
    with Session(rotation_engine) as db:
        with pytest.raises((ValueError, RuntimeMutationAuthorityError)):
            if fault in {"web", "worker"}:
                MarketSectorCaptureAuthority.from_settings(
                    Settings(
                        _env_file=None,
                        process_role=ProcessRole.WEB
                        if fault == "web"
                        else ProcessRole.DURABLE_WORKER,
                    )
                )
            elif fault == "certification":
                MarketSectorCaptureAuthority(
                    RuntimeMutationAuthority.certification(
                        "market_sector_intelligence.capture", session_id="fixture"
                    ),
                    ProcessRole.CLI_OR_MAINTENANCE,
                )
            elif fault == "operation":
                MarketSectorCaptureAuthority(
                    RuntimeMutationAuthority.normal("unrelated"), ProcessRole.CLI_OR_MAINTENANCE
                )
            else:
                if fault == "tamper":
                    payload["sectors"][0]["rs_63_pct"] = 99
                repo = MarketSectorRotationRepository(
                    now=lambda: NOW + timedelta(days=2) if fault == "backdate" else NOW
                )
                repo.capture(db, payload, authority=None if fault == "authority" else authority())
    assert counts(rotation_engine) == (0, 0)


def test_cli_dry_run_and_capture_have_separate_transactions(rotation_engine):
    class FrozenService:
        def preview(self, _db):
            return complete_preview()

    factory = sessionmaker(rotation_engine)
    settings = Settings(
        _env_file=None, runtime_mode=RuntimeMode.NORMAL, process_role=ProcessRole.CLI_OR_MAINTENANCE
    )
    result = run_command(
        capture=False, settings=settings, session_factory=factory, service=FrozenService()
    )
    assert result["status"] == "DRY_RUN" and counts(rotation_engine) == (0, 0)
    result = run_command(
        capture=True,
        settings=settings,
        session_factory=factory,
        service=FrozenService(),
        repository=MarketSectorRotationRepository(now=lambda: NOW),
    )
    assert result["status"] == "CREATED" and counts(rotation_engine) == (1, 11)


def test_historical_api_is_stored_only_bounded_and_select_only(rotation_engine, monkeypatch):
    result = capture(rotation_engine)
    app = create_app()

    def database():
        with Session(rotation_engine) as db:
            yield db

    app.dependency_overrides[get_db] = database

    def forbidden():
        raise AssertionError("historical reads must not calculate")

    monkeypatch.setattr(routes, "MarketSectorRotationService", forbidden)
    sql = []
    event.listen(
        rotation_engine,
        "before_cursor_execute",
        lambda _c, _s, statement, *_a: sql.append(statement),
    )
    client = TestClient(app)
    base = "/api/market-sector-intelligence/rotation"
    latest = client.get(base + "/latest")
    assert latest.status_code == 200 and latest.json()["source_type"] == "PERSISTED"
    assert len(sql) == 2
    detail = client.get(base + f"/snapshots/{result['snapshot_id']}")
    assert detail.json() == latest.json()
    assert len(client.get(base + "/history?limit=1").json()) == 1
    assert client.get(base + "/history?limit=0").status_code == 422
    assert client.get(base + "/history?limit=101").status_code == 422
    assert client.get(base + "/snapshots/999").status_code == 404
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in sql)


def test_empty_history_is_explicit(rotation_engine):
    app = create_app()

    def database():
        with Session(rotation_engine) as db:
            yield db

    app.dependency_overrides[get_db] = database
    client = TestClient(app)
    base = "/api/market-sector-intelligence/rotation"
    assert client.get(base + "/latest").json()["detail"]["code"] == "NO_ROTATION_SNAPSHOT"
    assert client.get(base + "/history").json() == []


def test_history_is_bounded_descending_and_does_not_fill_uncaptured_sessions(rotation_engine):
    for delta in (0, 2, 5):
        now = NOW + timedelta(days=delta)
        cutoff = MarketClockService().cutoff_for(now, reason="SYNTHETIC_HISTORY")
        frames = {
            ticker: synthetic_frame(ticker, end=cutoff.latest_completed_session)
            for ticker in symbols()
        }
        preview = MarketSectorRotationService(frame_loader=loader_with(frames)).preview(
            object(), cutoff=cutoff
        )
        assert capture(rotation_engine, preview, now=now)["status"] == "CREATED"
    with Session(rotation_engine) as db:
        result = MarketSectorRotationRepository().history(db, limit=2)
    assert [item["market_session"] for item in result] == ["2026-10-26", "2026-10-23"]
    assert counts(rotation_engine) == (3, 33)


def test_explicit_cli_capture_refuses_disabled_feature_without_reads_or_writes(rotation_engine):
    class DisabledService:
        def preview(self, db):
            return MarketSectorRotationService().preview(
                db,
                cutoff=CUTOFF,
                config=replace(load_market_sector_intelligence_config(), enabled=False),
            )

    sql = []
    event.listen(
        rotation_engine,
        "before_cursor_execute",
        lambda _c, _s, statement, *_a: sql.append(statement),
    )
    result = run_command(
        capture=True,
        settings=Settings(_env_file=None),
        session_factory=sessionmaker(rotation_engine),
        service=DisabledService(),
    )
    assert result["status"] == "NOT_READY" and sql == []
    assert counts(rotation_engine) == (0, 0)
