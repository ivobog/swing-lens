from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models.tables import (
    PriceBar,
    UploadRun,
    WinnerCohortDefinition,
    WinnerCohortGeneration,
    WinnerCohortRefreshState,
    WinnerCohortStatistic,
    WinnerEvidenceManifest,
    WinnerEvidenceManifestMember,
    WinnerForwardOutcome,
    WinnerOutcomeDefinition,
    WinnerPredictionSnapshot,
    WinnerTargetStopOutcome,
    WinnerTemporalValidityDecision,
)
from app.services.winner_probability.cohort_generation_service import (
    CohortGenerationService,
    GenerationInvariantViolation,
)
from app.services.winner_probability.config import load_winner_probability_config
from app.services.winner_probability.evidence_service import EvidenceService
from app.services.winner_probability.outcome_orchestration_service import (
    H5NextOpenOrchestrationService,
)
from app.services.winner_probability.outcome_service import WinnerOutcomeRepository
from app.services.winner_probability.temporal_validation_service import (
    TemporalQuarantineItem,
    TemporalValidationService,
)


def test_upgrade_0056_through_temporal_head_and_append_only_ledger(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database, "0056_cover_ceri_freshness")
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    schema = inspect(engine)

    assert "decision_at" in {
        column["name"] for column in schema.get_columns("winner_prediction_snapshots")
    }
    assert "winner_temporal_validity_decisions" in schema.get_table_names()

    with Session(engine) as db:
        prediction = _prediction(db, ticker="AUDIT")
        event = TemporalValidationService().record(
            db,
            prediction=prediction,
            decision_at=datetime(2026, 8, 20, 12, 0, tzinfo=UTC),
            entry_session=date(2026, 8, 20),
            semantic_input_time_valid=True,
            evaluated_by="TEST",
        )
        db.commit()
        event_id = event.id

    with Session(engine) as db:
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(
                text(
                    "UPDATE winner_temporal_validity_decisions "
                    "SET evaluated_by = 'MUTATED' WHERE id = :id"
                ),
                {"id": event_id},
            )
            db.commit()
        db.rollback()
        assert (
            db.scalar(
                select(WinnerTemporalValidityDecision).where(
                    WinnerTemporalValidityDecision.id == event_id
                )
            ).evaluated_by
            == "TEST"
        )
    engine.dispose()


