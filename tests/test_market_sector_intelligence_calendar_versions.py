"""Frozen calendar contracts across real MSI readers, capture and native readiness."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from threading import Barrier
from unittest.mock import Mock

import pandas as pd
import pytest
from market_sector_rotation_helpers import loader_with, synthetic_frame
from market_sector_rotation_helpers import symbols as rotation_symbols
from market_sector_technical_helpers import frame, receipt, symbols
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from test_market_sector_intelligence_rotation_storage import (
    apply_migration,
    authority,
    counts,
)
from test_market_sector_intelligence_rotation_storage import (
    rotation_engine as rotation_engine,
)
from test_market_sector_intelligence_technical_pit import seed_cache

from app.models.tables import MarketCalculationContext, PriceBar
from app.services.market_calculation_context_service import cutoff_from_row
from app.services.market_clock_service import MarketClockService, clock_for_cutoff
from app.services.market_sector_intelligence_chart_service import (
    MarketSectorChartService,
    chart_sessions,
)
from app.services.market_sector_intelligence_coverage import MarketSectorIntelligenceCoverageService
from app.services.market_sector_intelligence_rotation_repository import (
    MarketSectorRotationRepository,
)
from app.services.market_sector_intelligence_rotation_service import (
    LEGACY_MATERIAL_IDENTITY,
    MATERIAL_IDENTITY,
    MarketSectorRotationService,
    snapshot_digest,
)
from app.services.market_sector_intelligence_technical_service import MarketSectorTechnicalService
from app.services.us_market_calendar import (
    CALENDAR_VERSION,
    LEGACY_CALENDAR_VERSION,
    subtract_us_trading_sessions,
)

NOW = datetime(2025, 1, 10, 22, tzinfo=UTC)
TARGET = date(2025, 1, 10)
VERSIONS = (LEGACY_CALENDAR_VERSION, CALENDAR_VERSION)


def frozen(version, *, instant=NOW, context_id=17):
    cutoff = MarketClockService(calendar_version=version).cutoff_for(
        instant, reason="CP_R01_ACTUAL_CONTEXT"
    )
    row = MarketCalculationContext(
        id=context_id,
        cutoff_at=cutoff.cutoff_at,
        exchange_timezone=cutoff.exchange_timezone,
        latest_completed_session=cutoff.latest_completed_session,
        daily_bar_ready_at=cutoff.daily_bar_ready_at,
        calendar_version=cutoff.calendar_version,
        bar_readiness_version=cutoff.bar_readiness_version,
        cutoff_reason=cutoff.cutoff_reason,
    )
    result = cutoff_from_row(row)
    assert row.calendar_version == result.calendar_version == version
    return result


def golden_frames():
    # Reproduce the unmodified D4/v1 synthetic anchor oracle. Never supply a
    # January 9 bar: it is not needed for January 10's v1 anchor calculations.
    days = [
        subtract_us_trading_sessions(TARGET, offset, calendar_version=LEGACY_CALENDAR_VERSION)
        for offset in reversed(range(65))
    ]
    result = {}
    for ticker in rotation_symbols():
        f = pd.DataFrame({"date": days, "close": [100.0] * len(days)})
        values = (110, 108, 105, 102, 100) if ticker == "SPY" else (120, 115, 110, 105, 100)
        for offset, value in zip((0, 5, 21, 42, 63), values, strict=True):
            day = subtract_us_trading_sessions(
                TARGET, offset, calendar_version=LEGACY_CALENDAR_VERSION
            )
            f.loc[f.date == day, "close"] = float(value)
        f = f[f.date != date(2025, 1, 9)].copy()
        f.attrs["price_basis"] = "TRADES"
        result[ticker] = f
    return result


def rotation(version, *, instant=NOW, context_id=17, frames=None):
    return MarketSectorRotationService(frame_loader=loader_with(frames or golden_frames())).preview(
        object(), cutoff=frozen(version, instant=instant, context_id=context_id)
    )


@pytest.mark.parametrize(
    "version,expected",
    [
        (LEGACY_CALENDAR_VERSION, "2025-01-03"),
        (CALENDAR_VERSION, "2025-01-02"),
    ],
)
def test_reconstructed_context_reaches_rotation_and_material_identity(version, expected):
    p = rotation(version)
    assert p["eligible_count"] == 11
    assert p["calendar_version"] == version
    assert {r["required_sessions"]["5"] for r in p["sectors"]} == {expected}
    assert p["material_identity_version"] == (
        LEGACY_MATERIAL_IDENTITY if version == LEGACY_CALENDAR_VERSION else MATERIAL_IDENTITY
    )


def test_legacy_material_hash_matches_independent_d4_golden_without_new_metadata():
    p = rotation(LEGACY_CALENDAR_VERSION)
    assert (
        p["snapshot_digest"] == "4b10591216551077c7a3898e3ab83a7d69eab7e6b8309dc49f6c3f605b1f3662"
    )
    p.pop("material_identity_version")
    assert snapshot_digest(p) == p["snapshot_digest"]


def test_same_anchors_still_have_different_calendar_material_identities():
    instant = datetime(2026, 10, 21, 22, tzinfo=UTC)
    frames = {s: synthetic_frame(s) for s in rotation_symbols()}
    old = rotation(LEGACY_CALENDAR_VERSION, instant=instant, frames=frames)
    new = rotation(CALENDAR_VERSION, instant=instant, frames=frames)
    assert old["sectors"][0]["required_sessions"] == new["sectors"][0]["required_sessions"]
    assert old["source_material_digest"] == new["source_material_digest"]
    assert old["snapshot_digest"] != new["snapshot_digest"]


@pytest.mark.parametrize("version", VERSIONS)
def test_canonical_order_clock_drift_and_context_ids_do_not_change_material(version):
    first = rotation(version)
    frames = {s: f.iloc[::-1].copy() for s, f in reversed(list(golden_frames().items()))}
    second = rotation(version, frames=frames, instant=NOW + timedelta(hours=1), context_id=9999)
    second["sectors"].reverse()
    assert snapshot_digest(second) == first["snapshot_digest"]


@pytest.mark.parametrize("version", VERSIONS)
def test_correct_capture_is_atomic_immutable_and_idempotent(rotation_engine, version):
    p = rotation(version)
    repo = MarketSectorRotationRepository(now=lambda: NOW)
    with Session(rotation_engine) as db:
        created = repo.capture(db, p, authority=authority())
    assert created["status"] == "CREATED" and counts(rotation_engine) == (1, 11)
    with Session(rotation_engine) as db:
        before = repo.detail(db, created["snapshot_id"])
    sql = []
    event.listen(rotation_engine, "before_cursor_execute", lambda _c, _s, s, *_a: sql.append(s))
    with Session(rotation_engine) as db:
        again = repo.capture(db, deepcopy(p), authority=authority())
    with Session(rotation_engine) as db:
        assert repo.detail(db, created["snapshot_id"]) == before
    assert again["status"] == "ALREADY_EXISTS"
    assert not any(s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for s in sql)


@pytest.mark.parametrize("original", VERSIONS)
def test_cross_calendar_recapture_conflicts_and_preserves_old_payload(rotation_engine, original):
    p = rotation(original)
    if original == LEGACY_CALENDAR_VERSION:
        p.pop("material_identity_version")  # actual old payload contract
    repo = MarketSectorRotationRepository(now=lambda: NOW)
    with Session(rotation_engine) as db:
        created = repo.capture(db, p, authority=authority())
    with Session(rotation_engine) as db:
        before = repo.detail(db, created["snapshot_id"])
    sql = []
    event.listen(rotation_engine, "before_cursor_execute", lambda _c, _s, s, *_a: sql.append(s))
    other = CALENDAR_VERSION if original == LEGACY_CALENDAR_VERSION else LEGACY_CALENDAR_VERSION
    with Session(rotation_engine) as db:
        result = repo.capture(db, rotation(other), authority=authority())
    with Session(rotation_engine) as db:
        after = repo.detail(db, created["snapshot_id"])
    assert result["status"] == "EVIDENCE_CONFLICT"
    assert before == after and counts(rotation_engine) == (1, 11)
    if original == LEGACY_CALENDAR_VERSION:
        assert "material_identity_version" not in after
    assert not any(s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for s in sql)


def test_concurrent_cross_calendar_capture_keeps_one_coherent_immutable_cohort(rotation_engine):
    gate = Barrier(2)

    def capture(version):
        p = rotation(version)
        with Session(rotation_engine) as db:
            gate.wait(timeout=10)
            return MarketSectorRotationRepository(now=lambda: NOW).capture(
                db, p, authority=authority()
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(capture, VERSIONS))
    assert sorted(r["status"] for r in results) == ["CREATED", "EVIDENCE_CONFLICT"]
    assert counts(rotation_engine) == (1, 11)
    with Session(rotation_engine) as db:
        saved = MarketSectorRotationRepository().latest(db)
    assert saved["snapshot_digest"] == snapshot_digest(saved)


@pytest.mark.parametrize("declared", VERSIONS)
def test_mislabeled_anchors_rejected_before_any_sql(rotation_engine, declared):
    other = CALENDAR_VERSION if declared == LEGACY_CALENDAR_VERSION else LEGACY_CALENDAR_VERSION
    p = rotation(other)
    p["calendar_version"] = declared
    p["material_identity_version"] = (
        LEGACY_MATERIAL_IDENTITY if declared == LEGACY_CALENDAR_VERSION else MATERIAL_IDENTITY
    )
    p["snapshot_digest"] = snapshot_digest(
        p
    )  # even a recomputed hash cannot authorize wrong anchors
    sql = []
    event.listen(rotation_engine, "before_cursor_execute", lambda _c, _s, s, *_a: sql.append(s))
    with Session(rotation_engine) as db, pytest.raises(ValueError, match="CAPTURE_ANCHOR_MISMATCH"):
        MarketSectorRotationRepository(now=lambda: NOW).capture(db, p, authority=authority())
    assert sql == []


@pytest.mark.parametrize(
    "fault", ["unknown", "identity", "digest", "timezone", "readiness", "cutoff", "unversioned_v2"]
)
def test_invalid_calendar_envelope_or_digest_rejected_before_sql(rotation_engine, fault):
    p = rotation(CALENDAR_VERSION)
    if fault == "unknown":
        p["calendar_version"] = "unknown-calendar"
    elif fault == "identity":
        p["material_identity_version"] = LEGACY_MATERIAL_IDENTITY
    elif fault == "digest":
        p["snapshot_digest"] = rotation(LEGACY_CALENDAR_VERSION)["snapshot_digest"]
    elif fault == "timezone":
        p["exchange_timezone"] = "UTC"
    elif fault == "readiness":
        p["bar_readiness_version"] = "wrong"
    elif fault == "unversioned_v2":
        p.pop("material_identity_version")
    else:
        p["cutoff_at_utc"] = "2025-01-09T22:00:00Z"
    sql = []
    event.listen(rotation_engine, "before_cursor_execute", lambda _c, _s, s, *_a: sql.append(s))
    with Session(rotation_engine) as db, pytest.raises(ValueError):
        MarketSectorRotationRepository(now=lambda: NOW).capture(db, p, authority=authority())
    assert sql == []


@pytest.mark.parametrize("kind", ["rotation", "technical", "chart", "coverage"])
@pytest.mark.parametrize("fault", ["unknown", "inconsistent_session"])
def test_msi_boundaries_reject_unsupported_or_inconsistent_frozen_calendars_before_reads(
    kind, fault
):
    c = frozen(CALENDAR_VERSION)
    c = (
        replace(c, calendar_version="unknown")
        if fault == "unknown"
        else replace(c, latest_completed_session=date(2025, 1, 8))
    )
    loader = Mock(side_effect=AssertionError("must fail before reading"))
    service = {
        "rotation": MarketSectorRotationService,
        "technical": MarketSectorTechnicalService,
        "chart": MarketSectorChartService,
        "coverage": MarketSectorIntelligenceCoverageService,
    }[kind](frame_loader=loader)
    method = service.preview if kind in {"rotation", "technical"} else service.build
    with pytest.raises(ValueError):
        method(object(), cutoff=c)
    loader.assert_not_called()


@pytest.fixture(scope="module")
def cached_2025(tmp_path_factory):
    engine = create_engine(
        "sqlite:///" + (tmp_path_factory.mktemp("cp-r01") / "cached.sqlite3").as_posix()
    )
    apply_migration(engine)
    seed_cache(engine)
    days = [subtract_us_trading_sessions(TARGET, i) for i in reversed(range(400))]
    assert date(2025, 1, 9) not in days
    rows = []
    for ticker in symbols() + ["SPY", "QQQ"]:
        f = frame(ticker, len(days))
        f["date"] = days
        for bar in f.itertuples(index=False):
            rows.append(
                dict(
                    id=len(rows) + 1,
                    ticker=ticker,
                    bar_date=bar.date,
                    timeframe="1 day",
                    what_to_show="TRADES",
                    source="CP_R01_ACTUAL_READER_FIXTURE",
                    **{
                        k: float(getattr(bar, k))
                        for k in ("open", "high", "low", "close", "volume")
                    },
                    created_at=NOW - timedelta(minutes=5),
                    first_seen_at=NOW - timedelta(minutes=5),
                    last_seen_at=NOW - timedelta(minutes=5),
                    revision_count=0,
                )
            )
    with engine.begin() as db:
        db.execute(PriceBar.__table__.delete())
        db.execute(PriceBar.__table__.insert(), rows)
    yield engine
    engine.dispose()


@pytest.mark.parametrize("version", VERSIONS)
def test_actual_reader_d2_honors_reconstructed_calendar(cached_2025, version):
    with Session(cached_2025) as db:
        p = MarketSectorRotationService().preview(db, cutoff=frozen(version))
    assert p["eligible_count"] == 11
    assert p["sectors"][0]["required_sessions"]["5"] == (
        "2025-01-03" if version == LEGACY_CALENDAR_VERSION else "2025-01-02"
    )


@pytest.mark.parametrize("version", VERSIONS)
def test_actual_d3_window_continuity_and_sql_use_selected_calendar(cached_2025, version):
    sql = []

    def observe(_c, _s, statement, params, *_args):
        sql.append((statement, params))

    event.listen(cached_2025, "before_cursor_execute", observe)
    try:
        with Session(cached_2025) as db:
            result = MarketSectorTechnicalService().preview(db, cutoff=frozen(version))
    finally:
        event.remove(cached_2025, "before_cursor_execute", observe)
    expected = subtract_us_trading_sessions(TARGET, 755, calendar_version=version).isoformat()
    assert result["read_window_start_session"] == expected
    assert result["calendar_version"] == version
    assert sql and all("bar_date >=" in query and "bar_date <=" in query for query, _p in sql)
    assert all(expected in params for _q, params in sql)
    if version == LEGACY_CALENDAR_VERSION:
        # Correctly reproduce v1's requirement, without inventing its missing bar.
        assert result["v4_ready_count"] == result["v5_ready_count"] == 0
        assert all("MISSING_TRADING_SESSION" in row["data_reasons"] for row in result["rows"])
    else:
        assert result["v4_ready_count"] == result["v5_ready_count"] == 11


@pytest.mark.parametrize("version", VERSIONS)
def test_actual_d4_price_window_and_identity_use_selected_calendar(cached_2025, version):
    with Session(cached_2025) as db:
        result = MarketSectorChartService().build(
            db, symbols="XLK", horizon=5, cutoff=frozen(version)
        )
    days = chart_sessions(TARGET, calendar_version=version)
    assert result["calendar_version"] == version
    assert result["calendar_sessions"] == [d.isoformat() for d in days]
    assert result["start_session"] == days[-6].isoformat()
    if version == LEGACY_CALENDAR_VERSION:
        assert "2025-01-09" in result["calendar_sessions"]
        assert "MISSING_SESSION" in result["rows"][0]["reasons"]
        assert result["rows"][0]["points"] == []
    else:
        assert "2025-01-09" not in result["calendar_sessions"]
        assert result["rows"][0]["status"] == "CURRENT"
        assert len(result["rows"][0]["points"]) == 6


@pytest.mark.parametrize("kind", ["technical", "chart"])
def test_preclosure_supported_versions_keep_distinct_source_identities(kind):
    instant = datetime(2024, 12, 20, 22, tzinfo=UTC)
    target = date(2024, 12, 20)
    days = [subtract_us_trading_sessions(target, i) for i in reversed(range(320))]

    def loader(_db, ticker, **kwargs):
        f = frame(ticker)
        f["date"] = days
        return receipt(f, ticker, kwargs), None

    service = (
        MarketSectorTechnicalService(frame_loader=loader)
        if kind == "technical"
        else MarketSectorChartService(frame_loader=loader)
    )
    results = []
    for version in VERSIONS:
        cutoff = frozen(version, instant=instant)
        p = (
            service.preview(object(), cutoff=cutoff)
            if kind == "technical"
            else service.build(object(), symbols="XLK", horizon=5, cutoff=cutoff)
        )
        results.append(p)
    key = "evaluation_digest" if kind == "technical" else "evidence_digest"
    assert results[0][key] != results[1][key]
    if kind == "technical":
        assert results[0]["v4_ready_count"] == results[0]["v5_ready_count"] == 11
        assert results[1]["v4_ready_count"] == results[1]["v5_ready_count"] == 11
        assert [r["v4"]["final_v4_score"] for r in results[0]["rows"]] == [
            r["v4"]["final_v4_score"] for r in results[1]["rows"]
        ]
    else:
        assert results[0]["calendar_sessions"] == results[1]["calendar_sessions"]
        assert results[0]["rows"][0]["points"] == results[1]["rows"][0]["points"]


def test_shared_execution_boundaries_reject_legacy_before_work(monkeypatch):
    from app.services.ceri.capture_service import CeriRunCaptureService
    from app.services.technical_score_service import score_run_technicals
    from app.services.winner_probability.capture_service import WinnerPredictionCaptureService

    cutoff = frozen(LEGACY_CALENDAR_VERSION)
    with pytest.raises(ValueError, match="LEGACY_CALENDAR_EXECUTION_NOT_SUPPORTED"):
        score_run_technicals(Mock(), 1, market_cutoff=cutoff)
    ceri = object.__new__(CeriRunCaptureService)
    with pytest.raises(ValueError, match="LEGACY_CALENDAR_EXECUTION_NOT_SUPPORTED"):
        ceri.capture_run(Mock(), 1, market_cutoff=cutoff)
    winner = object.__new__(WinnerPredictionCaptureService)
    with pytest.raises(ValueError, match="LEGACY_CALENDAR_EXECUTION_NOT_SUPPORTED"):
        winner.capture_run(Mock(), run_id=1, market_cutoff=cutoff)


def test_selected_clock_does_not_mutate_original_context():
    cutoff = frozen(LEGACY_CALENDAR_VERSION)
    before = deepcopy(cutoff)
    assert clock_for_cutoff(cutoff).calendar_version == LEGACY_CALENDAR_VERSION
    assert cutoff == before


def test_opaque_retained_context_is_inspectable_but_never_defaults_to_v2():
    legacy = frozen(LEGACY_CALENDAR_VERSION, instant=datetime(2025, 1, 9, 22, tzinfo=UTC))
    retained = replace(legacy, calendar_version="unrecognized-retained-calendar")
    assert retained.latest_completed_session == date(2025, 1, 9)
    assert retained.calendar_version == "unrecognized-retained-calendar"
    with pytest.raises(ValueError, match="unsupported US equity calendar"):
        clock_for_cutoff(retained)
