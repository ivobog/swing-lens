"""Measure native capture and durable pipeline delivery at real populations."""

import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import pytest
import test_contextual_configuration_adoption_postgresql as contextual
from sqlalchemy import event, select
from sqlalchemy.orm import Session
from test_ceri_batched_workflow_v2 import _execute_handler, _new_job

from app.models.ceri_tables import CeriChangeEvent, CeriScoreSnapshot
from app.models.tables import PipelineRun, RawCompanyRow, UploadRun
from app.services.background_job_service import JobStatus
from app.services.ceri.capture_service import CeriRunCaptureService
from app.services.ceri.job_handlers import execute_capture_run_job
from app.services.ceri.snapshot_service import CeriSnapshotService
from app.services.contextual_effective_configuration import resolve_ceri_configuration
from app.services.market_calculation_context_service import create_pipeline_market_context
from app.services.market_clock_service import MarketClockService

contextual_engine = contextual.contextual_engine
pytestmark = [pytest.mark.integration, pytest.mark.destructive]


@pytest.fixture(scope="module")
def capture_authority_measurements():
    return {}


@pytest.mark.parametrize("population", [1, 50])
@pytest.mark.parametrize("delivery", ["capture", "pipeline", "material"])
def test_capture_authority_scaling(
    contextual_engine,
    population,
    delivery,
    monkeypatch,
    record_property,
    capture_authority_measurements,
):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "e2e"))
    from single_run_certification import fixtures

    tickers = ("ALFA",) + tuple(f"T14DC{i:03}" for i in range(population - 1))
    monkeypatch.setattr(fixtures, "CANONICAL_TICKERS", tickers)
    monkeypatch.setenv("CERI_ENABLED", "true")
    monkeypatch.setenv("CERI_RUN_CAPTURE_ENABLED", "true")
    monkeypatch.setenv("CERI_ALERTS_ENABLED", "true")
    from app.settings import get_settings

    get_settings.cache_clear()
    statements = Counter()
    authority = Counter()
    calls = Counter()
    names = {
        "_required_rows",
        "_rows_where",
        "validate_score_source",
        "_normalized_source",
        "_source_value",
        "validate_retained_decision",
        "_validate_configuration",
        "load_configuration_delivery",
        "validate_core_mutation_authority",
        "core_mutation_context",
        "validate_alert_source",
        "validate_change_source",
        "validate_rule",
        "validate_notification",
        "operational_decision_authority",
        "_eligible_source_backed_rows",
        "assert_current_execution_ownership",
        "resolve_pipeline_market_context",
        "_capture_source_bundle",
        "_prior_snapshot",
        "effective_disposition_by_snapshot",
    }

    def observe(_conn, _cursor, sql, _params, _context, _many):
        kind = sql.lstrip().split(None, 1)[0].upper()
        statements[kind] += 1
        if kind != "SELECT":
            return
        frame = sys._getframe().f_back
        while frame is not None:
            if frame.f_code.co_name == "load" and frame.f_code.co_filename.endswith(
                "source_mutation_authority.py"
            ):
                authority["PrefetchedSourceBodies.load"] += 1
                break
            if frame.f_code.co_name in names:
                authority[frame.f_code.co_name] += 1
                break
            frame = frame.f_back

    try:
        with Session(contextual_engine) as db:
            cutoff = MarketClockService().cutoff_for(
                datetime.now(UTC), reason="T14D_CAPTURE_POPULATION"
            )
            fixtures._seed_ceri_manual_evidence(
                db,
                as_of_session=cutoff.latest_completed_session,
                baseline_tickers=tickers if delivery == "material" else ("ALFA",),
            )
            db.commit()
            cutoff = MarketClockService().cutoff_for(
                datetime.now(UTC), reason="T14D_CAPTURE_AFTER_SOURCE_ACQUISITION"
            )
            run = UploadRun(
                filename=f"capture-{delivery}-{population}.csv",
                status="COMPLETED",
                row_count=population,
            )
            db.add(run)
            db.flush()
            db.add_all(
                RawCompanyRow(run_id=run.id, row_number=i + 1, ticker=ticker, raw_json={})
                for i, ticker in enumerate(tickers)
            )
            db.commit()
            snapshots = CeriSnapshotService()
            frozen = resolve_ceri_configuration(
                snapshots.config,
                consumer={
                    "run_capture": True,
                    "revision_feature_config_hash": snapshots.config.config_hash,
                    "ibmi_enabled": False,
                    "volatility_enabled": False,
                    "short_pressure_enabled": False,
                    "volatility": {"ceri_risk_max_contribution": 1.5},
                },
            )
            if delivery == "pipeline":
                pipeline = PipelineRun(upload_run_id=run.id, status="RUNNING")
                db.add(pipeline)
                db.flush()
                cutoff = create_pipeline_market_context(db, pipeline, cutoff_at=cutoff.cutoff_at)
            from app.services.ceri.feature_rebuild_service import (
                CeriFeatureRebuildRequest,
                CeriFeatureRebuildService,
            )

            prepared = CeriFeatureRebuildService().rebuild(
                db,
                CeriFeatureRebuildRequest(
                    run_id=run.id,
                    as_of_session=cutoff.latest_completed_session,
                    cutoff_at=cutoff.cutoff_at,
                    calculation_context_id=cutoff.context_id,
                    calendar_version=cutoff.calendar_version,
                    ownership_mode="PIPELINE" if delivery == "pipeline" else "STANDALONE",
                ),
            )
            assert prepared.failed == 0 and prepared.companies_rebuilt == population, (
                prepared.as_dict()
            )
            db.commit()
            if delivery == "pipeline":
                job = _new_job(
                    db,
                    job_type="CERI_CAPTURE_RUN",
                    status=JobStatus.RUNNING,
                    related_run_id=run.id,
                    request_key=f"capture-scaling-{population}",
                    payload_json={
                        "run_id": run.id,
                        "pipeline_run_id": pipeline.id,
                        "cutoff_at": cutoff.cutoff_at.isoformat(),
                        "as_of_session": cutoff.latest_completed_session.isoformat(),
                        "calculation_context_id": cutoff.context_id,
                        "calendar_version": cutoff.calendar_version,
                    },
                )
            event.listen(contextual_engine, "before_cursor_execute", observe)
            from app.services.ceri import change_authority

            real_persist = CeriSnapshotService.persist_snapshot
            real_validate = change_authority.validate_score_source

            def counted_persist(*args, **kwargs):
                calls["persist_snapshot"] += 1
                return real_persist(*args, **kwargs)

            def counted_validate(*args, **kwargs):
                calls["validate_score_source"] += 1
                return real_validate(*args, **kwargs)

            monkeypatch.setattr(CeriSnapshotService, "persist_snapshot", counted_persist)
            monkeypatch.setattr(change_authority, "validate_score_source", counted_validate)
            started = perf_counter()
            try:
                if delivery != "pipeline":
                    result = CeriRunCaptureService(snapshot_service=snapshots).capture_run(
                        db, run.id, force=True, market_cutoff=cutoff, effective_configuration=frozen
                    )
                    assert result.failed == 0 and result.score_snapshots == population, result
                    assert result.alerts == (population if delivery == "material" else 1), result
                    db.commit()
                else:
                    result = _execute_handler(db, job, execute_capture_run_job)
                    assert result["score_snapshots"] == population, result
                    assert result["alerts"] == 1, result
            finally:
                duration = perf_counter() - started
                event.remove(contextual_engine, "before_cursor_execute", observe)
            record_property("population", population)
            record_property("delivery", delivery)
            record_property("wall_seconds", duration)
            record_property("selects", statements["SELECT"])
            record_property("total_sql", sum(statements.values()))
            record_property("authority_selects", sum(authority.values()))
            record_property("authority_by_function", dict(authority))
            record_property("authority_validations", calls["validate_score_source"])
            record_property("writer_invocations", calls["persist_snapshot"])
            assert calls["persist_snapshot"] == population
            assert all(
                score.evidence_id is not None
                for score in db.scalars(
                    select(CeriScoreSnapshot).where(CeriScoreSnapshot.run_id == run.id)
                )
            )
            if delivery == "material":
                score_ids = set(
                    db.scalars(
                        select(CeriScoreSnapshot.id).where(CeriScoreSnapshot.run_id == run.id)
                    )
                )
                material = list(
                    db.scalars(
                        select(CeriChangeEvent).where(CeriChangeEvent.to_snapshot_id.in_(score_ids))
                    )
                )
                assert len({change.company_id for change in material}) == population
                assert all(change.comparison_state == "COMPARABLE" for change in material)
                record_property("material_changes", len(material))
            capture_authority_measurements[delivery, population] = sum(authority.values())
            if population == 50:
                assert (delivery, 1) in capture_authority_measurements, (
                    "Population 1 baseline required"
                )
                baseline = capture_authority_measurements[delivery, 1]
                # The same admitted tables and native branches must not add
                # authority lookups when the writer population grows fiftyfold.
                assert sum(authority.values()) <= baseline, dict(authority)
    finally:
        get_settings.cache_clear()
