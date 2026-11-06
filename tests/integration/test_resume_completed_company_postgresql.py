"""Resume progress follows a complete immutable evidence set for each company."""

from decimal import Decimal

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session
from test_run26_handoff_regression_postgresql import (
    CUTOFF,
    OLDER_SESSION,
    _baseline_bar,
    _raw,
    _seal,
    _seed_core_ticker_artifacts,
    _seed_required_context,
    _seed_run_and_acquisition,
)

from alembic import command
from app.database_safety import configure_guarded_alembic
from app.models.tables import PipelineRun, TechnicalScore, TransitionDecisionHandoffManifest
from app.services import transition_preflight_plan_service as preflight
from app.services.canonical_evidence import CanonicalEvidenceSerializer
from app.services.configuration_delivery import resolve_pipeline_configurations
from app.services.core_calculation_evidence import CoreEvidenceKind
from app.services.market_calculation_context_service import cutoff_from_row
from app.services.pipeline_executor import (
    PipelineCancelled,
    _resume_validation_checkpoint_callback,
    _validate_resume_evidence,
)
from app.services.transition_preflight_plan_service import (
    freeze_transition_decision_handoff_manifest,
)


def _financial_records(db):
    tables = (
        "raw_company_rows",
        "fundamental_scores",
        "technical_scores",
        "combined_results",
        "ranking_results",
        "market_regime_snapshots",
        "sector_rotation_snapshots",
        "core_calculation_evidence",
        "transition_decision_handoff_manifests",
    )
    return {
        table: tuple(
            db.scalars(text(f'SELECT row_to_json(t)::text FROM "{table}" t ORDER BY id'))
        )
        for table in tables
    }


def _seed_sources(db):
    run, pipeline, context, _item = _seed_run_and_acquisition(db)
    market, sector, regime_config, sector_config = _seed_required_context(
        db, run.id, pipeline.id, context
    )
    configurations = resolve_pipeline_configurations(db)
    for index, ticker in enumerate(("AUTH", "NOBAR"), start=1):
        raw = _raw(db, run.id, index, ticker)
        _baseline_bar(db, ticker, OLDER_SESSION, CUTOFF, close="100")
        _seed_core_ticker_artifacts(db, raw, pipeline.id, context, configurations)
    _seal(db, market, CoreEvidenceKind.REGIME, effective_configuration=regime_config)
    _seal(
        db,
        sector,
        CoreEvidenceKind.SECTOR,
        sources={"market_regime": market},
        effective_configuration=sector_config,
    )
    db.commit()
    return run.id, pipeline.id, context


@pytest.mark.parametrize("outcome", ["complete", "tamper", "failure", "cancel"])
def test_resume_reports_each_fully_validated_company_and_preserves_sources(
    disposable_postgres_database, outcome
):
    config = Config("alembic.ini")
    configure_guarded_alembic(config, disposable_postgres_database)
    command.upgrade(config, "head")
    engine = create_engine(disposable_postgres_database)
    try:
        with Session(engine, expire_on_commit=False) as db:
            run_id, pipeline_id, context = _seed_sources(db)
            freeze_transition_decision_handoff_manifest(
                db, upload_run_id=run_id, market_cutoff=cutoff_from_row(context)
            )
            db.commit()
            if outcome == "tamper":
                changed = db.scalar(
                    select(TechnicalScore).where(
                        TechnicalScore.run_id == run_id, TechnicalScore.ticker == "NOBAR"
                    )
                )
                changed.dual_score = Decimal("76")
                db.commit()
            before = _financial_records(db)
            checkpoints = []

            def progress(_db, **checkpoint):
                checkpoints.append(checkpoint)
                assert checkpoint["last_completed_item"] == ("AUTH", "NOBAR")[
                    len(checkpoints) - 1
                ]
                assert checkpoint["processed"] == len(checkpoints)
                assert checkpoint["total"] == 2
                assert checkpoint["checkpoint_version"] == (
                    f"pipeline-resume-v1:RESUME_EVIDENCE_VALIDATED:{len(checkpoints)}"
                )
                assert _financial_records(db) == before
                with Session(engine) as observer:
                    assert _financial_records(observer) == before
                if outcome == "failure":
                    raise RuntimeError("resume completed company interrupted")

            callback = _resume_validation_checkpoint_callback(
                db,
                stage="FREEZING_DECISION_HANDOFF_MANIFEST",
                should_cancel=lambda: outcome == "cancel",
                progress_callback=progress,
            )
            pipeline = db.get(PipelineRun, pipeline_id)
            if outcome == "complete":
                result = _validate_resume_evidence(
                    db, pipeline=pipeline, upload_run_id=run_id, checkpoint_callback=callback
                )
                assert len(checkpoints) == 2
                assert result == _validate_resume_evidence(
                    db, pipeline=pipeline, upload_run_id=run_id
                )
            else:
                expected = {
                    "tamper": ValueError,
                    "failure": RuntimeError,
                    "cancel": PipelineCancelled,
                }[outcome]
                with pytest.raises(expected):
                    _validate_resume_evidence(
                        db,
                        pipeline=pipeline,
                        upload_run_id=run_id,
                        checkpoint_callback=callback,
                    )
                assert len(checkpoints) == (0 if outcome == "cancel" else 1)
            db.rollback()
            assert _financial_records(db) == before
    finally:
        engine.dispose()