def test_invalid_pending_outcome_is_ignored_even_when_due_and_valid_peer_is_selected(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        invalid = _prediction(db, ticker="INVALID")
        valid = _prediction(db, ticker="VALID")
        unresolved = _prediction(db, ticker="UNRESOLVED")
        validator = TemporalValidationService()
        original_entry_session = invalid.planned_entry_session
        plan = validator.plan_quarantine(
            db,
            items=(
                TemporalQuarantineItem(
                    prediction_id=invalid.id,
                    decision_at=datetime(2026, 8, 20, 15, 28, tzinfo=UTC),
                    entry_session=date(2026, 8, 20),
                    semantic_input_time_valid=None,
                    incident_reason="PROVEN_HISTORICAL_INCIDENT",
                ),
            ),
        )
        quarantine = validator.apply_quarantine(
            db,
            plan=plan,
            expected_manifest_hash=plan.manifest_hash,
            actor="TEST_QUARANTINE",
            request_key="test-quarantine-1",
            approve_write=True,
        )
        assert quarantine.inserted_count == 1
        assert invalid.planned_entry_session == original_entry_session
        validator.record(
            db,
            prediction=valid,
            decision_at=datetime(2026, 8, 20, 12, 0, tzinfo=UTC),
            entry_session=date(2026, 8, 20),
            semantic_input_time_valid=True,
            evaluated_by="TEST_CAPTURE",
        )
        for prediction in (invalid, valid, unresolved):
            db.add(
                WinnerForwardOutcome(
                    prediction_id=prediction.id,
                    entry_model="NEXT_OPEN",
                    horizon_sessions=5,
                    entry_session=date(2026, 8, 20),
                    due_session=date(2026, 8, 26),
                    status="PENDING",
                    revision=1,
                    is_current_revision=True,
                    metadata_json={},
                )
            )
        for bar_date in (
            date(2026, 8, 20),
            date(2026, 8, 21),
            date(2026, 8, 24),
            date(2026, 8, 25),
            date(2026, 8, 26),
        ):
            db.add(
                PriceBar(
                    ticker=invalid.ticker,
                    bar_date=bar_date,
                    timeframe="1 day",
                    open=Decimal("100"),
                    high=Decimal("102"),
                    low=Decimal("99"),
                    close=Decimal("101"),
                    volume=Decimal("1000000"),
                    source="TEST",
                    what_to_show="ADJUSTED_LAST",
                )
            )
        db.commit()

        selected = WinnerOutcomeRepository().get_due_pending_forward_outcomes(
            db,
            completed_on=date(2026, 8, 27),
            limit=10,
            entry_model="NEXT_OPEN",
            horizon_sessions=5,
            retry_as_of=datetime(2026, 8, 27, tzinfo=UTC),
        )

        assert [row.prediction_id for row in selected] == [valid.id]
        queue = H5NextOpenOrchestrationService().queue_state(
            db,
            now=datetime(2026, 8, 27, 22, 0, tzinfo=UTC),
        )
        assert queue.due_total == 1
        assert queue.retry_eligible_now == 1
        assert (
            db.scalar(
                select(WinnerForwardOutcome.status).where(
                    WinnerForwardOutcome.prediction_id == invalid.id
                )
            )
            == "PENDING"
        )

        generation = _generation_with_members(db, invalid=invalid, valid=valid)
        audit = CohortGenerationService.audit_temporal_integrity(db, generation=generation)
        assert audit.invalid_prediction_ids == (invalid.id,)
        with pytest.raises(GenerationInvariantViolation, match="temporally ineligible"):
            CohortGenerationService._assert_temporally_clean(db, generation)

        definition = db.scalar(select(WinnerOutcomeDefinition))
        forwards = list(db.scalars(select(WinnerForwardOutcome)))
        for ordinal, forward in enumerate(forwards):
            forward.status = "MATURED"
            forward.matured_at = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
            forward.source_revision_cutoff_at = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
            db.add(
                WinnerTargetStopOutcome(
                    prediction_id=forward.prediction_id,
                    outcome_definition_id=definition.id,
                    forward_outcome_id=forward.id,
                    entry_model=forward.entry_model,
                    horizon_sessions=forward.horizon_sessions,
                    status="MATURED",
                    revision=1,
                    is_current_revision=True,
                    target_pct=definition.target_pct,
                    stop_pct=definition.stop_pct,
                    target_hit=bool(ordinal),
                    stop_hit=not bool(ordinal),
                    first_event="TARGET_FIRST" if ordinal else "STOP_FIRST",
                    primary_winner=bool(ordinal),
                    optimistic_winner=bool(ordinal),
                    conservative_winner=bool(ordinal),
                    evaluated_at=datetime(2026, 8, 27, 12, 0, tzinfo=UTC),
                    metadata_json={},
                )
            )
        db.flush()
        targets = list(db.scalars(select(WinnerTargetStopOutcome)))
        evidence = EvidenceService().load_generation_evidence(
            db,
            outcome_definition=definition,
            training_cutoff_at=datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
            config=load_winner_probability_config(),
            watermark={
                "forward_revision_id": max(row.id for row in forwards),
                "target_stop_revision_id": max(row.id for row in targets),
                "eligibility_decision_id": 0,
                "training_replay_id": 0,
                "temporal_validity_decision_id": db.scalar(
                    select(WinnerTemporalValidityDecision.id).order_by(
                        WinnerTemporalValidityDecision.id.desc()
                    )
                ),
            },
        )
        assert [row.prediction.id for row in evidence.evidence] == [valid.id]
    engine.dispose()


def _prediction(db: Session, *, ticker: str) -> WinnerPredictionSnapshot:
    config = load_winner_probability_config()
    upload = UploadRun(
        filename=f"{ticker}.csv",
        uploaded_at=datetime(2026, 8, 19, 21, 0, tzinfo=UTC),
        status="COMPLETED",
    )
    db.add(upload)
    db.flush()
    prediction = WinnerPredictionSnapshot(
        run_id=upload.id,
        ticker=ticker,
        prediction_as_of_date=date(2026, 8, 19),
        source_data_cutoff_at=datetime(2026, 8, 19, 21, 0, tzinfo=UTC),
        decision_at=datetime(2026, 8, 20, 12, 0, tzinfo=UTC),
        captured_at=datetime(2026, 8, 20, 12, 1, tzinfo=UTC),
        planned_entry_session=date(2026, 8, 20),
        entry_schedule_status="RESOLVED",
        entry_data_status="NOT_DUE",
        eligibility_status="ELIGIBLE",
        feature_schema_version=config.feature_schema.version,
        feature_vector_hash=f"hash-{ticker}",
        config_hash=config.config_hash,
        calculation_version=config.engine.calculation_version,
        feature_json={"setup_family": "breakout"},
        source_ids_json={},
        warning_flags_json=[],
        lineage_json={
            "point_in_time_validated": True,
            "capture_training_candidate": True,
            "evidence_training_eligible": True,
            "training_rejection_reasons": [],
        },
    )
    db.add(prediction)
    db.flush()
    return prediction


def _generation_with_members(
    db: Session,
    *,
    invalid: WinnerPredictionSnapshot,
    valid: WinnerPredictionSnapshot,
) -> WinnerCohortGeneration:
    config = load_winner_probability_config()
    raw = config.primary_outcome_definition
    definition = WinnerOutcomeDefinition(
        definition_id=raw.id,
        label="Temporal gate",
        entry_model=raw.entry_model,
        horizon_sessions=raw.horizon_sessions,
        target_pct=raw.target_pct,
        stop_pct=raw.stop_pct,
        same_bar_conflict_policy=raw.same_bar_conflict_policy,
        calculation_version=config.engine.calculation_version,
        config_hash=config.config_hash,
        is_primary=True,
        is_active=True,
        metadata_json={},
    )
    db.add(definition)
    db.flush()
    state = WinnerCohortRefreshState(
        outcome_definition_id=definition.id,
        feature_schema_version=config.feature_schema.version,
        calculation_version=config.engine.calculation_version,
        config_hash=config.config_hash,
        eligibility_policy_version="test",
        compatibility_policy_version="test",
        cohort_algorithm_version="test",
        desired_watermark_hash="watermark",
    )
    db.add(state)
    db.flush()
    generation = WinnerCohortGeneration(
        generation_key="temporal-gate",
        refresh_state_id=state.id,
        outcome_definition_id=definition.id,
        watermark_hash="watermark",
        watermark_json={},
        feature_schema_version=config.feature_schema.version,
        calculation_version=config.engine.calculation_version,
        config_hash=config.config_hash,
        eligibility_policy_version="test",
        compatibility_policy_version="test",
        cohort_algorithm_version="test",
        status="READY",
        training_cutoff_at=datetime(2026, 8, 27, tzinfo=UTC),
        requested_at=datetime(2026, 8, 27, tzinfo=UTC),
        planned_group_count=1,
        completed_group_count=1,
        checkpoint_json={},
        metrics_json={},
    )
    cohort = WinnerCohortDefinition(
        cohort_key="L5:test",
        level="L5",
        outcome_definition_id=definition.id,
        entry_model=definition.entry_model,
        dimensions_json={"global": "all"},
        feature_schema_version=config.feature_schema.version,
        config_hash=config.config_hash,
        source_version="test",
        status="ACTIVE",
    )
    manifest = WinnerEvidenceManifest(
        manifest_hash="temporal-manifest",
        hash_algorithm="sha256",
        content_encoding="json",
        member_count=2,
        payload_json={},
    )
    db.add_all([generation, cohort, manifest])
    db.flush()
    db.add(
        WinnerCohortStatistic(
            cohort_definition_id=cohort.id,
            outcome_definition_id=definition.id,
            generation_id=generation.id,
            evidence_manifest_id=manifest.id,
            statistic_as_of=datetime(2026, 8, 27, tzinfo=UTC),
            training_cutoff_at=datetime(2026, 8, 27, tzinfo=UTC),
            sample_n=2,
            effective_n=2,
            wins=1,
            evidence_grade="Insufficient",
            config_hash=config.config_hash,
            evidence_manifest_hash=manifest.manifest_hash,
            metadata_json={},
        )
    )
    forwards = {
        row.prediction_id: row
        for row in db.scalars(
            select(WinnerForwardOutcome).where(
                WinnerForwardOutcome.prediction_id.in_([invalid.id, valid.id])
            )
        )
    }
    for ordinal, prediction in enumerate((invalid, valid)):
        forward = forwards[prediction.id]
        db.add(
            WinnerEvidenceManifestMember(
                manifest_id=manifest.id,
                member_ordinal=ordinal,
                prediction_id=prediction.id,
                forward_outcome_id=forward.id,
                forward_revision=forward.revision,
                target_stop_outcome_id=ordinal + 1,
                target_stop_revision=1,
                evidence_origin="NATIVE_1_1",
                inclusion_weight=1,
                primary_winner=bool(ordinal),
                member_hash=f"member-{ordinal}",
            )
        )
    db.flush()
    return generation


def _upgrade(database_url: str, revision: str = "head") -> None:
    env = {**os.environ, "DATABASE_URL": database_url}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
