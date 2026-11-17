"""Native fresh-plan handoff, financial tail, and useful-progress qualification.

Every financial source is produced by the native calculators in an isolated
PostgreSQL database. Spies count real validation calls; none replaces a result.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from time import perf_counter

import pytest
import test_run40_lifecycle_remediation_postgresql as native_fixture
from evidence_output import output_directory
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session
from test_run40_lifecycle_remediation_postgresql import _migrate, _seed_universe

import app.services.transition_preflight_plan_service as handoff_service
from app.models.tables import (
    BackgroundJob,
    CoreCalculationEvidence,
    RawCompanyRow,
    SectorRotationSnapshot,
    SetupSignalSnapshot,
    TransitionDecisionHandoffManifest,
    TransitionPreflightPlan,
)
from app.services.background_job_service import fence_stalled_jobs, record_job_progress
from app.services.runtime_mutation_authority import RecoveryAuthority
from app.services.setup_lifecycle.evaluation_service import SetupLifecycleEvaluationService
from app.services.setup_lifecycle.snapshot_builder import SetupLifecycleSnapshotCaptureService
from app.services.winner_probability.capture_service import WinnerPredictionCaptureService
from app.services.winner_probability.config import load_winner_probability_config

RUN_ID = 702
STAGE = "FREEZING_DECISION_HANDOFF_MANIFEST"
TOKEN = "pipeline61-native-test-owner"


@pytest.fixture
def handoff_engine(disposable_postgres_database):
    _migrate(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database, pool_size=8)
    try:
        yield engine
    finally:
        engine.dispose()


def _source_seals(db):
    return list(
        db.execute(
            select(
                CoreCalculationEvidence.id,
                CoreCalculationEvidence.artifact_kind,
                CoreCalculationEvidence.payload_fingerprint,
                CoreCalculationEvidence.calculation_identity_fingerprint,
            )
            .where(CoreCalculationEvidence.run_id == RUN_ID)
            .where(CoreCalculationEvidence.artifact_kind != "SETUP")
            .order_by(CoreCalculationEvidence.id)
        )
    )


def _prepare(db, population, monkeypatch, note=None):
    config = load_winner_probability_config()
    config = replace(config, engine=replace(config.engine, enabled=True))
    native_seed = native_fixture.seed_price_frame
    native_quality = native_fixture._quality_values

    def seed_inputs(db, ticker, frame, cutoff_at):
        # An actual short benchmark history makes native Regime readiness
        # insufficient. Native Sector must freeze its omission and exact parent
        # evidence, exercising the same NULL-parent fallback as pipeline 61.
        if ticker in {"SPY", "QQQ"}:
            frame = frame.tail(20).copy()
        return native_seed(db, ticker, frame, cutoff_at)

    with monkeypatch.context() as inputs:
        inputs.setattr(native_fixture, "seed_price_frame", seed_inputs)
        inputs.setattr(
            native_fixture, "_quality_values",
            lambda: {**native_quality(), "financials": {"revenue": "1000"}},
        )
        cutoff, pipeline_id = _seed_universe(
            db,
            run_id=RUN_ID,
            tickers=tuple(f"R{index:03d}" for index in range(population)),
            cutoff_at=datetime(2026, 9, 18, 21, tzinfo=UTC),
            winner_config=config,
            technical_checkpoint_callback=(
                (lambda **values: note("native_technical_progress", **values)) if note else None
            ),
            ranking_checkpoint_callback=(
                (lambda **values: note("native_ranking_progress", **values)) if note else None
            ),
        )
    assert db.scalar(select(func.count()).select_from(TransitionPreflightPlan)) == 0
    assert db.scalar(select(func.count()).select_from(TransitionDecisionHandoffManifest)) == 0
    return cutoff, pipeline_id, config


def _progress_owner(engine):
    now = datetime.now(UTC)
    with Session(engine) as control:
        job = BackgroundJob(
            job_type="FULL_PIPELINE",
            status="RUNNING",
            execution_token=TOKEN,
            worker_id="pipeline61-native-worker",
            payload_json={},
            started_at=now,
            locked_at=now,
            heartbeat_at=now,
            lease_expires_at=now + timedelta(hours=2),
            progress_stage=STAGE,
            progress_sequence=0,
        )
        control.add(job)
        control.commit()
        return job.id


def _checkpoint_recorder(engine, job_id, checkpoints):
    def checkpoint(**values):
        checkpoints.append(dict(values))
        with Session(engine) as control:
            record_job_progress(
                control,
                job_id=job_id,
                execution_token=TOKEN,
                stage=STAGE,
                current_item=f"{values['phase']}:{values.get('current_item') or '-'}",
                last_completed_item=values.get("last_completed_item"),
                processed=values.get("processed"),
                total=values.get("total"),
                checkpoint_version=f"pipeline61-native:{values['phase']}:{len(checkpoints)}",
            )
            control.commit()
    return checkpoint


@pytest.mark.parametrize(
    "population", [3, pytest.param(159, marks=(pytest.mark.extended_scale, pytest.mark.slow))]
)
def test_native_fresh_handoff_and_complete_financial_tail(
    handoff_engine, monkeypatch, population, record_property
):
    """A real no-plan handoff and consumers, including the omitted-regime fallback."""
    pair_calls = []
    original_pair_check = handoff_service._sector_market_parent_matches

    def observe_pair(db, sector, market, **kwargs):
        pair_calls.append((sector.id, market.id))
        return original_pair_check(db, sector, market, **kwargs)

    monkeypatch.setattr(handoff_service, "_sector_market_parent_matches", observe_pair)
    measurement = output_directory("pipeline61-native") / f"tail-{population}.jsonl"

    def note(event, **values):
        with measurement.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"at": datetime.now(UTC).isoformat(), "event": event,
                                     **values}, default=str) + "\n")

    with Session(handoff_engine) as db:
        note("native_seed_started", population=population)
        cutoff, pipeline_id, winner_config = _prepare(db, population, monkeypatch, note)
        note("native_seed_completed")
        sector = db.scalar(
            select(SectorRotationSnapshot).where(SectorRotationSnapshot.run_id == RUN_ID)
        )
        assert sector is not None and sector.market_regime_snapshot_id is None
        before = _source_seals(db)
        assert before
        db.commit()
        job_id = _progress_owner(handoff_engine)
        checkpoints = []
        record_checkpoint = _checkpoint_recorder(handoff_engine, job_id, checkpoints)

        def checkpoint(**values):
            record_checkpoint(**values)
            note("native_handoff_progress", **values)

        started = perf_counter()
        manifest = handoff_service.freeze_transition_decision_handoff_manifest(
            db, upload_run_id=RUN_ID, market_cutoff=cutoff, checkpoint_callback=checkpoint
        )
        handoff_seconds = perf_counter() - started
        note("native_handoff_completed", seconds=handoff_seconds)
        assert manifest is not None
        assert len(manifest.manifest_json["decision_manifests"]) == population
        assert len(pair_calls) == 1, pair_calls
        phases = [item["phase"] for item in checkpoints]
        assert phases.index("SETUP_INPUTS_BUILT") < phases.index("PLAN_LOADED")
        assert "TEMPORAL_LINEAGE_VALIDATED" in phases
        if population == 159:
            assert "HANDOFF_EVIDENCE_HASHED" in phases
        manifest_id = manifest.id
        fingerprint = manifest.manifest_fingerprint
        db.commit()
        assert _source_seals(db) == before

        # A fresh transaction rechecks authority, but creates no second manifest.
        repeated = handoff_service.freeze_transition_decision_handoff_manifest(
            db, upload_run_id=RUN_ID, market_cutoff=cutoff, checkpoint_callback=checkpoint
        )
        assert repeated.id == manifest_id and repeated.manifest_fingerprint == fingerprint
        assert len(pair_calls) == 2
        db.commit()
        assert db.scalar(select(func.count()).select_from(TransitionPreflightPlan)) == 1
        assert db.scalar(select(func.count()).select_from(TransitionDecisionHandoffManifest)) == 1
        note("native_handoff_replay_completed")

        # No financial output or downstream terminal status is stubbed.
        capture = SetupLifecycleSnapshotCaptureService().capture_snapshots_for_run(
            db, RUN_ID, market_cutoff=cutoff
        )
        assert capture.failed == 0 and capture.captured == population
        db.commit()
        note("native_setup_capture_completed", captured=capture.captured)
        evaluated = SetupLifecycleEvaluationService().evaluate_run(
            db, RUN_ID, capture_result=capture, market_cutoff=cutoff,
            pipeline_run_id=pipeline_id,
        )
        assert evaluated.failed == 0 and evaluated.canonical_snapshots == population
        db.commit()
        note("native_lifecycle_completed", canonical=evaluated.canonical_snapshots)
        winner = WinnerPredictionCaptureService().capture_run(
            db, run_id=RUN_ID, market_cutoff=cutoff, config=winner_config,
            decision_handoff_manifest_id=manifest_id,
        )
        assert winner.failed == 0
        assert winner.inserted + winner.excluded == population
        db.commit()
        note("native_winner_completed", inserted=winner.inserted, excluded=winner.excluded)
        assert _source_seals(db) == before
        assert db.scalar(
            select(func.count()).select_from(SetupSignalSnapshot)
            .where(SetupSignalSnapshot.run_id == RUN_ID)
        ) == population

        # Exercise the actual production watchdog with native persisted progress.
        with Session(handoff_engine) as control:
            owner = control.get(BackgroundJob, job_id)
            assert owner.progress_sequence == len(checkpoints) > 0
            progressed_at = owner.last_progress_at
            assert fence_stalled_jobs(
                control, authority=RecoveryAuthority.normal("pipeline61.native.healthy"),
                default_timeout_seconds=60, market_data_timeout_seconds=120,
                long_stage_timeout_seconds=1800,
                now=progressed_at + timedelta(seconds=1799),
            ) == []
            control.rollback()
            assert fence_stalled_jobs(
                control, authority=RecoveryAuthority.normal("pipeline61.native.stalled"),
                default_timeout_seconds=60, market_data_timeout_seconds=120,
                long_stage_timeout_seconds=1800,
                now=progressed_at + timedelta(seconds=1801),
            ) == [job_id]
            control.commit()
            assert control.get(BackgroundJob, job_id).status == "STALLED"
        note("native_watchdog_checks_completed")
        for key, value in {
            "pipeline61_population": population,
            "pipeline61_native_handoff_complete": "true",
            "pipeline61_native_setup_canonical": evaluated.canonical_snapshots,
            "pipeline61_native_winner_completed": "true",
            "pipeline61_winner_inserted": winner.inserted,
            "pipeline61_winner_excluded": winner.excluded,
            "pipeline61_source_seals_unchanged": "true",
            "pipeline61_real_watchdog_preserved": "true",
            "pipeline61_native_sector_fallback": "true",
            "pipeline61_native_progress_before_plan": "true",
            "pipeline61_native_replay_idempotent": "true",
            "pipeline61_handoff_seconds": round(handoff_seconds, 3),
            "pipeline61_checkpoints": len(checkpoints),
            "pipeline61_measurement_path": str(measurement),
        }.items():
            record_property(key, value)


def test_native_cancelled_fresh_preparation_rolls_back_and_can_retry(handoff_engine, monkeypatch):
    class CancelledPreparation(RuntimeError):
        pass

    with Session(handoff_engine) as db:
        cutoff, _, _ = _prepare(db, 3, monkeypatch)
        before = _source_seals(db)

        def cancel(**values):
            if values["phase"] == "SETUP_INPUTS_BUILT":
                raise CancelledPreparation("native cancellation at verified work boundary")

        with pytest.raises(CancelledPreparation):
            handoff_service.freeze_transition_decision_handoff_manifest(
                db, upload_run_id=RUN_ID, market_cutoff=cutoff, checkpoint_callback=cancel
            )
        db.rollback()
        assert db.scalar(select(func.count()).select_from(TransitionPreflightPlan)) == 0
        assert db.scalar(select(func.count()).select_from(TransitionDecisionHandoffManifest)) == 0
        assert _source_seals(db) == before
        mutated = []

        def change_untracked_source(**values):
            if values["phase"] == "PLAN_LOADED":
                raw = db.scalar(
                    select(RawCompanyRow).where(RawCompanyRow.run_id == RUN_ID)
                    .order_by(RawCompanyRow.id).limit(1)
                )
                raw.raw_json["financials"]["revenue"] = "2000"
                assert not db.is_modified(raw, include_collections=True)
                mutated.append(raw.id)

        # Plain JSONB nested edits are invisible to db.dirty. Reusing a prepared
        # DTO across this public checkpoint would miss the changed source body.
        with pytest.raises((ValueError, handoff_service.TransitionPreflightError)) as rejected:
            handoff_service.freeze_transition_decision_handoff_manifest(
                db, upload_run_id=RUN_ID, market_cutoff=cutoff,
                checkpoint_callback=change_untracked_source,
            )
        assert mutated
        if isinstance(rejected.value, handoff_service.TransitionPreflightError):
            assert rejected.value.code == "DECISION_MANIFEST_MISMATCH"
        else:
            assert "MISMATCH" in str(rejected.value)
        db.rollback()
        assert db.scalar(select(func.count()).select_from(TransitionPreflightPlan)) == 0
        assert db.scalar(select(func.count()).select_from(TransitionDecisionHandoffManifest)) == 0
        assert _source_seals(db) == before
        retried = handoff_service.freeze_transition_decision_handoff_manifest(
            db, upload_run_id=RUN_ID, market_cutoff=cutoff
        )
        assert retried is not None
        db.commit()


def test_native_new_transaction_rejects_tampered_shared_evidence(handoff_engine, monkeypatch):
    with Session(handoff_engine) as db:
        cutoff, _, _ = _prepare(db, 3, monkeypatch)
        manifest = handoff_service.freeze_transition_decision_handoff_manifest(
            db, upload_run_id=RUN_ID, market_cutoff=cutoff
        )
        assert manifest is not None
        manifest_id, fingerprint = manifest.id, manifest.manifest_fingerprint
        db.commit()
        # Deliberate corruption stays inside this disposable PostgreSQL fixture.
        db.execute(
            text("UPDATE core_calculation_evidence SET payload_fingerprint=:bad "
                 "WHERE run_id=:run AND artifact_kind='SECTOR'"),
            {"bad": "0" * 64, "run": RUN_ID},
        )
        db.commit()
        db.expire_all()
        with pytest.raises((ValueError, handoff_service.TransitionPreflightError)):
            handoff_service.freeze_transition_decision_handoff_manifest(
                db, upload_run_id=RUN_ID, market_cutoff=cutoff
            )
        db.rollback()
        retained = db.get(TransitionDecisionHandoffManifest, manifest_id)
        assert retained.manifest_fingerprint == fingerprint
        assert db.scalar(select(func.count()).select_from(TransitionDecisionHandoffManifest)) == 1


def _qualification_receipt():
    from scripts.qa.qualify_pipeline61_handoff import REQUIRED_NATIVE, TARGETS

    return {
        "status": "PASS", "contract": "pipeline61-handoff-v1", "pytest_exit_code": 0,
        "tests": 1, "failed_errored_or_skipped": 0, "source_unchanged_during_checks": True,
        "tracked_source_clean": True, "selected_targets": list(TARGETS),
        "selected_target_coverage": dict.fromkeys(TARGETS, True),
        "native_159_proof_passed": True,
        "native_159_proof": {
            **REQUIRED_NATIVE, "pipeline61_winner_inserted": "0",
            "pipeline61_winner_excluded": "159", "pipeline61_handoff_seconds": "1.0",
            "pipeline61_checkpoints": "5",
        },
        "git_commit": "candidate", "source_sha256": {"source": "sealed"},
    }


def test_qualification_accepts_matching_native_contract():
    from scripts.qa.verify_pipeline61_handoff_qualification import verify

    verify(_qualification_receipt(), revision="candidate", hashes={"source": "sealed"})


@pytest.mark.parametrize("missing", [
    "native_159_proof", "native_159_proof_passed", "tracked_source_clean", "selected_targets",
    "selected_target_coverage",
])
def test_qualification_rejects_omitted_native_proof(missing):
    from scripts.qa.verify_pipeline61_handoff_qualification import verify

    report = _qualification_receipt()
    del report[missing]
    with pytest.raises(RuntimeError, match="HANDOFF_QUALIFICATION_REQUIRED"):
        verify(report, revision="candidate", hashes={"source": "sealed"})


@pytest.mark.parametrize("revision,hashes", [
    ("old", {"source": "sealed"}), ("candidate", {"source": "changed"}),
])
def test_qualification_rejects_stale_release(revision, hashes):
    from scripts.qa.verify_pipeline61_handoff_qualification import verify

    with pytest.raises(RuntimeError, match="HANDOFF_QUALIFICATION_STALE"):
        verify(_qualification_receipt(), revision=revision, hashes=hashes)
