from __future__ import annotations

import importlib.util
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import pandas as pd
import pytest
from native_mutation_support import seed_native_core, seed_price_frame
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session
from test_fundamental_ranker_v2 import _quality_values
from test_slse_performance_certification import _upgrade
from test_technical_work import _synthetic_frame

from app.models.tables import (
    CoreCalculationEvidence,
    PipelineRun,
    RawCompanyRow,
    SetupSignalSnapshot,
    TransitionDecisionHandoffManifest,
    TransitionPreflightPlan,
)
from app.services.canonical_evidence import CanonicalEvidenceSerializer
from app.services.combined_decision import refresh_combined_results
from app.services.core_calculation_evidence import calculation_evidence_payload
from app.services.decision_effective_configuration import (
    resolve_setup_configuration,
)
from app.services.earnings_date_parser import parse_earnings_date
from app.services.fundamental_score_service import recalculate_run_fundamentals
from app.services.ranking_profile_service import refresh_ranking_profile
from app.services.setup_lifecycle.decision_evidence import (
    _SETUP_PROJECTION_FIELDS,
    persist_setup_evidence,
)
from app.services.setup_lifecycle.repository import SetupLifecycleRepository
from app.services.setup_lifecycle.snapshot_builder import SetupLifecycleSnapshotCaptureService
from app.services.technical_score_service import score_run_technicals
from app.services.transition_preflight_plan_service import (
    freeze_transition_decision_handoff_manifest,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.destructive,
    pytest.mark.slow,
    pytest.mark.skipif(
        os.getenv("RUN_RUN8_REMEDIATION_BENCHMARK") != "1",
        reason="set RUN_RUN8_REMEDIATION_BENCHMARK=1 for Run 8 scale certification",
    ),
]


def _install_task_q_transaction_trace(engine) -> None:
    trace_directory = os.getenv("TASK_2_9_12Q_TRACE_DIR")
    if not trace_directory:
        return
    os.environ["CERTIFICATION_TRANSACTION_TRACE_DIR"] = trace_directory
    trace_module_path = (
        Path(__file__).resolve().parents[1]
        / "e2e"
        / "single_run_certification"
        / "transaction_ownership.py"
    )
    spec = importlib.util.spec_from_file_location(
        "task_2_9_12q_transaction_ownership",
        trace_module_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("transaction ownership tracer could not be loaded")
    trace_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(trace_module)
    assert trace_module.install_transaction_ownership_trace(engine)


@pytest.mark.parametrize("population", (10, 25, 100, 107))
def test_setup_capture_run8_scale_is_bounded(
    disposable_postgres_database: str,
    population: int,
    monkeypatch: pytest.MonkeyPatch,
    record_property,
    record_testsuite_property,
) -> None:
    """Measure the invariant-complete setup writer at Run 8 population checkpoints."""

    _upgrade(disposable_postgres_database)
    from sqlalchemy import create_engine

    engine = create_engine(disposable_postgres_database)
    observer = create_engine(disposable_postgres_database, isolation_level="AUTOCOMMIT")
    queries = Counter()
    sql_seconds = 0.0
    flushes = 0
    evidence_writes = 0
    transaction_ages: list[float] = []
    cpu_parent_transaction_states: list[bool] = []
    final_parent_transaction = None

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
            parent_backend_pid = int(db.scalar(text("select pg_backend_pid()")))
            db.commit()

            queries.clear()
            sql_seconds = 0.0
            flushes = 0
            evidence_writes = 0
            progress: list[tuple[str, int, int, str]] = []

            def capture_progress(ticker, processed, total, phase="PERSISTING"):
                if phase in {"PREPARING", "SETUP_INPUTS_BUILT"}:
                    cpu_parent_transaction_states.append(db.in_transaction())
                    assert not db.in_transaction()
                with observer.connect() as connection:
                    observed = (
                        connection.execute(
                            text(
                                "select extract(epoch from clock_timestamp() - xact_start) "
                                "as age_seconds from pg_stat_activity "
                                "where pid = :pid and xact_start is not null"
                            ),
                            {"pid": parent_backend_pid},
                        )
                        .mappings()
                        .one_or_none()
                    )
                if observed is not None:
                    transaction_ages.append(float(observed["age_seconds"]))
                progress.append((ticker, processed, total, phase))

            started = perf_counter()
            result = SetupLifecycleSnapshotCaptureService().capture_snapshots_for_run(
                db,
                7,
                market_cutoff=cutoff,
                frozen_tickers=tickers,
                progress_callback=capture_progress,
            )
            db.commit()
            with observer.connect() as connection:
                final_parent_transaction = connection.scalar(
                    text(
                        "select xact_start from pg_stat_activity "
                        "where pid = :pid and xact_start is not null"
                    ),
                    {"pid": parent_backend_pid},
                )
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
        record_testsuite_property(
            f"setup_{population}_maximum_transaction_age_seconds",
            f"{max(transaction_ages, default=0.0):.6f}",
        )
        record_testsuite_property(
            f"setup_{population}_cpu_parent_transaction_states",
            ",".join(str(value).lower() for value in cpu_parent_transaction_states),
        )
        assert result.status == "COMPLETED"
        assert result.captured == population
        assert evidence_writes == population
        assert evidence_loads <= 3 * population
        assert configuration_loads <= 8 * population
        assert progress[-1][1:] == (population, population, "EVIDENCE")
        assert cpu_parent_transaction_states
        assert not any(cpu_parent_transaction_states)
        assert max(transaction_ages, default=0.0) < 120
        assert final_parent_transaction is None
        assert query_count <= 250 + (45 * population)
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor)
        event.remove(engine, "after_cursor_execute", after_cursor)
        event.remove(Session, "after_flush", after_flush)
        observer.dispose()
        engine.dispose()