@pytest.mark.parametrize("outcome", ["complete", "failure", "cancel"])
def test_lineage_progress_follows_all_company_hashes_before_publication(
    disposable_postgres_database, monkeypatch, outcome
):
    config = Config("alembic.ini")
    configure_guarded_alembic(config, disposable_postgres_database)
    command.upgrade(config, "head")
    engine = create_engine(disposable_postgres_database)
    hashed_rows = []
    native_identity = preflight._artifact_identity

    def observe_identity(row):
        result = native_identity(row)
        hashed_rows.append(row)
        return result

    monkeypatch.setattr(preflight, "_artifact_identity", observe_identity)
    try:
        with Session(engine, expire_on_commit=False) as db:
            run_id, _pipeline_id, context = _seed_sources(db)
            before = _financial_records(db)
            completed = []

            def checkpoint(**progress):
                if progress["phase"] != "ARTIFACT_LINEAGE_SERIALIZED":
                    return
                completed.append(progress)
                ticker = ("AUTH", "NOBAR")[len(completed) - 1]
                assert progress["processed"] == len(completed)
                assert progress["total"] == 2
                assert progress["current_item"] == progress["last_completed_item"] == ticker
                raw_index = max(
                    index
                    for index, row in enumerate(hashed_rows)
                    if type(row).__name__ == "RawCompanyRow"
                )
                company_rows = hashed_rows[raw_index:]
                assert company_rows[0].ticker == ticker
                assert [type(row).__name__ for row in company_rows] == [
                    "RawCompanyRow",
                    "FundamentalScore",
                    "TechnicalScore",
                    "CombinedResult",
                    "RankingResult",
                    "MarketRegimeSnapshot",
                    "SectorRotationSnapshot",
                    "SectorRotationRow",
                ]
                assert _financial_records(db) == before
                assert db.scalar(
                    select(func.count()).select_from(TransitionDecisionHandoffManifest)
                ) == 0
                with Session(engine) as observer:
                    assert _financial_records(observer) == before
                if outcome == "failure":
                    raise RuntimeError("completed lineage interrupted")
                if outcome == "cancel":
                    raise PipelineCancelled("completed lineage cancelled")

            if outcome == "complete":
                handoff = freeze_transition_decision_handoff_manifest(
                    db,
                    upload_run_id=run_id,
                    market_cutoff=cutoff_from_row(context),
                    checkpoint_callback=checkpoint,
                )
                assert len(completed) == 2
                assert CanonicalEvidenceSerializer.fingerprint(handoff.manifest_json) == (
                    handoff.manifest_fingerprint
                )
                with Session(engine) as observer:
                    assert _financial_records(observer) == before
            else:
                expected = RuntimeError if outcome == "failure" else PipelineCancelled
                with pytest.raises(expected):
                    freeze_transition_decision_handoff_manifest(
                        db,
                        upload_run_id=run_id,
                        market_cutoff=cutoff_from_row(context),
                        checkpoint_callback=checkpoint,
                    )
                assert len(completed) == 1
            db.rollback()
            assert _financial_records(db) == before
    finally:
        engine.dispose()
