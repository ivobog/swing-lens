from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum

import pytest
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models.tables import (
    IBContract,
    PriceBar,
    UploadRun,
    WinnerCohortDefinition,
    WinnerCohortGeneration,
    WinnerCohortRefreshState,
    WinnerCohortStatistic,
    WinnerEvidenceManifest,
    WinnerEvidenceManifestMember,
    WinnerForwardOutcome,
    WinnerMarketDataObligation,
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
from app.services.winner_probability.market_data_obligation_service import (
    MarketDataObligationService,
    global_daily_bar_lag,
    required_outcome_sessions,
)
from app.services.winner_probability.outcome_orchestration_service import (
    H5NextOpenOrchestrationService,
)
from app.services.winner_probability.outcome_service import WinnerOutcomeRepository
from app.services.winner_probability.temporal_validation_service import (
    TemporalCertificationItem,
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
        db.flush()
        valid_forward = db.scalar(
            select(WinnerForwardOutcome).where(
                WinnerForwardOutcome.prediction_id == valid.id
            )
        )
        db.add(
            WinnerMarketDataObligation(
                prediction_id=valid.id,
                forward_outcome_id=valid_forward.id,
                ticker_snapshot=valid.ticker,
                entry_session=date(2026, 8, 20),
                required_through_session=date(2026, 8, 26),
                required_sessions_json=[
                    "2026-08-20",
                    "2026-08-21",
                    "2026-08-24",
                    "2026-08-25",
                    "2026-08-26",
                ],
                timeframe="1 day",
                what_to_show="ADJUSTED_LAST",
                status="SATISFIED",
                price_series_watermark="test",
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


class _MetadataClassification(StrEnum):
    EXECUTION_INVALID = "EXECUTION_INVALID"


def test_quarantine_bulk_metadata_is_json_safe_and_round_trips_exactly(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        first = _prediction(db, ticker="JSON1")
        second = _prediction(db, ticker="JSON2")
        metadata = {
            "entry_open_at": datetime.fromisoformat("2026-08-20T09:30:00-04:00"),
            "source_date": date(2026, 8, 19),
            "classification": _MetadataClassification.EXECUTION_INVALID,
            "weight": Decimal("1.2300"),
            "proven": True,
            "unknown": None,
            "run_id": 120,
            "ticker": "JSON1",
            "nested": {
                "observed_at": datetime(2026, 8, 20, 13, 30, 0, 123456, tzinfo=UTC),
                "values": [date(2026, 8, 18), Decimal("2.500")],
            },
        }
        service = TemporalValidationService()
        plan = service.plan_quarantine(
            db,
            items=(
                TemporalQuarantineItem(
                    prediction_id=first.id,
                    decision_at=datetime(2026, 8, 20, 15, 28, tzinfo=UTC),
                    entry_session=date(2026, 8, 20),
                    semantic_input_time_valid=False,
                    incident_reason="PROVEN_HISTORICAL_INCIDENT",
                    metadata=metadata,
                ),
                TemporalQuarantineItem(
                    prediction_id=second.id,
                    decision_at=datetime(2026, 8, 20, 15, 29, tzinfo=UTC),
                    entry_session=date(2026, 8, 20),
                    semantic_input_time_valid=None,
                    incident_reason="PROVEN_HISTORICAL_INCIDENT",
                    metadata={"ticker": "JSON2"},
                ),
            ),
        )

        result = service.apply_quarantine(
            db,
            plan=plan,
            expected_manifest_hash=plan.manifest_hash,
            actor="TEST_QUARANTINE",
            request_key="json-safe-bulk",
            approve_write=True,
        )
        db.commit()

        assert result.inserted_count == 2
        stored = db.scalar(
            select(WinnerTemporalValidityDecision).where(
                WinnerTemporalValidityDecision.prediction_id == first.id
            )
        )
        assert stored.metadata_json == {
            "entry_open_at": "2026-08-20T13:30:00.000000Z",
            "source_date": "2026-08-19",
            "classification": "EXECUTION_INVALID",
            "weight": "1.2300",
            "proven": True,
            "unknown": None,
            "run_id": 120,
            "ticker": "JSON1",
            "nested": {
                "observed_at": "2026-08-20T13:30:00.123456Z",
                "values": ["2026-08-18", "2.500"],
            },
            "request_key": "json-safe-bulk",
            "manifest_hash": plan.manifest_hash,
        }
    engine.dispose()


def test_quarantine_bulk_rejects_unsupported_metadata_atomically_with_path(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        first = _prediction(db, ticker="ATOMIC1")
        second = _prediction(db, ticker="ATOMIC2")
        first_metadata: dict = {"ticker": "ATOMIC1"}
        second_metadata: dict = {"ticker": "ATOMIC2"}
        service = TemporalValidationService()
        plan = service.plan_quarantine(
            db,
            items=(
                TemporalQuarantineItem(
                    prediction_id=first.id,
                    decision_at=datetime(2026, 8, 20, 15, 28, tzinfo=UTC),
                    entry_session=date(2026, 8, 20),
                    semantic_input_time_valid=False,
                    incident_reason="PROVEN_HISTORICAL_INCIDENT",
                    metadata=first_metadata,
                ),
                TemporalQuarantineItem(
                    prediction_id=second.id,
                    decision_at=datetime(2026, 8, 20, 15, 29, tzinfo=UTC),
                    entry_session=date(2026, 8, 20),
                    semantic_input_time_valid=False,
                    incident_reason="PROVEN_HISTORICAL_INCIDENT",
                    metadata=second_metadata,
                ),
            ),
        )
        second_metadata["nested"] = {"bad_value": object()}

        with pytest.raises(TypeError, match=r"metadata\.nested\.bad_value.*object"):
            service.apply_quarantine(
                db,
                plan=plan,
                expected_manifest_hash=plan.manifest_hash,
                actor="TEST_QUARANTINE",
                request_key="atomic-bulk",
                approve_write=True,
            )
        db.rollback()

        assert db.scalar(select(func.count()).select_from(WinnerTemporalValidityDecision)) == 0
    engine.dispose()


def test_quarantine_full_historical_cardinality_uses_one_atomic_bulk_path(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        predictions = [_prediction(db, ticker=f"BULK{index:04d}") for index in range(1292)]
        service = TemporalValidationService()
        plan = service.plan_quarantine(
            db,
            items=tuple(
                TemporalQuarantineItem(
                    prediction_id=prediction.id,
                    decision_at=datetime(2026, 8, 20, 15, 28, tzinfo=UTC),
                    entry_session=date(2026, 8, 20),
                    semantic_input_time_valid=False if index < 1114 else None,
                    incident_reason="PROVEN_HISTORICAL_INCIDENT",
                    metadata={
                        "outcome_id": 10_000 + index,
                        "entry_open_at": datetime.fromisoformat("2026-08-20T09:30:00-04:00"),
                    },
                )
                for index, prediction in enumerate(predictions)
            ),
        )

        result = service.apply_quarantine(
            db,
            plan=plan,
            expected_manifest_hash=plan.manifest_hash,
            actor="TEST_QUARANTINE",
            request_key="full-cardinality-bulk",
            approve_write=True,
        )
        db.commit()

        assert result.inserted_count == 1292
        assert db.scalar(select(func.count()).select_from(WinnerTemporalValidityDecision)) == 1292
        assert (
            db.scalar(
                select(func.count())
                .select_from(WinnerTemporalValidityDecision)
                .where(WinnerTemporalValidityDecision.evidence_eligible.is_(False))
            )
            == 1292
        )
        first = db.scalar(
            select(WinnerTemporalValidityDecision).order_by(
                WinnerTemporalValidityDecision.prediction_id
            )
        )
        assert first.metadata_json["entry_open_at"] == "2026-08-20T13:30:00.000000Z"
        assert all(
            prediction.planned_entry_session == date(2026, 8, 20) for prediction in predictions
        )
    engine.dispose()


def test_positive_temporal_certification_is_deterministic_append_only_and_does_not_rewrite(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        prediction = _prediction(db, ticker="CERTIFIED")
        prediction.decision_at = None
        original_capture = prediction.captured_at
        original_entry = prediction.planned_entry_session
        service = TemporalValidationService()
        item = TemporalCertificationItem(
            prediction_id=prediction.id,
            decision_at=prediction.captured_at,
            entry_session=prediction.planned_entry_session,
            semantic_input_time_valid=True,
            certification_reason="HISTORICAL_DURABLE_LINEAGE_VERIFIED",
            metadata={"feature_cutoff_audit_hash": "abc123", "outcome_id": 91},
        )

        first = service.plan_certification(db, items=(item,))
        second = service.plan_certification(db, items=(item,))
        assert first.manifest_hash == second.manifest_hash
        assert first.valid_count == first.item_count == 1

        result = service.apply_certification(
            db,
            plan=first,
            expected_manifest_hash=first.manifest_hash,
            actor="TEST_CERTIFIER",
            request_key="positive-certification-1",
            approve_write=True,
        )
        db.commit()

        assert result.inserted_count == 1
        assert prediction.decision_at is None
        assert prediction.captured_at == original_capture
        assert prediction.planned_entry_session == original_entry
        decision = db.scalar(
            select(WinnerTemporalValidityDecision).where(
                WinnerTemporalValidityDecision.prediction_id == prediction.id
            )
        )
        assert decision.status == "VALID"
        assert decision.evidence_eligible is True
        assert decision.metadata_json["request_key"] == "positive-certification-1"
        assert decision.metadata_json["manifest_hash"] == first.manifest_hash
    engine.dispose()


def test_positive_certification_rejects_unresolved_semantic_lineage(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        prediction = _prediction(db, ticker="UNCERTIFIED")
        service = TemporalValidationService()
        item = TemporalCertificationItem(
            prediction_id=prediction.id,
            decision_at=prediction.captured_at,
            entry_session=prediction.planned_entry_session,
            semantic_input_time_valid=None,
            certification_reason="LINEAGE_UNRESOLVED",
        )

        with pytest.raises(ValueError, match="not positively valid"):
            service.plan_certification(db, items=(item,))

        assert db.scalar(select(func.count()).select_from(WinnerTemporalValidityDecision)) == 0
    engine.dispose()


def test_market_data_obligation_persists_identity_and_gates_maturation(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        prediction = _prediction(db, ticker="OBLIG")
        TemporalValidationService().record(
            db,
            prediction=prediction,
            decision_at=datetime(2026, 8, 20, 12, 0, tzinfo=UTC),
            entry_session=date(2026, 8, 20),
            semantic_input_time_valid=True,
            evaluated_by="TEST_CAPTURE",
        )
        contract = IBContract(
            ticker="OBLIG",
            ib_conid=81234,
            symbol="OBLIG",
            local_symbol="OBLIG",
            exchange="SMART",
            primary_exchange="NASDAQ",
            currency="USD",
            sec_type="STK",
            trading_class="NMS",
            resolution_status="RESOLVED",
        )
        outcome = WinnerForwardOutcome(
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
        db.add_all([contract, outcome])
        db.flush()

        created = MarketDataObligationService().ensure_for_outcomes(db, [outcome])
        assert created.created == 2
        assert created.fetch_required == 2
        obligations = list(db.scalars(select(WinnerMarketDataObligation)))
        assert {row.what_to_show for row in obligations} == {"ADJUSTED_LAST", "TRADES"}
        assert {row.ib_conid_snapshot for row in obligations} == {81234}
        assert (
            WinnerOutcomeRepository().get_due_pending_forward_outcomes(
                db, completed_on=date(2026, 8, 27), limit=10
            )
            == []
        )

        sessions = required_outcome_sessions(date(2026, 8, 20), 5)
        for session in sessions:
            db.add(
                PriceBar(
                    ticker="OBLIG",
                    bar_date=session,
                    timeframe="1 day",
                    open=Decimal("100"),
                    high=Decimal("102"),
                    low=Decimal("99"),
                    close=Decimal("101"),
                    volume=Decimal("1000"),
                    source="TEST",
                    what_to_show="ADJUSTED_LAST",
                )
            )
        db.flush()
        refreshed = MarketDataObligationService().evaluate(db, obligations=obligations)
        assert refreshed.satisfied == 1
        unchanged_watermark = obligations[0].price_series_watermark
        repeated = MarketDataObligationService().evaluate(db, obligations=obligations)
        assert repeated.satisfied == 1
        assert obligations[0].price_series_watermark == unchanged_watermark
        selected = WinnerOutcomeRepository().get_due_pending_forward_outcomes(
            db, completed_on=date(2026, 8, 27), limit=10
        )
        assert [row.id for row in selected] == [outcome.id]

        contract.ib_conid = 99999
        db.flush()
        blocked = MarketDataObligationService().evaluate(db, obligations=obligations)
        assert blocked.identity_blocked == 2
        assert (
            WinnerOutcomeRepository().get_due_pending_forward_outcomes(
                db, completed_on=date(2026, 8, 27), limit=10
            )
            == []
        )
    engine.dispose()


def test_uncertified_and_quarantined_pending_outcomes_create_no_obligation(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        uncertified = _prediction(db, ticker="UNCERTOBL")
        quarantined = _prediction(db, ticker="QUAROBL")
        valid = _prediction(db, ticker="PERSISTOBL")
        validator = TemporalValidationService()
        validator.record(
            db,
            prediction=quarantined,
            decision_at=datetime(2026, 8, 20, 15, 0, tzinfo=UTC),
            entry_session=date(2026, 8, 20),
            semantic_input_time_valid=None,
            evaluated_by="TEST",
        )
        validator.record(
            db,
            prediction=valid,
            decision_at=datetime(2026, 8, 20, 12, 0, tzinfo=UTC),
            entry_session=date(2026, 8, 20),
            semantic_input_time_valid=True,
            evaluated_by="TEST",
        )
        outcomes = []
        for prediction in (uncertified, quarantined, valid):
            outcome = WinnerForwardOutcome(
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
            db.add(outcome)
            outcomes.append(outcome)
        db.flush()

        first = MarketDataObligationService().ensure_for_outcomes(db, outcomes)
        assert first.excluded == 2
        assert first.created == 2
        assert set(
            db.scalars(select(WinnerMarketDataObligation.prediction_id))
        ) == {valid.id}

        # A later run/universe change does not remove the durable dependency.
        second = MarketDataObligationService().ensure_for_outcomes(db, [outcomes[-1]])
        assert second.created == 0
        assert db.scalar(select(func.count()).select_from(WinnerMarketDataObligation)) == 2
    engine.dispose()


def test_global_daily_bar_lag_is_explicit(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        db.add(
            PriceBar(
                ticker="SPY",
                bar_date=date(2026, 9, 3),
                timeframe="1 day",
                open=Decimal("100"),
                high=Decimal("102"),
                low=Decimal("99"),
                close=Decimal("101"),
                volume=Decimal("1000"),
                source="TEST",
                what_to_show="TRADES",
            )
        )
        db.flush()

        lag = global_daily_bar_lag(db, latest_completed_session=date(2026, 9, 4))

        assert lag.degraded is True
        assert lag.lag_sessions == 1
        assert lag.latest_local_session == date(2026, 9, 3)
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
