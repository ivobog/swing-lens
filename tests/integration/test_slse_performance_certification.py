from __future__ import annotations

import ctypes
import json
import math
import os
import subprocess
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event, func, insert, select, text
from sqlalchemy.orm import Session

from app.models.tables import (
    CombinedResult,
    FundamentalScore,
    PriceBar,
    RawCompanyRow,
    SetupLifecycleEvent,
    SetupSignalSnapshot,
    SignalChangeEvent,
    TechnicalScore,
    UploadRun,
)
from app.services.setup_lifecycle.evaluation_service import SetupLifecycleEvaluationService
from app.services.setup_lifecycle.query_service import (
    SetupLifecycleFilters,
    SetupLifecycleListQuery,
    SetupLifecycleQueryService,
)
from app.services.setup_lifecycle.snapshot_builder import (
    SetupLifecycleSnapshotCaptureService,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = (
    REPO_ROOT
    / "docs"
    / "slse_review"
    / "evidence"
    / "slse_performance_certification_2026-08-12.json"
)
PERFORMANCE_DATE = date(2026, 8, 11)
SCALE_DATES = {
    100: date(2026, 8, 3),
    500: date(2026, 8, 4),
    1_000: date(2026, 8, 5),
}

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.skipif(
        os.getenv("RUN_SLSE_PERFORMANCE_CERTIFICATION") != "1",
        reason="set RUN_SLSE_PERFORMANCE_CERTIFICATION=1 for the dedicated scale gate",
    ),
]

_EVALUATION_REUSE_REPORT = os.getenv("SLSE_PERFORMANCE_EVALUATION_REPORT")


@dataclass
class _QueryCounter:
    count: int = 0

    def before_cursor_execute(self, *_args) -> None:
        self.count += 1


@dataclass
class _TimedCaptureService:
    inner: SetupLifecycleSnapshotCaptureService
    counter: _QueryCounter
    elapsed_seconds: float = 0.0
    query_count: int = 0
    performance: dict[str, float] | None = None

    def capture_snapshots_for_run(self, *args, **kwargs):
        before_queries = self.counter.count
        started = time.perf_counter()
        result = self.inner.capture_snapshots_for_run(*args, **kwargs)
        self.elapsed_seconds = time.perf_counter() - started
        self.query_count = self.counter.count - before_queries
        self.performance = dict(result.performance)
        return result


