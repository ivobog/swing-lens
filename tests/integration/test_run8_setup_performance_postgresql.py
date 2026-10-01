from __future__ import annotations

import os
from collections import Counter
from datetime import UTC, datetime
from time import perf_counter

import pandas as pd
import pytest
from native_mutation_support import seed_native_core, seed_price_frame
from sqlalchemy import event, select
from sqlalchemy.orm import Session
from test_fundamental_ranker_v2 import _quality_values
from test_slse_performance_certification import _upgrade
from test_technical_work import _synthetic_frame

from app.models.tables import PipelineRun, RawCompanyRow
from app.services.combined_decision import refresh_combined_results
from app.services.decision_effective_configuration import (
    resolve_setup_configuration,
)
from app.services.earnings_date_parser import parse_earnings_date
from app.services.fundamental_score_service import recalculate_run_fundamentals
from app.services.ranking_profile_service import refresh_ranking_profile
from app.services.setup_lifecycle.decision_evidence import persist_setup_evidence
from app.services.setup_lifecycle.snapshot_builder import SetupLifecycleSnapshotCaptureService
from app.services.technical_score_service import score_run_technicals

pytestmark = [
    pytest.mark.integration,
    pytest.mark.destructive,
    pytest.mark.slow,
    pytest.mark.skipif(
        os.getenv("RUN_RUN8_REMEDIATION_BENCHMARK") != "1",
        reason="set RUN_RUN8_REMEDIATION_BENCHMARK=1 for Run 8 scale certification",
    ),
]


@pytest.mark.parametrize("population", (10, 25, 107))
def test_setup_capture_run8_scale_is_bounded(
    disposable_postgres_database: str,
    population: int,
    monkeypatch: pytest.MonkeyPatch,
    record_property,
) -> None:
    """Measure the invariant-complete setup writer at Run 8 population checkpoints."""

    _upgrade(disposable_postgres_database)
    from sqlalchemy import create_engine

    engine = create_engine(disposable_postgres_database)
    queries = Counter()
    sql_seconds = 0.0
    flushes = 0
    evidence_writes = 0

    def before_cursor(_conn, _cursor, statement, _params, context, _many):
        context._run8_query_started = perf_counter()
        normalized = " ".join(statement.split())
        queries[normalized] += 1

    def after_cursor(_conn, _cursor, _statement, _params, context, _many):
        nonlocal sql_seconds
        sql_seconds += perf_counter() - context._run8_query_started

    def after_flush(_session, _flush_context):
        nonlocal flushes
        flushes += 1

    original_persist = persist_setup_evidence

    def counted_persist(*args, **kwargs):
        nonlocal evidence_writes
        evidence_writes += 1
        return original_persist(*args, **kwargs)

    monkeypatch.setattr(
        "app.services.setup_lifecycle.decision_evidence.persist_setup_evidence",
        counted_persist,
    )
    event.listen(engine, "before_cursor_execute", before_cursor)
    event.listen(engine, "after_cursor_execute", after_cursor)
    event.listen(Session, "after_flush", after_flush)
    try:
        setup_configuration = resolve_setup_configuration()
        with Session(engine) as db:
            cutoff, configurations, _ = seed_native_core(
                db,
                ticker="R8S000",
                cutoff_at=datetime(2026, 8, 7, 21, tzinfo=UTC),
                extra_configurations=(setup_configuration,),
            )
            pipeline = db.scalar(select(PipelineRun).where(PipelineRun.upload_run_id == 7))
            tickers = tuple(f"R8S{index:03d}" for index in range(population))
            frame = _synthetic_frame()
            frame["date"] = pd.bdate_range(
                end=cutoff.latest_completed_session,
                periods=len(frame),
            )
            for index, ticker in enumerate(tickers[1:], start=2):
                db.add(
                    RawCompanyRow(
                        run_id=7,
                        row_number=index,
                        ticker=ticker,
                        sector="Technology",
                        sector_canonical="Information Technology",
                        upcoming_earnings_date=parse_earnings_date(None),
                        raw_json={"Symbol": ticker, **_quality_values()},
                    )
                )
                seed_price_frame(db, ticker, frame, cutoff.cutoff_at)
            db.flush()
            args = {
                "market_cutoff": cutoff,
                "pipeline_run_id": pipeline.id,
            }
            fundamentals = recalculate_run_fundamentals(
                db,
                7,
                **args,
                effective_configuration=configurations[0],
            )
            technicals = score_run_technicals(
                db,
                7,
                tickers=list(tickers),
                **args,
                effective_configuration=configurations[1],
            )
            source_evidence = {
                ticker: {
                    "fundamental": next(
                        row.evidence_id for row in fundamentals if row.ticker == ticker
                    ),
                    "technical": next(
                        row.evidence_id for row in technicals if row.ticker == ticker
                    ),
                }
                for ticker in tickers
            }
            refresh_combined_results(
                db,
                7,
                **args,
                effective_configuration=configurations[2],
                source_evidence=source_evidence,
            )
            refresh_ranking_profile(
                db,
                7,
                "momentum_swing",
                **args,
                effective_configuration=configurations[3],
            )
            db.commit()

            queries.clear()
            sql_seconds = 0.0
            flushes = 0
            evidence_writes = 0
            progress: list[tuple[str, int, int, str]] = []
            started = perf_counter()
            result = SetupLifecycleSnapshotCaptureService().capture_snapshots_for_run(
                db,
                7,
                market_cutoff=cutoff,
                frozen_tickers=tickers,
                progress_callback=lambda ticker, processed, total, phase="PERSISTING": (
                    progress.append((ticker, processed, total, phase))
                ),
            )
            db.commit()
            wall_seconds = perf_counter() - started

        query_count = sum(queries.values())
        duplicate_queries = sum(count - 1 for count in queries.values())
        evidence_loads = sum(
            count
            for statement, count in queries.items()
            if statement.upper().startswith("SELECT")
            and "CORE_CALCULATION_EVIDENCE" in statement.upper()
        )
        configuration_loads = sum(
            count
            for statement, count in queries.items()
            if statement.upper().startswith("SELECT")
            and any(
                table in statement.upper()
                for table in (
                    "EFFECTIVE_CONFIGURATION_RECORDS",
                    "EXECUTION_CONFIGURATION_ANCHORS",
                    "EXECUTION_CONFIGURATION_BINDINGS",
                )
            )
        )
        record_property("population", population)
        record_property("wall_seconds", round(wall_seconds, 6))
        record_property("sql_seconds", round(sql_seconds, 6))
        record_property("query_count", query_count)
        record_property("duplicate_queries", duplicate_queries)
        record_property("flush_count", flushes)
        record_property("evidence_writes", evidence_writes)
        record_property("evidence_loads", evidence_loads)
        record_property("configuration_loads", configuration_loads)
        record_property("progress_events", len(progress))
        assert result.status == "COMPLETED"
        assert result.captured == population
        assert evidence_writes == population
        assert evidence_loads <= 3 * population
        assert configuration_loads <= 8 * population
        assert progress[-1][1:] == (population, population, "EVIDENCE")
        assert query_count <= 250 + (45 * population)
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor)
        event.remove(engine, "after_cursor_execute", after_cursor)
        event.remove(Session, "after_flush", after_flush)
        engine.dispose()