def test_composed_setup_100_consumed_handoff_publication_is_bounded(
    disposable_postgres_database: str,
    record_property,
    record_testsuite_property,
) -> None:
    """Exercise the complete handoff verification and Setup publication boundary."""

    _upgrade(disposable_postgres_database)
    from sqlalchemy import create_engine

    engine = create_engine(disposable_postgres_database)
    observer = create_engine(disposable_postgres_database, isolation_level="AUTOCOMMIT")
    _install_task_q_transaction_trace(engine)
    queries = Counter()
    sql_seconds = 0.0
    flushes = 0
    transaction_ages: list[float] = []
    transaction_samples: list[dict[str, object]] = []

    class FinancialParityRepository(SetupLifecycleRepository):
        prepared_financial_fingerprint: str | None = None

        def upsert_snapshots(
            self,
            db,
            dtos,
            *,
            progress_callback=None,
            should_cancel=None,
            prepared_validations=None,
        ):
            candidates = [self._snapshot_from_write(dto) for dto in dtos]
            self.prepared_financial_fingerprint = CanonicalEvidenceSerializer.fingerprint(
                [
                    {
                        "ticker": candidate.ticker,
                        "payload": calculation_evidence_payload(
                            candidate,
                            excluded_columns=_SETUP_PROJECTION_FIELDS,
                        ),
                    }
                    for candidate in candidates
                ]
            )
            return super().upsert_snapshots(
                db,
                dtos,
                progress_callback=progress_callback,
                should_cancel=should_cancel,
                prepared_validations=prepared_validations,
            )

    repository = FinancialParityRepository()

    def before_cursor(_conn, _cursor, statement, _params, context, _many):
        context._task_q_query_started = perf_counter()
        queries[" ".join(statement.split())] += 1

    def after_cursor(_conn, _cursor, _statement, _params, context, _many):
        nonlocal sql_seconds
        sql_seconds += perf_counter() - context._task_q_query_started

    def after_flush(_session, _flush_context):
        nonlocal flushes
        flushes += 1

    event.listen(engine, "before_cursor_execute", before_cursor)
    event.listen(engine, "after_cursor_execute", after_cursor)
    event.listen(Session, "after_flush", after_flush)
    try:
        setup_configuration = resolve_setup_configuration()
        tickers = tuple(f"Q12{index:03d}" for index in range(100))
        with Session(engine, expire_on_commit=False) as db:
            cutoff, configurations, _ = seed_native_core(
                db,
                ticker=tickers[0],
                cutoff_at=datetime(2026, 8, 7, 21, tzinfo=UTC),
                extra_configurations=(setup_configuration,),
                include_decision_context=True,
            )
            pipeline = db.scalar(select(PipelineRun).where(PipelineRun.upload_run_id == 7))
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
            args = {"market_cutoff": cutoff, "pipeline_run_id": pipeline.id}
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

            handoff = freeze_transition_decision_handoff_manifest(
                db,
                upload_run_id=7,
                market_cutoff=cutoff,
            )
            assert handoff is not None
            handoff_id = handoff.id
            handoff_fingerprint = handoff.manifest_fingerprint
            db.commit()
            plan = db.get(TransitionPreflightPlan, handoff.preflight_plan_id)
            assert plan is not None and plan.status == "CONSUMED"
            db.commit()

            parent_session_id = id(db)
            parent_backend_pid = int(db.scalar(text("select pg_backend_pid()")))
            db.commit()
            db.info["ownership_trace_next_phase"] = "SETUP_ATOMIC_PUBLICATION"
            queries.clear()
            sql_seconds = 0.0
            flushes = 0
            progress: list[tuple[str, int, int, str]] = []

            def capture_progress(ticker, processed, total, phase="PERSISTING"):
                with observer.connect() as connection:
                    observed = (
                        connection.execute(
                            text(
                                "select extract(epoch from clock_timestamp() - xact_start) "
                                "as age_seconds, state, query from pg_stat_activity "
                                "where pid=:pid and xact_start is not null"
                            ),
                            {"pid": parent_backend_pid},
                        )
                        .mappings()
                        .one_or_none()
                    )
                if observed is not None:
                    age = float(observed["age_seconds"])
                    transaction_ages.append(age)
                    transaction_samples.append(
                        {
                            "phase": phase,
                            "ticker": ticker,
                            "processed": processed,
                            "age_seconds": age,
                            "state": observed["state"],
                            "query": observed["query"],
                        }
                    )
                progress.append((ticker, processed, total, phase))

            started = perf_counter()
            result = SetupLifecycleSnapshotCaptureService(
                repository=repository
            ).capture_snapshots_for_run(
                db,
                7,
                market_cutoff=cutoff,
                frozen_tickers=tickers,
                progress_callback=capture_progress,
            )
            publication_seconds = perf_counter() - started
            db.commit()

            snapshots = list(
                db.scalars(
                    select(SetupSignalSnapshot)
                    .where(SetupSignalSnapshot.run_id == 7)
                    .order_by(SetupSignalSnapshot.ticker)
                )
            )
            evidences = list(
                db.scalars(
                    select(CoreCalculationEvidence)
                    .where(CoreCalculationEvidence.artifact_kind == "SETUP")
                    .order_by(CoreCalculationEvidence.ticker)
                )
            )
            retained_handoff = db.get(TransitionDecisionHandoffManifest, handoff_id)
            published_financial_fingerprint = CanonicalEvidenceSerializer.fingerprint(
                [
                    {
                        "ticker": row.ticker,
                        "payload": calculation_evidence_payload(
                            row,
                            excluded_columns=_SETUP_PROJECTION_FIELDS,
                        ),
                    }
                    for row in snapshots
                ]
            )
            financial_fingerprint = CanonicalEvidenceSerializer.fingerprint(
                [
                    {
                        "ticker": row.ticker,
                        "payload": row.payload_json,
                        "sources": row.source_evidence_ids_json,
                    }
                    for row in evidences
                ]
            )
            db.commit()

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
        maximum_transaction_age = max(transaction_ages, default=0.0)
        record_property("parent_session_id", parent_session_id)
        record_property("parent_backend_pid", parent_backend_pid)
        record_property("publication_seconds", round(publication_seconds, 6))
        record_property("maximum_transaction_age_seconds", round(maximum_transaction_age, 6))
        record_property("sql_seconds", round(sql_seconds, 6))
        record_property("query_count", query_count)
        record_property("duplicate_queries", duplicate_queries)
        record_property("configuration_loads", configuration_loads)
        record_property("evidence_loads", evidence_loads)
        record_property("flush_count", flushes)
        record_property(
            "prepared_financial_fingerprint",
            repository.prepared_financial_fingerprint,
        )
        record_property(
            "published_financial_fingerprint",
            published_financial_fingerprint,
        )
        record_property("financial_fingerprint", financial_fingerprint)
        record_testsuite_property(
            "task_2_9_12q_maximum_publication_transaction_seconds",
            f"{maximum_transaction_age:.6f}",
        )
        assert result.status == "COMPLETED"
        assert result.captured == 100
        assert len(snapshots) == 100
        assert len(evidences) == 100
        assert retained_handoff is not None
        assert retained_handoff.manifest_fingerprint == handoff_fingerprint
        assert repository.prepared_financial_fingerprint == published_financial_fingerprint
        assert progress[-1][1:] == (100, 100, "EVIDENCE")
        assert maximum_transaction_age <= 60
        assert query_count <= 4_500
        assert flushes <= 10
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor)
        event.remove(engine, "after_cursor_execute", after_cursor)
        event.remove(Session, "after_flush", after_flush)
        observer.dispose()
        engine.dispose()