def test_slse_phase_12_performance_certification(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    from sqlalchemy import create_engine

    engine = create_engine(disposable_postgres_database)
    counter = _QueryCounter()
    event.listen(engine, "before_cursor_execute", counter.before_cursor_execute)
    report: dict[str, Any] = {
        "certification": "SLSE Phase 12 performance",
        "generated_at": datetime.now(UTC).isoformat(),
        "database": "disposable PostgreSQL",
        "targets": {
            "evaluation_1000_tickers_seconds": 60,
            "query_p95_ms": 500,
            "minimum_snapshot_history": 100_000,
        },
        "evaluation": [],
        "queries": {},
    }
    try:
        if _EVALUATION_REUSE_REPORT:
            reused = json.loads(Path(_EVALUATION_REUSE_REPORT).read_text(encoding="utf-8"))
            report["evaluation"] = list(reused["evaluation"])
            report["evaluation_reused_from"] = str(_EVALUATION_REUSE_REPORT)
        else:
            for size in (100, 500, 1_000):
                with Session(engine) as db:
                    run_id = _seed_source_run(db, size=size, as_of=SCALE_DATES[size])
                capture = _TimedCaptureService(SetupLifecycleSnapshotCaptureService(), counter)
                service = SetupLifecycleEvaluationService(capture_service=capture)
                before_rss = _working_set_bytes()
                before_queries = counter.count
                started = time.perf_counter()
                with Session(engine) as db:
                    result = service.evaluate_run(
                        db, run_id, requester="slse-performance-certification"
                    )
                    db.commit()
                elapsed = time.perf_counter() - started
                after_rss = _working_set_bytes()
                row = {
                    "ticker_count": size,
                    "total_seconds": round(elapsed, 6),
                    "capture_seconds": round(capture.elapsed_seconds, 6),
                    "post_capture_seconds": round(elapsed - capture.elapsed_seconds, 6),
                    "query_count": counter.count - before_queries,
                    "capture_query_count": capture.query_count,
                    "rss_before_bytes": before_rss,
                    "rss_after_bytes": after_rss,
                    "rss_delta_bytes": after_rss - before_rss,
                    "capture_metrics": capture.performance,
                    "result": result.as_dict(),
                }
                report["evaluation"].append(row)
                assert result.status == "COMPLETED"
                assert result.snapshots_captured == size
        evaluation_1_000 = next(row for row in report["evaluation"] if row["ticker_count"] == 1_000)
        assert evaluation_1_000["total_seconds"] <= 60

        with Session(engine) as db:
            for table_name in (
                "setup_signal_snapshots",
                "setup_lifecycle_events",
                "signal_change_events",
                "setup_lifecycle_episodes",
            ):
                db.execute(text(f"ALTER TABLE {table_name} SET (autovacuum_enabled = false)"))
            db.commit()
            _seed_query_history(db, snapshot_count=110_000, event_count=100_000)
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            for table_name in (
                "setup_signal_snapshots",
                "setup_lifecycle_events",
                "signal_change_events",
                "setup_lifecycle_episodes",
            ):
                connection.exec_driver_sql(f"VACUUM (ANALYZE) {table_name}")
            connection.exec_driver_sql("CHECKPOINT")
        with Session(engine) as db:
            snapshot_count = int(
                db.scalar(select(func.count()).select_from(SetupSignalSnapshot)) or 0
            )
            combined_change_count = int(
                db.scalar(select(func.count()).select_from(SetupLifecycleEvent)) or 0
            ) + int(db.scalar(select(func.count()).select_from(SignalChangeEvent)) or 0)
        report["fixture"] = {
            "snapshot_count": snapshot_count,
            "combined_change_count": combined_change_count,
            "synthetic_scope": "performance-only manufactured history",
        }
        assert snapshot_count >= 100_000
        assert combined_change_count >= 100_000

        service = SetupLifecycleQueryService()

        def first_page(db: Session):
            return service.changes(
                db,
                SetupLifecycleListQuery(filters=SetupLifecycleFilters(), limit=50),
            )

        with Session(engine) as db:
            legacy_deep = service.changes(
                db,
                SetupLifecycleListQuery(filters=SetupLifecycleFilters(), limit=50, cursor="60000"),
            )
        deep_cursor = legacy_deep["next_cursor"]
        assert deep_cursor and deep_cursor.startswith("k1.")

        query_cases = {
            "market_changes_first_page": first_page,
            "market_changes_deep_cursor": lambda db: service.changes(
                db,
                SetupLifecycleListQuery(
                    filters=SetupLifecycleFilters(),
                    limit=50,
                    cursor=deep_cursor,
                ),
            ),
            "no_material_change": lambda db: service.changes(
                db,
                SetupLifecycleListQuery(
                    filters=SetupLifecycleFilters(
                        as_of_date=PERFORMANCE_DATE,
                        transition="NO_MATERIAL_CHANGE",
                    ),
                    limit=50,
                ),
            ),
            "ticker_timeline": lambda db: service.ticker_timeline(db, ticker="Q0000", limit=100),
            "common_filters": lambda db: service.changes(
                db,
                SetupLifecycleListQuery(
                    filters=SetupLifecycleFilters(
                        setup_family="BREAKOUT",
                        confidence_min=75,
                        date_from=PERFORMANCE_DATE - timedelta(days=30),
                    ),
                    limit=50,
                ),
            ),
            "compound_filter": lambda db: service.changes(
                db,
                SetupLifecycleListQuery(
                    filters=SetupLifecycleFilters(
                        sector="Technology",
                        setup_family="BREAKOUT",
                        lifecycle_state="READY",
                        actionability="ACTIONABLE",
                        confidence_min=75,
                        confidence_max=90,
                        setup_score_min=7.0,
                        setup_score_max=9.0,
                        date_from=PERFORMANCE_DATE - timedelta(days=45),
                        date_to=PERFORMANCE_DATE,
                    ),
                    limit=50,
                ),
            ),
            "export_preflight_500": lambda db: service.changes(
                db,
                SetupLifecycleListQuery(filters=SetupLifecycleFilters(), limit=500),
            ),
        }
        query_failures: dict[str, dict[str, Any]] = {}
        for name, factory in query_cases.items():
            metrics = _benchmark_query(engine, counter, factory)
            report["queries"][name] = metrics
            if name != "export_preflight_500" and metrics["p95_ms"] > 500:
                query_failures[name] = metrics
        assert not query_failures, query_failures

        report["status"] = "PASS"
    except Exception:
        report["status"] = "FAIL"
        raise
    finally:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        event.remove(engine, "before_cursor_execute", counter.before_cursor_execute)
        engine.dispose()


def _benchmark_query(engine, counter: _QueryCounter, factory) -> dict[str, Any]:
    for _ in range(3):
        with Session(engine) as db:
            factory(db)
    durations: list[float] = []
    query_counts: list[int] = []
    item_counts: list[int] = []
    rss_before = _working_set_bytes()
    for _ in range(20):
        before_queries = counter.count
        started = time.perf_counter()
        with Session(engine) as db:
            payload = factory(db)
        durations.append((time.perf_counter() - started) * 1_000)
        query_counts.append(counter.count - before_queries)
        item_counts.append(len(payload.get("items") or payload.get("lifecycle_events") or []))
    rss_after = _working_set_bytes()
    return {
        "samples": len(durations),
        "p50_ms": round(_percentile(durations, 50), 3),
        "p95_ms": round(_percentile(durations, 95), 3),
        "p99_ms": round(_percentile(durations, 99), 3),
        "min_ms": round(min(durations), 3),
        "max_ms": round(max(durations), 3),
        "query_count_min": min(query_counts),
        "query_count_max": max(query_counts),
        "item_count_min": min(item_counts),
        "item_count_max": max(item_counts),
        "rss_before_bytes": rss_before,
        "rss_after_bytes": rss_after,
        "rss_delta_bytes": rss_after - rss_before,
    }


def _percentile(values: list[float], percentile: int) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil((percentile / 100) * len(ordered)) - 1)
    return ordered[index]


