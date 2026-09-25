from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import replace
from datetime import timedelta

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from test_winner_maturation_canary_postgresql import (
    _native_operation_at,
    _seed_native_ready_outcome,
)

from app.models.tables import (
    WinnerCohortGeneration,
    WinnerCohortRefreshState,
    WinnerCohortStatistic,
    WinnerEvidenceManifestMember,
    WinnerForwardOutcome,
    WinnerOutcomeDefinition,
    WinnerPredictionEpisode,
    WinnerProbabilityEstimate,
    WinnerTargetStopOutcome,
)
from app.services.winner_probability.cohort_generation_service import (
    CohortGenerationService,
    CohortGenerationStatus,
    EvidenceWatermarkService,
    GenerationPublicationStatus,
    contract_for,
)
from app.services.winner_probability.cohort_materialization_service import (
    CohortMaterializationService,
)
from app.services.winner_probability.config import (
    load_winner_probability_config,
    winner_probability_config_hash,
)
from app.services.winner_probability.outcome_service import OutcomeMaturationService


def test_native_capture_maturation_materializes_and_publishes_financial_generation(
    disposable_postgres_database: str,
) -> None:
    """Prove positive publication from native capture through serving projection."""

    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    config = _material_config()
    with Session(engine) as db:
        forward_id = _seed_native_ready_outcome(
            db,
            ticker="MATERIAL",
            run_suffix="positive-publication",
        )
        operation_at = _native_operation_at(db, forward_id)
        result = OutcomeMaturationService().process_forward_outcome(
            db,
            db.get(WinnerForwardOutcome, forward_id),
            now=operation_at,
        )
        assert result.processed == result.matured == 1
        assert result.target_stop_matured == 1
        db.commit()

        forward = db.get(WinnerForwardOutcome, forward_id)
        target = db.scalar(
            select(WinnerTargetStopOutcome).where(
                WinnerTargetStopOutcome.forward_outcome_id == forward.id,
                WinnerTargetStopOutcome.is_current_revision.is_(True),
            )
        )
        definition = db.get(WinnerOutcomeDefinition, target.outcome_definition_id)
        observed_at = operation_at + timedelta(seconds=1)
        advance = EvidenceWatermarkService().advance_to_current_material_evidence(
            db,
            outcome_definition=definition,
            config=config,
            observed_at=observed_at,
        )
        assert advance.advanced
        generation = CohortGenerationService().capture_or_resume(
            db,
            state=advance.state,
            contract=contract_for(definition, config),
            requested_at=observed_at + timedelta(microseconds=1),
            config=config,
        )
        db.commit()

        publication = CohortMaterializationService().materialize_slice(
            db,
            generation=generation,
            outcome_definition=definition,
            config=config,
            lease_guard=lambda: None,
            should_cancel=lambda: False,
            operation_at=observed_at + timedelta(microseconds=2),
        )
        assert publication.status == CohortGenerationStatus.PUBLISHED
        assert publication.publication_status == GenerationPublicationStatus.PUBLISHED
        assert publication.evidence_rows_loaded == 1
        db.commit()

        generation = db.get(WinnerCohortGeneration, generation.id)
        state = db.get(WinnerCohortRefreshState, generation.refresh_state_id)
        manifest_ids = select(WinnerCohortStatistic.evidence_manifest_id).where(
            WinnerCohortStatistic.generation_id == generation.id
        )
        assert generation.status == CohortGenerationStatus.PUBLISHED
        assert generation.evidence_row_count == 1
        assert generation.root_manifest_hash
        assert state.published_generation_id == generation.id
        assert state.published_watermark_hash == generation.watermark_hash
        assert generation.metrics_json["evidence_funnel"]["certified_financial_population"] == 1
        assert (
            db.scalar(
                select(func.count(WinnerEvidenceManifestMember.id)).where(
                    WinnerEvidenceManifestMember.manifest_id.in_(manifest_ids)
                )
            )
            > 0
        )
        assert db.scalar(select(func.count(WinnerPredictionEpisode.id))) == 1
        assert db.scalar(
            select(func.count(WinnerProbabilityEstimate.id)).where(
                WinnerProbabilityEstimate.prediction_id == forward.prediction_id,
                WinnerProbabilityEstimate.estimate_kind == "DECISION_TIME",
            )
        )

        repeated = CohortMaterializationService().materialize_slice(
            db,
            generation=generation,
            outcome_definition=definition,
            config=config,
            lease_guard=lambda: None,
            should_cancel=lambda: False,
            operation_at=observed_at + timedelta(seconds=2),
        )
        assert repeated.publication_status == GenerationPublicationStatus.ALREADY_ACTIVE
        assert (
            db.scalar(
                select(func.count(WinnerCohortGeneration.id)).where(
                    WinnerCohortGeneration.status == CohortGenerationStatus.PUBLISHED,
                    WinnerCohortGeneration.refresh_state_id == state.id,
                )
            )
            == 1
        )
    engine.dispose()


def _material_config():
    config = load_winner_probability_config()
    config = replace(
        config,
        engine=replace(config.engine, enabled=True),
        outcome_definitions=tuple(
            replace(definition, target_pct=5.0, stop_pct=5.0)
            for definition in config.outcome_definitions
        ),
    )
    return replace(config, config_hash=winner_probability_config_hash(config))


def _upgrade(database_url: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env={**os.environ, "DATABASE_URL": database_url},
        check=True,
        capture_output=True,
        text=True,
    )
