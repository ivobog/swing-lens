from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.tables import (
    IBContract,
    PriceBar,
    PriceSeriesVersion,
    UploadRun,
    WinnerEstimateEvidenceMember,
    WinnerEvidenceManifestMember,
    WinnerForwardOutcome,
    WinnerMarketDataObligation,
    WinnerOutcomeDefinition,
    WinnerPredictionSnapshot,
    WinnerTargetStopOutcome,
    WinnerTemporalValidityDecision,
)
from app.services.us_market_calendar import us_market_session
from app.services.winner_probability.maturation_canary_service import (
    CanaryApprovalError,
    build_maturation_canary_manifest,
    canonical_canary_hash,
    execute_reviewed_maturation_canary,
    verify_maturation_canary_results,
)
from app.services.winner_probability.outcome_service import (
    OutcomeMaturationService,
    WinnerOutcomeRepository,
)
from app.services.winner_probability.target_stop_scope_repair_service import (
    apply_target_stop_scope_repair,
    build_target_stop_scope_repair_manifest,
    target_stop_scope_repair_hash,
)


def test_explicit_hash_gated_canary_matures_only_reviewed_id(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        reviewed_id = _seed_ready_outcome(db, ticker="CANARY", run_suffix="one")
        unrelated_id = _seed_ready_outcome(db, ticker="CONTROL", run_suffix="two")
        db.commit()
    with factory() as db:
        manifest = build_maturation_canary_manifest(db, [reviewed_id])
        reviewed_hash = canonical_canary_hash(manifest)

    result = execute_reviewed_maturation_canary(
        factory,
        manifest,
        reviewed_manifest_hash=reviewed_hash,
        approve_write=True,
        actor="pytest",
        request_key="pytest-explicit-canary",
        now=datetime(2026, 8, 27, 22, 0, tzinfo=UTC),
    )

    assert result.outcome_ids == (reviewed_id,)
    assert result.processed == result.matured == 1
    assert result.failed == 0
    with factory() as db:
        reviewed = db.get(WinnerForwardOutcome, reviewed_id)
        unrelated = db.get(WinnerForwardOutcome, unrelated_id)
        assert reviewed.status == "MATURED"
        assert reviewed.revision == 1
        assert reviewed.is_current_revision is True
        assert reviewed.entry_price == Decimal("100.000000")
        assert reviewed.exit_price == Decimal("105.000000")
        assert reviewed.close_return_pct == Decimal("5.000000")
        assert unrelated.status == "PENDING"
        assert unrelated.matured_at is None
        diagnostic = db.scalar(
            select(WinnerTargetStopOutcome).where(
                WinnerTargetStopOutcome.prediction_id == reviewed.prediction_id,
                WinnerTargetStopOutcome.entry_model == "SIGNAL_CLOSE_DIAGNOSTIC",
                WinnerTargetStopOutcome.is_current_revision.is_(True),
            )
        )
        assert diagnostic.status == "PENDING"
        assert diagnostic.evaluated_at is None
        assert db.scalar(select(func.count(WinnerEstimateEvidenceMember.id))) == 0
        assert db.scalar(select(func.count(WinnerEvidenceManifestMember.id))) == 0
        verify_maturation_canary_results(
            db,
            manifest,
            executed_at=datetime(2026, 8, 27, 22, 0, tzinfo=UTC),
        )
    engine.dispose()


def test_canary_preflight_hash_and_state_drift_roll_back_without_maturation(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        outcome_id = _seed_ready_outcome(db, ticker="DRIFT", run_suffix="drift")
        db.commit()
    with factory() as db:
        manifest = build_maturation_canary_manifest(db, [outcome_id])
        reviewed_hash = canonical_canary_hash(manifest)

    with pytest.raises(CanaryApprovalError, match="hash"):
        execute_reviewed_maturation_canary(
            factory,
            manifest,
            reviewed_manifest_hash="0" * 64,
            approve_write=True,
            actor="pytest",
            request_key="pytest-bad-hash",
        )
    with factory() as db:
        bar = db.scalar(
            select(PriceBar).where(
                PriceBar.ticker == "DRIFT",
                PriceBar.what_to_show == "ADJUSTED_LAST",
                PriceBar.bar_date == date(2026, 8, 26),
            )
        )
        bar.close = Decimal("104")
        bar.data_hash = "drifted"
        db.commit()

    with pytest.raises(CanaryApprovalError, match="preflight"):
        execute_reviewed_maturation_canary(
            factory,
            manifest,
            reviewed_manifest_hash=reviewed_hash,
            approve_write=True,
            actor="pytest",
            request_key="pytest-drift",
        )
    with factory() as db:
        assert db.get(WinnerForwardOutcome, outcome_id).status == "PENDING"
    engine.dispose()


def test_canary_rejects_temporal_quarantine_and_clbk(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        invalid_id = _seed_ready_outcome(
            db, ticker="INVALID", run_suffix="invalid", temporal_status="EXECUTION_INVALID"
        )
        clbk_id = _seed_ready_outcome(db, ticker="CLBK", run_suffix="clbk")
        db.commit()
    with Session(engine) as db:
        with pytest.raises(CanaryApprovalError, match="VALID temporal"):
            build_maturation_canary_manifest(db, [invalid_id])
        with pytest.raises(CanaryApprovalError, match="CLBK"):
            build_maturation_canary_manifest(db, [clbk_id])
    engine.dispose()


def test_batch_and_non_batch_target_selection_match_and_bad_link_fails_closed(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    repository = WinnerOutcomeRepository()
    with Session(engine) as db:
        outcome_id = _seed_ready_outcome(db, ticker="PARITY", run_suffix="parity")
        db.commit()
    with Session(engine) as db:
        outcome = db.get(WinnerForwardOutcome, outcome_id)
        context_ids = [
            int(row.id)
            for row in repository.load_batch_context(db, [outcome]).target_stops_for(outcome)
        ]
        direct_ids = [
            int(row.id)
            for row in repository.get_target_stop_outcomes_for_forward(
                db,
                prediction_id=outcome.prediction_id,
                entry_model=outcome.entry_model,
                horizon_sessions=outcome.horizon_sessions,
            )
        ]
        assert context_ids == direct_ids
        target = db.get(WinnerTargetStopOutcome, context_ids[0])
        diagnostic_forward = db.scalar(
            select(WinnerForwardOutcome).where(
                WinnerForwardOutcome.prediction_id == outcome.prediction_id,
                WinnerForwardOutcome.entry_model == "SIGNAL_CLOSE_DIAGNOSTIC",
            )
        )
        target.forward_outcome_id = diagnostic_forward.id
        db.commit()
    with Session(engine) as db:
        with pytest.raises(CanaryApprovalError, match="TARGET_STOP_FORWARD_MISMATCH"):
            build_maturation_canary_manifest(db, [outcome_id])
    engine.dispose()


def test_postgresql_batched_diagnostic_maturation_leaves_next_open_sibling_unchanged(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    repository = WinnerOutcomeRepository()
    service = OutcomeMaturationService(repository=repository)
    with Session(engine) as db:
        next_open_id = _seed_ready_outcome(db, ticker="REVERSE", run_suffix="reverse")
        next_open = db.get(WinnerForwardOutcome, next_open_id)
        diagnostic = db.scalar(
            select(WinnerForwardOutcome).where(
                WinnerForwardOutcome.prediction_id == next_open.prediction_id,
                WinnerForwardOutcome.entry_model == "SIGNAL_CLOSE_DIAGNOSTIC",
                WinnerForwardOutcome.is_current_revision.is_(True),
            )
        )
        targets = list(
            db.scalars(
                select(WinnerTargetStopOutcome).where(
                    WinnerTargetStopOutcome.prediction_id == next_open.prediction_id,
                    WinnerTargetStopOutcome.is_current_revision.is_(True),
                )
            )
        )
        next_open_target = next(
            row for row in targets if row.entry_model == "NEXT_OPEN"
        )
        diagnostic_target = next(
            row for row in targets if row.entry_model == "SIGNAL_CLOSE_DIAGNOSTIC"
        )
        next_open_before = (
            next_open_target.status,
            next_open_target.revision,
            next_open_target.forward_outcome_id,
            next_open_target.evaluated_at,
            next_open_target.source_bar_lineage_hash,
            next_open_target.metadata_json,
        )

        context = service.build_batch_context(db, [diagnostic])
        result = service.process_forward_outcome(
            db,
            diagnostic,
            now=datetime(2026, 8, 27, 22, 0, tzinfo=UTC),
            context=context,
        )
        db.commit()

        assert result.processed == result.matured == 1
        assert result.target_stop_matured == 1
        assert diagnostic.status == "MATURED"
        assert diagnostic.entry_price == Decimal("101.000000")
        assert diagnostic_target.status == "MATURED"
        assert (
            next_open_target.status,
            next_open_target.revision,
            next_open_target.forward_outcome_id,
            next_open_target.evaluated_at,
            next_open_target.source_bar_lineage_hash,
            next_open_target.metadata_json,
        ) == next_open_before
    engine.dispose()


def test_postgresql_same_bar_conflict_fixture_is_conservative(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        outcome_id = _seed_ready_outcome(
            db,
            ticker="CONFLICT",
            run_suffix="conflict",
            first_high=Decimal("106"),
            first_low=Decimal("94"),
        )
        db.commit()
    with factory() as db:
        manifest = build_maturation_canary_manifest(db, [outcome_id])
        expected = manifest["outcomes"][0]["target_stops"][0]["expected"]
        assert expected["first_event"] == "SAME_BAR_CONFLICT"
        assert expected["primary_winner"] is False
        reviewed_hash = canonical_canary_hash(manifest)
    execute_reviewed_maturation_canary(
        factory,
        manifest,
        reviewed_manifest_hash=reviewed_hash,
        approve_write=True,
        actor="pytest",
        request_key="pytest-conflict",
        now=datetime(2026, 8, 27, 22, 0, tzinfo=UTC),
    )
    with factory() as db:
        target = db.scalar(
            select(WinnerTargetStopOutcome).where(
                WinnerTargetStopOutcome.prediction_id
                == db.get(WinnerForwardOutcome, outcome_id).prediction_id
            )
        )
        assert target.first_event == "SAME_BAR_CONFLICT"
        assert target.same_bar_conflict is True
        assert target.primary_winner is False
        assert target.optimistic_winner is True
        assert target.conservative_winner is False
    engine.dispose()


def test_scope_leak_repair_creates_append_only_pending_revision(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        next_open_id = _seed_ready_outcome(db, ticker="REPAIR", run_suffix="repair")
        prediction_id = db.get(WinnerForwardOutcome, next_open_id).prediction_id
        diagnostic = db.scalar(
            select(WinnerTargetStopOutcome).where(
                WinnerTargetStopOutcome.prediction_id == prediction_id,
                WinnerTargetStopOutcome.entry_model == "SIGNAL_CLOSE_DIAGNOSTIC",
            )
        )
        diagnostic.status = "MATURED"
        diagnostic.forward_outcome_id = next_open_id
        diagnostic.target_hit = True
        diagnostic.stop_hit = False
        diagnostic.first_event = "TARGET_FIRST"
        diagnostic.event_session = date(2026, 8, 21)
        diagnostic.primary_winner = True
        diagnostic.optimistic_winner = True
        diagnostic.conservative_winner = True
        diagnostic.source_bar_lineage_hash = "wrong-next-open-lineage"
        diagnostic.evaluated_at = datetime(2026, 9, 6, 10, 14, 28, tzinfo=UTC)
        diagnostic.metadata_json = {
            "materialized_at_capture": True,
            "calculation_phase": "phase_5",
        }
        db.commit()
        bad_id = int(diagnostic.id)

    with Session(engine) as db:
        manifest = build_target_stop_scope_repair_manifest(
            db,
            target_stop_ids=[bad_id],
            incident="pytest-scope-leak",
        )
        reviewed_hash = target_stop_scope_repair_hash(manifest)
        result = apply_target_stop_scope_repair(
            db,
            manifest,
            reviewed_manifest_hash=reviewed_hash,
            approve_write=True,
            actor="pytest",
            request_key="pytest-repair",
            now=datetime(2026, 9, 7, tzinfo=UTC),
        )
        db.commit()
        assert result.created_revision_ids

    with Session(engine) as db:
        rows = list(
            db.scalars(
                select(WinnerTargetStopOutcome)
                .where(WinnerTargetStopOutcome.prediction_id == prediction_id)
                .where(WinnerTargetStopOutcome.entry_model == "SIGNAL_CLOSE_DIAGNOSTIC")
                .order_by(WinnerTargetStopOutcome.revision)
            )
        )
        assert len(rows) == 2
        old, current = rows
        assert old.status == "MATURED"
        assert old.source_bar_lineage_hash == "wrong-next-open-lineage"
        assert old.is_current_revision is False
        assert current.revision == 2
        assert current.is_current_revision is True
        assert current.status == "PENDING"
        assert current.forward_outcome_id != next_open_id
        assert current.source_bar_lineage_hash is None
        assert current.evaluated_at is None
        assert current.metadata_json["repair_type"] == "MATURATION_SCOPE_LEAK_CORRECTION"
    engine.dispose()


def test_scope_leak_repair_hash_gate_and_drift_are_atomic(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        next_open_id = _seed_ready_outcome(db, ticker="ATOMIC", run_suffix="atomic")
        prediction_id = db.get(WinnerForwardOutcome, next_open_id).prediction_id
        diagnostic = db.scalar(
            select(WinnerTargetStopOutcome).where(
                WinnerTargetStopOutcome.prediction_id == prediction_id,
                WinnerTargetStopOutcome.entry_model == "SIGNAL_CLOSE_DIAGNOSTIC",
            )
        )
        diagnostic.status = "MATURED"
        diagnostic.forward_outcome_id = next_open_id
        diagnostic.target_hit = True
        diagnostic.source_bar_lineage_hash = "bad"
        diagnostic.evaluated_at = datetime(2026, 9, 6, tzinfo=UTC)
        diagnostic.metadata_json = {"calculation_phase": "phase_5"}
        db.commit()
        bad_id = int(diagnostic.id)
    with Session(engine) as db:
        manifest = build_target_stop_scope_repair_manifest(
            db, target_stop_ids=[bad_id], incident="pytest-atomic"
        )
    with Session(engine) as db:
        with pytest.raises(RuntimeError, match="hash"):
            apply_target_stop_scope_repair(
                db,
                manifest,
                reviewed_manifest_hash="0" * 64,
                approve_write=True,
                actor="pytest",
                request_key="bad-hash",
            )
        db.rollback()
    with Session(engine) as db:
        db.get(WinnerTargetStopOutcome, bad_id).metadata_json = {
            "calculation_phase": "phase_5",
            "drift": True,
        }
        db.commit()
    with Session(engine) as db:
        with pytest.raises(RuntimeError, match="changed after review"):
            apply_target_stop_scope_repair(
                db,
                manifest,
                reviewed_manifest_hash=target_stop_scope_repair_hash(manifest),
                approve_write=True,
                actor="pytest",
                request_key="drift",
            )
        db.rollback()
    with Session(engine) as db:
        rows = list(
            db.scalars(
                select(WinnerTargetStopOutcome).where(
                    WinnerTargetStopOutcome.prediction_id == prediction_id,
                    WinnerTargetStopOutcome.entry_model == "SIGNAL_CLOSE_DIAGNOSTIC",
                )
            )
        )
        assert len(rows) == 1
        assert rows[0].is_current_revision is True
        assert rows[0].revision == 1
    engine.dispose()


def _seed_ready_outcome(
    db: Session,
    *,
    ticker: str,
    run_suffix: str,
    temporal_status: str = "VALID",
    first_high: Decimal = Decimal("103"),
    first_low: Decimal = Decimal("99"),
) -> int:
    uploaded_at = datetime(2026, 8, 19, 21, 0, tzinfo=UTC)
    run = UploadRun(
        filename=f"{ticker}-{run_suffix}.csv",
        uploaded_at=uploaded_at,
        status="COMPLETED",
    )
    db.add(run)
    db.flush()
    prediction = WinnerPredictionSnapshot(
        run_id=run.id,
        ticker=ticker,
        prediction_as_of_date=date(2026, 8, 19),
        source_data_cutoff_at=uploaded_at,
        decision_at=datetime(2026, 8, 20, 12, 0, tzinfo=UTC),
        captured_at=datetime(2026, 8, 20, 12, 1, tzinfo=UTC),
        planned_entry_session=date(2026, 8, 20),
        entry_schedule_status="RESOLVED",
        entry_data_status="NOT_DUE",
        eligibility_status="ELIGIBLE",
        setup_family="breakout",
        setup_classification="base",
        ranking_profile="default",
        feature_schema_version="test",
        feature_vector_hash=f"hash-{ticker}",
        config_hash="config-test",
        calculation_version="calculation-1.1",
        feature_json={"setup_family": "breakout"},
        source_ids_json={},
        warning_flags_json=[],
        lineage_json={"point_in_time_validated": True},
    )
    db.add(prediction)
    db.flush()
    valid = temporal_status == "VALID"
    session = us_market_session(date(2026, 8, 20))
    db.add(
        WinnerTemporalValidityDecision(
            prediction_id=prediction.id,
            validation_sequence=1,
            status=temporal_status,
            entry_timing_valid=valid,
            source_cutoff_valid=valid,
            semantic_input_time_valid=True if valid else None,
            evidence_eligible=valid,
            reason_codes_json=[] if valid else ["EXECUTION_INVALID"],
            validation_version="test",
            decision_at=(
                datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
                if valid
                else datetime(2026, 8, 20, 15, 0, tzinfo=UTC)
            ),
            entry_session=date(2026, 8, 20),
            entry_open_at=session.open_at,
            evaluated_at=datetime(2026, 8, 27, tzinfo=UTC),
            evaluated_by="pytest",
            metadata_json={},
        )
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
        pending_reason_code="missing_horizon_bar",
        metadata_json={"pending_reason": "missing_horizon_bar"},
    )
    db.add(outcome)
    db.flush()
    contract = IBContract(
        ticker=ticker,
        ib_conid=100_000 + int(outcome.id),
        symbol=ticker,
        local_symbol=ticker,
        exchange="SMART",
        primary_exchange="NYSE",
        currency="USD",
        sec_type="STK",
        trading_class=ticker,
        resolution_status="RESOLVED",
    )
    db.add(contract)
    db.flush()
    sessions = [
        date(2026, 8, 20),
        date(2026, 8, 21),
        date(2026, 8, 24),
        date(2026, 8, 25),
        date(2026, 8, 26),
    ]
    for basis in ("ADJUSTED_LAST", "TRADES"):
        for index, session_date in enumerate(sessions):
            high = first_high if index == 0 else Decimal(103 + index)
            low = first_low if index == 0 else Decimal(99 - index)
            close = Decimal(101 + index)
            db.add(
                PriceBar(
                    ticker=ticker,
                    bar_date=session_date,
                    timeframe="1 day",
                    open=Decimal("100"),
                    high=high,
                    low=low,
                    close=close,
                    volume=Decimal("1000"),
                    source="TEST",
                    what_to_show=basis,
                    adjustment_type="SPLIT_DIVIDEND" if basis == "ADJUSTED_LAST" else None,
                    created_at=datetime(2026, 8, 27, 12, 0, tzinfo=UTC),
                    first_seen_at=datetime(2026, 8, 27, 12, 0, tzinfo=UTC),
                    last_seen_at=datetime(2026, 8, 27, 12, 0, tzinfo=UTC),
                    revision_count=0,
                    data_hash=f"{ticker}-{basis}-{session_date.isoformat()}",
                )
            )
        db.add(
            PriceSeriesVersion(
                ticker=ticker,
                timeframe="1 day",
                what_to_show=basis,
                series_version=1,
                bar_count=5,
                first_bar_date=sessions[0],
                latest_bar_date=sessions[-1],
                last_changed_at=datetime(2026, 8, 27, 12, 0, tzinfo=UTC),
            )
        )
        db.add(
            WinnerMarketDataObligation(
                prediction_id=prediction.id,
                forward_outcome_id=outcome.id,
                ib_contract_id=contract.id,
                ticker_snapshot=ticker,
                ib_conid_snapshot=contract.ib_conid,
                symbol_snapshot=contract.symbol,
                local_symbol_snapshot=contract.local_symbol,
                exchange_snapshot=contract.exchange,
                primary_exchange_snapshot=contract.primary_exchange,
                currency_snapshot=contract.currency,
                sec_type_snapshot=contract.sec_type,
                trading_class_snapshot=contract.trading_class,
                entry_session=sessions[0],
                required_through_session=sessions[-1],
                required_sessions_json=[value.isoformat() for value in sessions],
                timeframe="1 day",
                what_to_show=basis,
                status="SATISFIED",
                price_series_watermark=f"watermark-{basis}",
                metadata_json={},
            )
        )
    definition = WinnerOutcomeDefinition(
        definition_id=f"primary-{ticker}",
        label="Primary",
        entry_model="NEXT_OPEN",
        horizon_sessions=5,
        target_pct=Decimal("5"),
        stop_pct=Decimal("5"),
        same_bar_conflict_policy="CONSERVATIVE_STOP_FIRST",
        calculation_version="calculation-1.1",
        config_hash="config-test",
        is_primary=True,
        is_active=True,
        metadata_json={},
    )
    db.add(definition)
    db.flush()
    db.add(
        WinnerTargetStopOutcome(
            prediction_id=prediction.id,
            outcome_definition_id=definition.id,
            forward_outcome_id=outcome.id,
            entry_model="NEXT_OPEN",
            horizon_sessions=5,
            status="PENDING",
            revision=1,
            is_current_revision=True,
            target_pct=Decimal("5"),
            stop_pct=Decimal("5"),
            same_bar_conflict=False,
            metadata_json={},
        )
    )
    diagnostic_forward = WinnerForwardOutcome(
        prediction_id=prediction.id,
        entry_model="SIGNAL_CLOSE_DIAGNOSTIC",
        horizon_sessions=5,
        entry_session=date(2026, 8, 20),
        due_session=date(2026, 8, 26),
        status="PENDING",
        revision=1,
        is_current_revision=True,
        metadata_json={},
    )
    db.add(diagnostic_forward)
    db.flush()
    diagnostic_definition = WinnerOutcomeDefinition(
        definition_id=f"diagnostic-{ticker}",
        label="Diagnostic",
        entry_model="SIGNAL_CLOSE_DIAGNOSTIC",
        horizon_sessions=5,
        target_pct=Decimal("5"),
        stop_pct=Decimal("5"),
        same_bar_conflict_policy="CONSERVATIVE_STOP_FIRST",
        calculation_version="calculation-1.1",
        config_hash="config-test",
        is_primary=False,
        is_active=True,
        metadata_json={},
    )
    db.add(diagnostic_definition)
    db.flush()
    db.add(
        WinnerTargetStopOutcome(
            prediction_id=prediction.id,
            outcome_definition_id=diagnostic_definition.id,
            forward_outcome_id=diagnostic_forward.id,
            entry_model="SIGNAL_CLOSE_DIAGNOSTIC",
            horizon_sessions=5,
            status="PENDING",
            revision=1,
            is_current_revision=True,
            target_pct=Decimal("5"),
            stop_pct=Decimal("5"),
            same_bar_conflict=False,
            metadata_json={"fixture": "diagnostic-sibling"},
        )
    )
    db.flush()
    return int(outcome.id)


def _upgrade(database_url: str) -> None:
    env = {**os.environ, "DATABASE_URL": database_url}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