def _seed_source_run(db: Session, *, size: int, as_of: date) -> int:
    processed_at = datetime.combine(as_of, datetime.min.time(), tzinfo=UTC).replace(hour=21)
    run = UploadRun(
        filename=f"slse-performance-{size}.csv",
        status="COMPLETED",
        row_count=size,
        uploaded_at=processed_at,
        processed_at=processed_at,
        notes="SLSE performance certification",
    )
    db.add(run)
    db.flush()
    tickers = [f"P{size:04d}{index:04d}" for index in range(size)]
    db.execute(
        insert(RawCompanyRow),
        [
            {
                "run_id": run.id,
                "row_number": index + 1,
                "ticker": ticker,
                "company_name": f"Performance {ticker}",
                "sector": "Technology",
                "sector_canonical": "Technology",
                "raw_json": {"pivot_price": 100.0, "trigger_price": 100.0},
            }
            for index, ticker in enumerate(tickers)
        ],
    )
    db.execute(
        insert(FundamentalScore),
        [
            {
                "run_id": run.id,
                "ticker": ticker,
                "fundamental_score": Decimal("8.0"),
                "liquidity_risk_score": Decimal("8.0"),
            }
            for ticker in tickers
        ],
    )
    db.execute(
        insert(TechnicalScore),
        [
            {
                "run_id": run.id,
                "ticker": ticker,
                "dual_score": Decimal("7.5"),
                "trend_score": Decimal("7.5"),
                "momentum_score": Decimal("7.0"),
                "setup_score": Decimal("7.8"),
                "risk_score": Decimal("2.0"),
                "relative_strength_score": Decimal("8.0"),
                "leadership_score": Decimal("8.0"),
                "classification": "Breakout Base",
                "technical_confidence": "HIGH",
                "data_quality_score": Decimal("9.0"),
                "warning_flags_json": [],
                "feature_flags_json": [],
                "debug_json": {"derived": {"atr": 2.0, "atr_pct": 2.0}},
                "v4_debug_json": {},
                "created_at": processed_at,
            }
            for ticker in tickers
        ],
    )
    db.execute(
        insert(CombinedResult),
        [
            {
                "run_id": run.id,
                "ticker": ticker,
                "company_name": f"Performance {ticker}",
                "sector": "Technology",
                "final_score": Decimal("90"),
                "fundamental_score": Decimal("8.0"),
                "technical_classification": "Breakout Base",
                "dual_score": Decimal("7.5"),
                "combined_decision": "WATCH",
                "earnings_risk_level": "LOW",
                "is_complete": True,
                "has_fundamental": True,
                "has_technical": True,
            }
            for ticker in tickers
        ],
    )
    db.execute(
        insert(PriceBar),
        [
            {
                "ticker": ticker,
                "bar_date": as_of,
                "timeframe": "1 day",
                "open": Decimal("98"),
                "high": Decimal("101"),
                "low": Decimal("97"),
                "close": Decimal("99"),
                "volume": Decimal("1000000"),
                "source": "SLSE_PERFORMANCE",
                "what_to_show": "TRADES",
                "data_hash": f"slse-perf:{ticker}:{as_of.isoformat()}",
            }
            for ticker in tickers
        ],
    )
    db.commit()
    return run.id


