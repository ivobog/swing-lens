from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.models.tables import (
    BackgroundJob,
    EstimateKind,
    UploadRun,
    WinnerCohortDefinition,
    WinnerCohortStatistic,
    WinnerEstimateEvidenceMember,
    WinnerEvidenceManifest,
    WinnerForwardOutcome,
    WinnerOutcomeDefinition,
    WinnerPredictionSnapshot,
    WinnerProbabilityEstimate,
    WinnerProcessingRun,
    WinnerTargetStopOutcome,
)
from app.services.background_job_service import JobStatus
from app.services.winner_probability.backfill import (
    BackfillExecutionResult,
    BackfillPlan,
    BackfillRequest,
    WinnerProbabilityBackfillService,
    WinnerProbabilityRolloutService,
)
from app.services.winner_probability.capture_service import WinnerPredictionCaptureResult
from app.services.winner_probability.config import load_winner_probability_config
from app.services.winner_probability.evidence_service import EvidenceOutcome
from app.services.winner_probability.job_handlers import (
    WINNER_HISTORICAL_BACKFILL,
    execute_historical_backfill_job,
)
from app.services.winner_probability.probability_estimator import ProbabilityEstimator
from app.settings import Settings


def test_phase_11_rollout_gate_keeps_defaults_disabled_before_activation() -> None:
    report = WinnerProbabilityRolloutService().readiness_report(
        settings=Settings(_env_file=None, job_worker_enabled=False),
        migrations_applied=True,
        lease_hardened=True,
        decision_time_estimator_available=True,
        selected_runs_validated=False,
        reproduction_validated=False,
    )

    assert report.safe_defaults
    assert report.ready_for_manual_capture
    assert not report.ready_for_pipeline_capture
    assert "selected_capture_validation" in report.blockers
    assert "reproduction_validation" in report.blockers


def test_backfill_plan_allows_only_completed_trustworthy_historical_runs() -> None:
    completed_trusted = _upload_run(1, notes="point_in_time_trustworthy")
    untrusted = _upload_run(2)
    incomplete = _upload_run(3, status="FAILED", notes="point_in_time_trustworthy")
    db = BackfillFakeDb(runs=(completed_trusted, untrusted, incomplete))

    plan = WinnerProbabilityBackfillService().plan_backfill(
        db,
        BackfillRequest(run_ids=(1, 2, 3, 404)),
    )

    assert [item.status for item in plan.items] == ["READY", "SKIPPED", "SKIPPED", "SKIPPED"]
    assert plan.items[0].source_quality_flags == (
        "reconstructed_history",
        "exclude_from_production_training",
        "trusted_by_run_notes",
    )
    assert plan.items[1].reason == "untrusted_point_in_time_source"
    assert plan.items[2].reason == "run_not_completed"
    assert plan.items[3].reason == "run_not_found"


def test_backfill_execution_invokes_capture_as_reconstructed_replay() -> None:
    run = _upload_run(1, notes="point_in_time_trustworthy")
    db = BackfillFakeDb(runs=(run,))
    capture = RecordingCaptureService()
    config = load_winner_probability_config()

    result = WinnerProbabilityBackfillService().execute_backfill(
        db,
        BackfillRequest(run_ids=(1,)),
        config=config,
        capture_service=capture,
    )

    assert result.captured_runs == 1
    assert result.counts["inserted"] == 1
    assert capture.calls[0]["run_id"] == 1
    assert capture.calls[0]["reconstruction_method"] == "HISTORICAL_AS_OF_REPLAY"
    assert "exclude_from_production_training" in capture.calls[0]["source_quality_flags"]
    assert capture.calls[0]["production_training_allowed"] is False


