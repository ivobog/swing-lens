"""Native capture-attempt/creator regression, integrity and cancellation coverage."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session
from test_run40_lifecycle_remediation_postgresql import _capture, _counts, _migrate, _seed_universe

from app.models.tables import (
    CoreCalculationEvidence,
    SetupLifecycleEvaluationRun,
    SetupSignalSnapshot,
)
from app.services.background_job_service import classify_job_failure
from app.services.pipeline_executor import _invoke_setup_evaluation
from app.services.setup_lifecycle.capture_provenance import CAPTURE_MEMBERSHIP_KEY
from app.services.setup_lifecycle.errors import SetupCaptureHandoffError
from app.services.setup_lifecycle.evaluation_service import (
    SetupLifecycleEvaluationCancelled,
    SetupLifecycleEvaluationService,
)
from app.services.setup_lifecycle.snapshot_builder import SetupLifecycleSnapshotCaptureService


@pytest.fixture
def run41_engine(disposable_postgres_database):
    _migrate(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    try:
        yield engine
    finally:
        engine.dispose()


def _seed(db, tickers=("R041",)):
    return _seed_universe(
        db,
        run_id=702,
        tickers=tickers,
        cutoff_at=datetime(2026, 9, 18, 21, tzinfo=UTC),
    )


def _artifact_pins(db):
    return [
        tuple(row)
        for row in db.execute(
            select(
                SetupSignalSnapshot.id,
                SetupSignalSnapshot.evaluation_run_id,
                SetupSignalSnapshot.evidence_id,
                CoreCalculationEvidence.payload_fingerprint,
                CoreCalculationEvidence.payload_json,
            )
            .join(
                CoreCalculationEvidence,
                CoreCalculationEvidence.id == SetupSignalSnapshot.evidence_id,
            )
            .where(SetupSignalSnapshot.run_id == 702)
            .order_by(SetupSignalSnapshot.id)
        )
    ]


@pytest.mark.parametrize("handoff_enabled", [False, True])
def test_native_recapture_and_original_handoff_preserve_creators_and_replay(
    run41_engine,
    handoff_enabled,
    caplog,
):
    with Session(run41_engine) as db:
        cutoff, pipeline_id = _seed(db)
        original = _capture(db, 702, cutoff)
        # Retain Run 41's old capture format as well as the new membership
        # format: no persisted membership existed on production capture 58.
        db.execute(
            text("update setup_lifecycle_evaluation_runs set audit_json='{}' where id=:id"),
            {"id": original.evaluation_run_id},
        )
        db.commit()
        before = _artifact_pins(db)
        progress = []
        with caplog.at_level("INFO"):
            result = _invoke_setup_evaluation(
                SetupLifecycleEvaluationService().evaluate_run,
                db,
                702,
                capture_result=original if handoff_enabled else None,
                capture_handoff_enabled=handoff_enabled,
                market_cutoff=cutoff,
                pipeline_run_id=pipeline_id,
                progress_callback=lambda **row: progress.append(row),
            )
        db.commit()
        assert result.status == "COMPLETED" and result.canonical_snapshots == 1
        assert _artifact_pins(db) == before
        published = _counts(db)
        events = [
            record
            for record in caplog.records
            if record.getMessage() == "setup.lifecycle.evaluation_inputs"
        ]
        assert events[-1].capture_handoff_enabled is handoff_enabled
        assert events[-1].capture_handoff_supplied is handoff_enabled
        if not handoff_enabled:
            assert any(row["phase"].startswith("RECAPTURE_") for row in progress)
            attempts = list(
                db.scalars(
                    select(SetupLifecycleEvaluationRun)
                    .where(
                        SetupLifecycleEvaluationRun.source_run_id == 702,
                        SetupLifecycleEvaluationRun.current_phase == "snapshot_capture",
                    )
                    .order_by(SetupLifecycleEvaluationRun.id)
                )
            )
            assert len(attempts) == 2
            member = attempts[-1].audit_json[CAPTURE_MEMBERSHIP_KEY]["snapshots"]
            assert (
                member[str(original.snapshot_ids[0])]["creator_evaluation_run_id"]
                == original.evaluation_run_id
            )
        replay = _invoke_setup_evaluation(
            SetupLifecycleEvaluationService().evaluate_run,
            db,
            702,
            capture_result=original if handoff_enabled else None,
            market_cutoff=cutoff,
            pipeline_run_id=pipeline_id,
        )
        db.commit()
        assert replay.evaluation_run_id == result.evaluation_run_id
        assert _counts(db) == published and _artifact_pins(db) == before


def test_native_mixed_creator_capture_membership_and_tamper_rejection(run41_engine):
    with Session(run41_engine) as db:
        cutoff, pipeline_id = _seed(db, ("FIRST", "SECOND"))
        original = SetupLifecycleSnapshotCaptureService().capture_snapshots_for_run(
            db,
            702,
            market_cutoff=cutoff,
            frozen_tickers=("FIRST",),
        )
        db.commit()
        mixed = _capture(db, 702, cutoff)
        before = _artifact_pins(db)
        owners = {row[1] for row in before}
        assert owners == {original.evaluation_run_id, mixed.evaluation_run_id}
        service = SetupLifecycleEvaluationService()

        def validate(capture=mixed):
            service._validate_capture_handoff(
                db,
                run_id=702,
                capture_result=capture,
                snapshot_ids=mixed.snapshot_ids,
                market_cutoff=cutoff,
                pipeline_run_id=pipeline_id,
            )

        validate()
        db.rollback()
        for mutation in ("owner", "extra_member", "missing_membership", "evidence", "context"):
            if mutation == "owner":
                db.execute(
                    text("update setup_signal_snapshots set evaluation_run_id=:owner where id=:id"),
                    {"owner": mixed.evaluation_run_id, "id": original.snapshot_ids[0]},
                )
            elif mutation == "extra_member":
                attempt = db.get(SetupLifecycleEvaluationRun, mixed.evaluation_run_id)
                audit = json.loads(json.dumps(attempt.audit_json))
                audit[CAPTURE_MEMBERSHIP_KEY]["snapshots"]["999999"] = {}
                db.execute(
                    text(
                        "update setup_lifecycle_evaluation_runs "
                        "set audit_json=cast(:audit as jsonb) where id=:id"
                    ),
                    {"audit": json.dumps(audit), "id": mixed.evaluation_run_id},
                )
            elif mutation == "missing_membership":
                db.execute(
                    text("update setup_lifecycle_evaluation_runs set audit_json='{}' where id=:id"),
                    {"id": mixed.evaluation_run_id},
                )
            elif mutation == "evidence":
                db.execute(
                    text("update setup_signal_snapshots set evidence_id=null where id=:id"),
                    {"id": original.snapshot_ids[0]},
                )
            else:
                db.execute(
                    text(
                        "update setup_signal_snapshots set calculation_cutoff_at="
                        "calculation_cutoff_at+interval '1 second' where id=:id"
                    ),
                    {"id": original.snapshot_ids[0]},
                )
            db.expire_all()
            with pytest.raises(SetupCaptureHandoffError) as rejected:
                validate()
            classification = classify_job_failure(rejected.value)
            assert classification["retryable"] is False
            assert classification["code"] == "SETUP_CAPTURE_HANDOFF_INVALID"
            assert classification["diagnostics"]["reason"]
            db.rollback()
        with pytest.raises(SetupCaptureHandoffError, match="membership"):
            validate(replace(mixed, evaluation_run_id=original.evaluation_run_id))
        db.rollback()
        result = service.evaluate_run(
            db,
            702,
            capture_result=mixed,
            market_cutoff=cutoff,
            pipeline_run_id=pipeline_id,
        )
        db.commit()
        assert result.status == "COMPLETED" and result.canonical_snapshots == 2
        assert _artifact_pins(db) == before
        # Membership does not substitute for the independent financial proof.
        db.execute(
            text(
                "update setup_signal_snapshots "
                "set final_score=coalesce(final_score,0)+1 where id=:id"
            ),
            {"id": original.snapshot_ids[0]},
        )
        db.expire_all()
        with pytest.raises(ValueError, match="MUTATION_"):
            service.evaluate_run(
                db,
                702,
                capture_result=mixed,
                market_cutoff=cutoff,
                pipeline_run_id=pipeline_id,
            )
        db.rollback()
        assert _artifact_pins(db) == before


@pytest.mark.parametrize("interruption", ["failure", "cancel"])
def test_native_recapture_interruption_rolls_back_and_records_only_audit(
    run41_engine, interruption
):
    with Session(run41_engine) as db:
        cutoff, pipeline_id = _seed(db)
        _capture(db, 702, cutoff)
        before = _artifact_pins(db)
        counts = _counts(db)
        db.commit()
        cancelled = False

        def checkpoint(**progress):
            nonlocal cancelled
            if progress["phase"] == "RECAPTURE_EVIDENCE":
                if interruption == "cancel":
                    cancelled = True
                else:
                    raise RuntimeError("run41 recapture interrupted")

        expected = SetupLifecycleEvaluationCancelled if interruption == "cancel" else RuntimeError
        with pytest.raises(expected):
            SetupLifecycleEvaluationService().evaluate_run(
                db,
                702,
                market_cutoff=cutoff,
                pipeline_run_id=pipeline_id,
                progress_callback=checkpoint,
                should_cancel=lambda: cancelled,
            )
        assert _artifact_pins(db) == before and _counts(db) == counts
        attempts = list(
            db.scalars(
                select(SetupLifecycleEvaluationRun)
                .where(
                    SetupLifecycleEvaluationRun.source_run_id == 702,
                )
                .order_by(SetupLifecycleEvaluationRun.id)
            )
        )
        assert len(attempts) == 2
        assert attempts[-1].status == ("CANCELLED" if interruption == "cancel" else "FAILED")
        assert db.scalar(select(func.count()).select_from(SetupSignalSnapshot)) == 1
