from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.tables import (
    BackgroundJob,
    BackgroundWorker,
    PipelineRun,
    PipelineStep,
    UploadRun,
    WinnerEvidenceManifest,
    WinnerEvidenceManifestMember,
    WinnerForwardOutcome,
    WinnerOutcomeDefinition,
    WinnerPredictionSnapshot,
    WinnerProbabilityEstimate,
    WinnerTargetStopOutcome,
    WinnerTemporalValidityDecision,
)
from app.services.background_job_service import (
    JobLeaseLost,
    claim_next_job,
    enqueue_job,
    fence_stalled_jobs,
    record_job_progress,
    requeue_stalled_jobs,
)
from app.services.winner_probability.capture_service import WinnerPredictionCaptureService
from app.services.winner_probability.config import load_winner_probability_config


def test_winner_ticker_transactions_survive_fencing_and_resume_without_duplicates(
    disposable_postgres_database: str,
) -> None:
    _migrate(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    tickers = ["AAA", "BBB", "CCC", "DDD", "EEE"]
    now = datetime.now(UTC)
    with sessions() as setup:
        recovered_run = UploadRun(filename="recovered.csv", status="COMPLETED", row_count=5)
        uninterrupted_run = UploadRun(
            filename="uninterrupted.csv", status="COMPLETED", row_count=5
        )
        definition = _outcome_definition()
        setup.add_all([recovered_run, uninterrupted_run, definition])
        setup.flush()
        pipeline = PipelineRun(
            upload_run_id=recovered_run.id,
            status="RUNNING",
            current_step="CAPTURING_WINNER_PREDICTIONS",
        )
        setup.add(pipeline)
        setup.flush()
        setup.add(
            PipelineStep(
                pipeline_run_id=pipeline.id,
                step_name="CAPTURING_WINNER_PREDICTIONS",
                step_order=12,
                status="RUNNING",
                started_at=now,
            )
        )
        setup.add_all(
            [
                BackgroundWorker(
                    worker_id=worker_id,
                    instance_id=f"{worker_id}-instance",
                    generation=1,
                    queues_json=["interactive", "background"],
                )
                for worker_id in ("worker-a", "worker-b")
            ]
        )
        job = enqueue_job(
            setup,
            "FULL_PIPELINE",
            {"pipeline_run_id": pipeline.id},
            related_run_id=recovered_run.id,
        )
        setup.commit()
        recovered_run_id = recovered_run.id
        uninterrupted_run_id = uninterrupted_run.id
        definition_id = definition.id
        job_id = job.id

    with sessions() as owner:
        job = claim_next_job(owner, "worker-a", lease_seconds=900)
        assert job is not None
        old_token = str(job.execution_token)
        record_job_progress(
            owner,
            job_id=job.id,
            execution_token=old_token,
            stage="EVALUATING_SETUP_LIFECYCLES",
            current_item=None,
            last_completed_item="EEE",
            processed=132,
            total=132,
        )
        owner.commit()
        service = _SyntheticWinnerCaptureService(
            _SyntheticRepository(recovered_run_id, definition_id, tickers)
        )
        fenced = False

        def first_attempt_progress(progress_db: Session, **progress) -> None:
            nonlocal fenced
            if progress.get("processed") == 3 and not fenced:
                with sessions() as age_progress:
                    victim = age_progress.get(BackgroundJob, job_id)
                    victim.heartbeat_at = now
                    victim.last_progress_at = now - timedelta(minutes=31)
                    victim.operational_metadata_json = {
                        "progress_watchdog": {
                            "progress_sequence": int(victim.progress_sequence or 0),
                            "unchanged_since": (now - timedelta(minutes=31)).isoformat(),
                        }
                    }
                    age_progress.commit()
                with sessions() as watchdog:
                    assert fence_stalled_jobs(
                        watchdog,
                        default_timeout_seconds=300,
                        market_data_timeout_seconds=300,
                        long_stage_timeout_seconds=1800,
                        now=now,
                        worker_id="worker-a",
                        worker_heartbeat_at=now,
                    ) == [job_id]
                    watchdog.commit()
                fenced = True
            record_job_progress(
                progress_db,
                job_id=job_id,
                execution_token=old_token,
                **progress,
            )

        with pytest.raises(JobLeaseLost):
            service.capture_run(
                owner,
                run_id=recovered_run_id,
                progress_callback=first_attempt_progress,
            )
        owner.rollback()

    with sessions() as interrupted:
        assert _artifact_counts(interrupted, recovered_run_id) == (2, 2, 2, 2, 2, 2, 2)
        step = interrupted.scalar(
            select(PipelineStep).where(
                PipelineStep.step_name == "CAPTURING_WINNER_PREDICTIONS"
            )
        )
        assert step.status == "INTERRUPTED"
        assert step.result_json["attempt_history"][-1]["status"] == "INTERRUPTED"
        with pytest.raises(JobLeaseLost):
            record_job_progress(
                interrupted,
                job_id=job_id,
                execution_token=old_token,
                stage="CAPTURING_WINNER_PREDICTIONS",
                current_item="CCC",
                processed=3,
                total=5,
            )
        interrupted.rollback()

    with sessions() as recovery:
        assert requeue_stalled_jobs(recovery, job_ids=[job_id], now=now) == 1
        recovery.commit()
    with sessions() as replacement:
        replacement_job = claim_next_job(replacement, "worker-b", lease_seconds=900)
        assert replacement_job is not None
        replacement_token = str(replacement_job.execution_token)
        assert replacement_token != old_token
        replacement.commit()

        resumed = _SyntheticWinnerCaptureService(
            _SyntheticRepository(recovered_run_id, definition_id, tickers)
        ).capture_run(
            replacement,
            run_id=recovered_run_id,
            progress_callback=lambda progress_db, **progress: record_job_progress(
                progress_db,
                job_id=job_id,
                execution_token=replacement_token,
                **progress,
            ),
        )
        assert resumed.duplicate == 2
        assert resumed.inserted == 3

    with sessions() as uninterrupted:
        complete = _SyntheticWinnerCaptureService(
            _SyntheticRepository(uninterrupted_run_id, definition_id, tickers)
        ).capture_run(uninterrupted, run_id=uninterrupted_run_id)
        assert complete.inserted == 5

    with sessions() as verify:
        assert _artifact_counts(verify, recovered_run_id) == (5, 5, 5, 5, 5, 5, 5)
        job = verify.get(BackgroundJob, job_id)
        assert job.progress_stage == "CAPTURING_WINNER_PREDICTIONS"
        assert job.progress_processed == 5
        assert job.progress_total == 5
        assert job.progress_last_completed_item == "EEE"
        assert job.execution_token == replacement_token
        assert _probabilities(verify, recovered_run_id) == _probabilities(
            verify, uninterrupted_run_id
        )
    engine.dispose()


class _SyntheticRepository:
    def __init__(self, run_id: int, definition_id: int, tickers: list[str]) -> None:
        self.run_id = run_id
        self.definition_id = definition_id
        self.tickers = tickers

    def load_run_context(self, _db: Session, run_id: int):
        assert run_id == self.run_id
        return SimpleNamespace(
            tickers=[
                SimpleNamespace(raw_row=SimpleNamespace(ticker=ticker))
                for ticker in self.tickers
            ]
        )

    def get_outcome_definition(self, db: Session, **_kwargs):
        return db.get(WinnerOutcomeDefinition, self.definition_id)


class _SyntheticWinnerCaptureService(WinnerPredictionCaptureService):
    def __init__(self, repository: _SyntheticRepository) -> None:
        super().__init__(repository=repository, decision_time_estimate_service=SimpleNamespace())

    def _capture_ticker(
        self,
        db: Session,
        *,
        run_id: int,
        ticker_context,
        primary_definition,
        totals,
        **_kwargs,
    ) -> None:
        ticker = ticker_context.raw_row.ticker
        existing = db.scalar(
            select(WinnerPredictionSnapshot)
            .where(WinnerPredictionSnapshot.run_id == run_id)
            .where(WinnerPredictionSnapshot.ticker == ticker)
        )
        if existing is not None:
            totals.duplicate += 1
            return
        cutoff = datetime(2026, 9, 11, 20, 0, tzinfo=UTC)
        prediction = WinnerPredictionSnapshot(
            run_id=run_id,
            ticker=ticker,
            prediction_as_of_date=date(2026, 9, 11),
            source_data_cutoff_at=cutoff,
            decision_at=cutoff,
            captured_at=cutoff,
            planned_entry_session=date(2026, 9, 14),
            entry_schedule_status="RESOLVED",
            entry_data_status="NOT_DUE",
            eligibility_status="ELIGIBLE",
            feature_schema_version="recovery-test-v1",
            feature_vector_hash=hashlib.sha256(ticker.encode()).hexdigest(),
            config_hash="recovery-test-config",
            calculation_version="recovery-test-calc",
            feature_json={},
            source_ids_json={},
            warning_flags_json=[],
            lineage_json={"point_in_time_validated": True},
        )
        db.add(prediction)
        db.flush()
        temporal = WinnerTemporalValidityDecision(
            prediction_id=prediction.id,
            validation_sequence=1,
            status="VALID",
            entry_timing_valid=True,
            source_cutoff_valid=True,
            semantic_input_time_valid=True,
            evidence_eligible=True,
            reason_codes_json=[],
            validation_version="recovery-test-v1",
            decision_at=cutoff,
            entry_session=date(2026, 9, 14),
            entry_open_at=datetime(2026, 9, 14, 13, 30, tzinfo=UTC),
            evaluated_at=cutoff,
            evaluated_by="RECOVERY_TEST",
            metadata_json={},
        )
        forward = WinnerForwardOutcome(
            prediction_id=prediction.id,
            entry_model="NEXT_OPEN",
            horizon_sessions=5,
            entry_session=date(2026, 9, 14),
            due_session=date(2026, 9, 18),
            status="PENDING",
            metadata_json={},
        )
        db.add_all([temporal, forward])
        db.flush()
        target = WinnerTargetStopOutcome(
            prediction_id=prediction.id,
            outcome_definition_id=primary_definition.id,
            forward_outcome_id=forward.id,
            entry_model="NEXT_OPEN",
            horizon_sessions=5,
            status="PENDING",
            target_pct=Decimal("2.5"),
            stop_pct=Decimal("2.0"),
            metadata_json={},
        )
        manifest = WinnerEvidenceManifest(
            manifest_hash=hashlib.sha256(f"{run_id}:{ticker}".encode()).hexdigest(),
            hash_algorithm="sha256",
            content_encoding="json",
            member_count=1,
            payload_json={"ticker": ticker},
        )
        db.add_all([target, manifest])
        db.flush()
        probability = Decimal(int(hashlib.sha256(ticker.encode()).hexdigest()[:2], 16)) / Decimal(
            "255"
        )
        estimate = WinnerProbabilityEstimate(
            prediction_id=prediction.id,
            outcome_definition_id=primary_definition.id,
            estimate_kind="DECISION_TIME",
            source="COHORT",
            source_version="recovery-test-v1",
            evidence_manifest_id=manifest.id,
            training_cutoff_at=cutoff,
            point_probability=probability,
            lower_bound=max(Decimal("0"), probability - Decimal("0.1")),
            upper_bound=min(Decimal("1"), probability + Decimal("0.1")),
            sample_n=1,
            effective_n=Decimal("1"),
            evidence_grade="Low",
            insufficient_reasons_json=[],
            config_hash="recovery-test-config",
            feature_schema_version="recovery-test-v1",
            evidence_manifest_hash=manifest.manifest_hash,
            metadata_json={},
        )
        member = WinnerEvidenceManifestMember(
            manifest_id=manifest.id,
            member_ordinal=1,
            prediction_id=prediction.id,
            forward_outcome_id=forward.id,
            forward_revision=1,
            target_stop_outcome_id=target.id,
            target_stop_revision=1,
            temporal_validity_decision_id=temporal.id,
            evidence_origin="NATIVE_1_1",
            inclusion_weight=Decimal("1"),
            primary_winner=True,
            member_hash=hashlib.sha256(f"member:{run_id}:{ticker}".encode()).hexdigest(),
        )
        db.add_all([estimate, member])
        totals.inserted += 1
        totals.pending_outcomes += 1
        totals.target_stop_outcomes += 1
        totals.decision_time_estimates += 1


def _outcome_definition() -> WinnerOutcomeDefinition:
    config = load_winner_probability_config()
    raw = config.primary_outcome_definition
    return WinnerOutcomeDefinition(
        definition_id=raw.id,
        label="Recovery test",
        entry_model=raw.entry_model,
        horizon_sessions=raw.horizon_sessions,
        target_pct=raw.target_pct,
        stop_pct=raw.stop_pct,
        calculation_version=config.engine.calculation_version,
        config_hash=config.config_hash,
        is_primary=True,
        is_active=True,
        metadata_json={},
    )


def _artifact_counts(db: Session, run_id: int) -> tuple[int, ...]:
    prediction_ids = select(WinnerPredictionSnapshot.id).where(
        WinnerPredictionSnapshot.run_id == run_id
    )
    manifest_ids = select(WinnerProbabilityEstimate.evidence_manifest_id).where(
        WinnerProbabilityEstimate.prediction_id.in_(prediction_ids)
    )
    return (
        db.scalar(
            select(func.count()).select_from(WinnerPredictionSnapshot).where(
                WinnerPredictionSnapshot.run_id == run_id
            )
        ),
        db.scalar(
            select(func.count()).select_from(WinnerTemporalValidityDecision).where(
                WinnerTemporalValidityDecision.prediction_id.in_(prediction_ids)
            )
        ),
        db.scalar(
            select(func.count()).select_from(WinnerForwardOutcome).where(
                WinnerForwardOutcome.prediction_id.in_(prediction_ids)
            )
        ),
        db.scalar(
            select(func.count()).select_from(WinnerTargetStopOutcome).where(
                WinnerTargetStopOutcome.prediction_id.in_(prediction_ids)
            )
        ),
        db.scalar(
            select(func.count()).select_from(WinnerProbabilityEstimate).where(
                WinnerProbabilityEstimate.prediction_id.in_(prediction_ids)
            )
        ),
        db.scalar(
            select(func.count()).select_from(WinnerEvidenceManifest).where(
                WinnerEvidenceManifest.id.in_(manifest_ids)
            )
        ),
        db.scalar(
            select(func.count()).select_from(WinnerEvidenceManifestMember).where(
                WinnerEvidenceManifestMember.manifest_id.in_(manifest_ids)
            )
        ),
    )


def _probabilities(db: Session, run_id: int) -> list[tuple[str, Decimal]]:
    return list(
        db.execute(
            select(WinnerPredictionSnapshot.ticker, WinnerProbabilityEstimate.point_probability)
            .join(
                WinnerProbabilityEstimate,
                WinnerProbabilityEstimate.prediction_id == WinnerPredictionSnapshot.id,
            )
            .where(WinnerPredictionSnapshot.run_id == run_id)
            .order_by(WinnerPredictionSnapshot.ticker)
        )
    )


def _migrate(database_url: str) -> None:
    environment = dict(os.environ)
    environment["DATABASE_URL"] = database_url
    environment["SWINGLENS_DATABASE_SAFETY_CONTEXT"] = "DISPOSABLE_TEST"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=os.getcwd(),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