def test_reconstructed_predictions_create_as_of_replay_estimates() -> None:
    config = load_winner_probability_config()
    db = EstimatorFakeDb()
    prediction = _prediction(999)
    prediction.reconstruction_method = "HISTORICAL_AS_OF_REPLAY"
    outcome_definition = _definition()
    evidence = tuple(_evidence(index, won=index % 2 == 0) for index in range(20))

    result = ProbabilityEstimator(
        evidence_service=FakeEvidenceService({"L5": evidence})
    ).create_decision_time_estimate(
        db,
        prediction=prediction,
        outcome_definition=outcome_definition,
        config=config,
    )

    assert result.estimate.estimate_kind == EstimateKind.AS_OF_REPLAY
    assert result.estimate.metadata_json["reconstruction_method"] == "HISTORICAL_AS_OF_REPLAY"


def test_historical_backfill_job_records_processing_run_and_counts() -> None:
    db = BackfillFakeDb(runs=())
    job = _job(
        job_type=WINNER_HISTORICAL_BACKFILL,
        payload={"run_ids": [1, 2], "trusted_run_ids": [1], "limit": 2},
    )
    service = FakeBackfillService()

    result = execute_historical_backfill_job(db, job, backfill_service=service)

    assert result["job_type"] == WINNER_HISTORICAL_BACKFILL
    assert result["status"] == JobStatus.PARTIAL
    assert result["captured_runs"] == 1
    assert job.status == JobStatus.PARTIAL
    assert db.processing_runs[0].process_type == WINNER_HISTORICAL_BACKFILL
    assert db.processing_runs[0].status == JobStatus.PARTIAL
    assert db.processing_runs[0].counts_json["skipped_runs"] == 1
    assert db.processing_runs[0].checkpoint_json["last_completed_phase"] == "historical_backfill"


class RecordingCaptureService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def capture_run(self, _db, **kwargs) -> WinnerPredictionCaptureResult:
        self.calls.append(kwargs)
        return WinnerPredictionCaptureResult(inserted=1, pending_outcomes=10)


class FakeBackfillService:
    def execute_backfill(self, _db, request, **kwargs) -> BackfillExecutionResult:
        assert request.run_ids == (1, 2)
        assert request.trusted_run_ids == (1,)
        assert callable(kwargs["should_cancel"])
        return BackfillExecutionResult(
            plan=BackfillPlan(
                items=(
                    type("Item", (), {"status": "READY"})(),
                    type("Item", (), {"status": "SKIPPED"})(),
                )
            ),
            captured_runs=1,
            skipped_runs=1,
            counts=WinnerPredictionCaptureResult(inserted=1).as_dict(),
        )


class BackfillFakeDb:
    def __init__(self, *, runs: tuple[UploadRun, ...]) -> None:
        self.runs = runs
        self.processing_runs: list[WinnerProcessingRun] = []
        self.flushes = 0
        self._next_id = 1

    def scalars(self, _statement):
        return iter(self.runs)

    def scalar(self, _statement):
        return False

    def add(self, row) -> None:
        if getattr(row, "id", None) is None:
            row.id = self._next_id
            self._next_id += 1
        if isinstance(row, WinnerProcessingRun):
            self.processing_runs.append(row)

    def flush(self) -> None:
        self.flushes += 1


class FakeEvidenceService:
    def __init__(self, evidence_by_level: dict[str, tuple[EvidenceOutcome, ...]]) -> None:
        self.evidence_by_level = evidence_by_level

    def load_evidence(self, _db, **kwargs) -> tuple[EvidenceOutcome, ...]:
        return self.evidence_by_level.get(kwargs["cohort_key"].level, ())


class EstimatorFakeDb:
    def __init__(self) -> None:
        self.rows: dict[type, list] = {
            WinnerCohortDefinition: [],
            WinnerCohortStatistic: [],
            WinnerEvidenceManifest: [],
            WinnerEstimateEvidenceMember: [],
            WinnerProbabilityEstimate: [],
        }
        self._next_id = 1

    def scalar(self, _statement):
        return None

    def add(self, row) -> None:
        if getattr(row, "id", None) is None:
            row.id = self._next_id
            self._next_id += 1
        self.rows.setdefault(type(row), []).append(row)

    def flush(self) -> None:
        return None

    def get_existing_probability_estimate(self, **kwargs):
        return next(
            (
                estimate
                for estimate in self.rows[WinnerProbabilityEstimate]
                if estimate.prediction_id == kwargs["prediction_id"]
                and estimate.outcome_definition_id == kwargs["outcome_definition_id"]
                and estimate.estimate_kind == kwargs["estimate_kind"]
                and estimate.source_version == kwargs["source_version"]
                and estimate.training_cutoff_at == kwargs["training_cutoff_at"]
            ),
            None,
        )