def _seed_query_history(db: Session, *, snapshot_count: int, event_count: int) -> None:
    ticker_count = 1_000
    dates_per_ticker = snapshot_count // ticker_count
    dates = [
        PERFORMANCE_DATE - timedelta(days=dates_per_ticker - index - 1)
        for index in range(dates_per_ticker)
    ]
    snapshot_rows: list[dict[str, Any]] = []
    for ticker_index in range(ticker_count):
        ticker = f"Q{ticker_index:04d}"
        for day_index, as_of in enumerate(dates):
            snapshot_rows.append(
                {
                    "source_run_id_text": "slse-performance-100k",
                    "ticker": ticker,
                    "company_name": f"Query Performance {ticker}",
                    "sector": "Technology",
                    "timeframe": "1d",
                    "data_as_of_date": as_of,
                    "calculated_at": datetime.combine(
                        as_of, datetime.min.time(), tzinfo=UTC
                    ).replace(hour=21),
                    "origin_type": "PERFORMANCE_FIXTURE",
                    "engine_version": "slse-1.2.0",
                    "config_version": "2026-08-12",
                    "config_hash": "performance-config-hash",
                    "source_data_hash": f"perf100k:{ticker}:{day_index}",
                    "schema_version": "v1",
                    "is_canonical": True,
                    "primary_setup_family": "BREAKOUT",
                    "primary_phase": "PIVOT_READY",
                    "lifecycle_state_candidate": "READY",
                    "actionability_candidate": "ACTIONABLE",
                    "data_quality_label": "HIGH",
                    "confidence_score": 82,
                    "confidence_label": "HIGH",
                    "dual_score": Decimal("7.6"),
                    "setup_score": Decimal("7.8"),
                    "distance_to_pivot_pct": Decimal("1.0"),
                    "required_feature_coverage": Decimal("1.0"),
                    "freshness_status": "FRESH",
                    "signals_json": {
                        "sector_rank": {"value": (ticker_index % 20) + 1},
                        "market_regime": {"value": "RISK_ON"},
                        "market_gate": {"value": True},
                    },
                    "feature_flags_json": {},
                    "warning_flags_json": [],
                    "missing_data_json": {},
                    "source_lineage_json": {"fixture": "performance-only"},
                    "diagnostic_high_cross_json": {},
                    "canonical_decision_json": {},
                    "debug_json": {},
                }
            )
    for chunk in _chunks(snapshot_rows, 5_000):
        db.execute(insert(SetupSignalSnapshot), chunk)
        db.commit()
    snapshot_refs = db.execute(
        select(
            SetupSignalSnapshot.id,
            SetupSignalSnapshot.ticker,
            SetupSignalSnapshot.data_as_of_date,
        )
        .where(SetupSignalSnapshot.source_run_id_text == "slse-performance-100k")
        .order_by(SetupSignalSnapshot.id)
    ).all()
    lifecycle_count = event_count // 2
    lifecycle_rows = [
        {
            "snapshot_id": row.id,
            "ticker": row.ticker,
            "timeframe": "1d",
            "setup_family": "BREAKOUT",
            "effective_date": row.data_as_of_date,
            "event_type": "STATE_TRANSITION",
            "from_state": "TIGHTENING",
            "to_state": "READY",
            "from_phase": "CONTRACTION",
            "to_phase": "PIVOT_READY",
            "state_age_before": 2,
            "actionability_before": "WATCH_ONLY",
            "actionability_after": "ACTIONABLE",
            "confidence_score": 82,
            "confidence_label": "HIGH",
            "severity": "ACTIONABLE" if index % 7 == 0 else "NOTABLE",
            "source_event_key": f"perf-lifecycle:{row.id}",
            "is_current_version": True,
            "engine_version": "slse-1.2.0",
            "config_version": "2026-08-12",
            "config_hash": "performance-config-hash",
            "reason_codes_json": ["PERFORMANCE_FIXTURE"],
            "evidence_json": {"velocity": {"3": {"normalized_delta": 0.2}}},
            "warning_flags_json": [],
        }
        for index, row in enumerate(snapshot_refs[:lifecycle_count])
    ]
    signal_refs = snapshot_refs[lifecycle_count:event_count]
    signal_rows = [
        {
            "current_snapshot_id": row.id,
            "ticker": row.ticker,
            "timeframe": "1d",
            "effective_date": row.data_as_of_date,
            "category": "SCORE",
            "signal_key": "technical_score",
            "value_type": "float",
            "old_value_json": {"value": 7.4},
            "new_value_json": {"value": 7.6},
            "delta_numeric": Decimal("0.2"),
            "normalized_delta": Decimal("0.2"),
            "direction": "higher_is_better",
            "severity": "RISK" if index % 11 == 0 else "NOTABLE",
            "signal_definition_version": "2026-08-12",
            "source_event_key": f"perf-signal:{row.id}",
            "config_hash": "performance-config-hash",
            "reason_codes_json": ["PERFORMANCE_FIXTURE"],
            "evidence_json": {"velocity": {"3": {"normalized_delta": 0.2}}},
        }
        for index, row in enumerate(signal_refs)
    ]
    for chunk in _chunks(lifecycle_rows, 5_000):
        db.execute(insert(SetupLifecycleEvent), chunk)
        db.commit()
    for chunk in _chunks(signal_rows, 5_000):
        db.execute(insert(SignalChangeEvent), chunk)
        db.commit()


def _chunks(rows: list[dict[str, Any]], size: int):
    for index in range(0, len(rows), size):
        yield rows[index : index + size]


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def _working_set_bytes() -> int:
    if os.name != "nt":
        # Linux reports ru_maxrss in KiB; macOS reports bytes.
        import resource

        value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return value if sys.platform == "darwin" else value * 1_024
    counters = _ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ProcessMemoryCounters),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    handle = kernel32.GetCurrentProcess()
    if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
        return 0
    return int(counters.WorkingSetSize)


def _upgrade(database_url: str) -> None:
    env = {**os.environ, "DATABASE_URL": database_url}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