def _upload_run(
    id: int,
    *,
    status: str = "COMPLETED",
    notes: str | None = None,
) -> UploadRun:
    return UploadRun(
        id=id,
        filename=f"run-{id}.csv",
        uploaded_at=datetime(2026, 7, id, 15, 0, tzinfo=UTC),
        processed_at=datetime(2026, 7, id, 16, 0, tzinfo=UTC),
        row_count=10,
        status=status,
        notes=notes,
    )


def _job(
    *,
    job_type: str,
    payload: dict,
) -> BackgroundJob:
    return BackgroundJob(
        id=11,
        job_type=job_type,
        status=JobStatus.RUNNING,
        payload_json=payload,
        execution_token="token-1",
        lease_owner="worker-a",
    )


def _prediction(id: int) -> WinnerPredictionSnapshot:
    return WinnerPredictionSnapshot(
        id=id,
        run_id=id,
        ticker=f"T{id}",
        prediction_as_of_date=datetime(2026, 1, 1, tzinfo=UTC).date(),
        source_data_cutoff_at=datetime(2026, 7, 1, 21, 0, tzinfo=UTC),
        entry_schedule_status="RESOLVED",
        entry_data_status="AVAILABLE",
        eligibility_status="ELIGIBLE",
        feature_schema_version="owpe-features-1.0.0",
        feature_vector_hash=f"hash-{id}",
        config_hash="config",
        calculation_version="calc",
        feature_json={
            "setup_family": "Breakout",
            "dual_score_band": "8_plus",
            "score_band": "8_plus",
            "market_risk_state": "Green",
            "sector_state": "Leading",
            "ranking_profile": "momentum_swing",
            "sector_leadership_bucket": "leader",
            "market_regime_family": "Confirmed Uptrend",
        },
    )


def _definition() -> WinnerOutcomeDefinition:
    return WinnerOutcomeDefinition(
        id=1,
        definition_id="T2_5_S2_0_H5_NEXT_OPEN",
        entry_model="NEXT_OPEN",
        horizon_sessions=5,
        target_pct=Decimal("2.5"),
        stop_pct=Decimal("2.0"),
        calculation_version="owpe-calc-1.0.0",
        config_hash="config",
        is_primary=True,
        is_active=True,
    )


def _evidence(index: int, *, won: bool) -> EvidenceOutcome:
    prediction = _prediction(index)
    prediction.source_data_cutoff_at = datetime(2026, 1, 1, 21, 0, tzinfo=UTC)
    forward = WinnerForwardOutcome(
        id=index + 1000,
        prediction_id=prediction.id,
        entry_model="NEXT_OPEN",
        horizon_sessions=5,
        status="MATURED",
        revision=1,
        is_current_revision=True,
        close_return_pct=Decimal("2.0") if won else Decimal("-1.0"),
        mfe_pct=Decimal("3.0"),
        mae_pct=Decimal("-1.0"),
        matured_at=datetime(2026, 1, 10, tzinfo=UTC),
    )
    target = WinnerTargetStopOutcome(
        id=index + 2000,
        prediction_id=prediction.id,
        outcome_definition_id=1,
        forward_outcome_id=forward.id,
        entry_model="NEXT_OPEN",
        horizon_sessions=5,
        status="MATURED",
        revision=1,
        is_current_revision=True,
        target_pct=Decimal("2.5"),
        stop_pct=Decimal("2.0"),
        first_event="TARGET_FIRST" if won else "STOP_FIRST",
        primary_winner=won,
        evaluated_at=datetime(2026, 1, 10, tzinfo=UTC),
    )
    return EvidenceOutcome(
        prediction=prediction,
        forward_outcome=forward,
        target_stop_outcome=target,
    )
