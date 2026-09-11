from datetime import UTC, datetime

import pytest

from app.models.tables import BackgroundJob, PipelineRun, PipelineStep, UploadRun
from app.services.background_job_service import JobStatus
from app.services.ceri.constants import CERI_PIPELINE_STEPS
from app.services.ceri.feature_flags import CeriFeatureFlags
from app.services.pipeline_service import (
    FULL_PIPELINE_JOB_TYPE,
    PIPELINE_STEP_NAMES,
    PipelineStatus,
    PipelineStepStatus,
    cancel_pipeline,
    get_pipeline_status,
    pipeline_step_names,
    resume_pipeline,
    start_pipeline,
)
from app.services.pre_enqueue_operational_gate import PreEnqueueOperationalGateError
from app.services.setup_lifecycle.constants import SLSE_PIPELINE_STEPS
from app.settings import RuntimeMode


@pytest.fixture(autouse=True)
def _disable_optional_pipeline_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import market_calculation_context_service

    create_context = market_calculation_context_service.create_pipeline_market_context
    monkeypatch.setattr(
        market_calculation_context_service,
        "create_pipeline_market_context",
        lambda db, pipeline: create_context(
            db,
            pipeline,
            cutoff_at=datetime(2026, 9, 5, 12, tzinfo=UTC),
        ),
    )
    monkeypatch.setattr(
        "app.services.pipeline_service.ceri_flags",
        lambda: CeriFeatureFlags(True, False, False, False, False, False, False),
    )
    monkeypatch.setattr(
        "app.services.pipeline_service.get_settings",
        lambda: type(
            "SettingsStub",
            (),
            {
                "setup_lifecycle_pipeline_step_enabled": False,
                "ceri_legacy_pipeline_scheduling_enabled": True,
                "ceri_batched_workflow_enabled": False,
            },
        )(),
    )


def test_start_pipeline_creates_pipeline_steps_and_background_job() -> None:
    upload_run = UploadRun(id=7, filename="sample.csv", status="COMPLETED")
    db = FakeDb(upload_runs={7: upload_run})

    pipeline = start_pipeline(db, upload_run_id=7, requested_by="local-user")

    assert pipeline.id == 1
    assert pipeline.upload_run_id == 7
    assert pipeline.status == PipelineStatus.PENDING
    assert pipeline.current_step == "VALIDATING_RUN"
    assert pipeline.requested_by == "local-user"
    assert pipeline.message == "Full pipeline is queued."

    steps = db.pipeline_steps_for(pipeline.id)
    assert [step.step_name for step in steps] == list(PIPELINE_STEP_NAMES)
    assert [step.step_order for step in steps] == list(range(1, len(PIPELINE_STEP_NAMES) + 1))
    assert {step.status for step in steps} == {PipelineStepStatus.PENDING}

    jobs = list(db.background_jobs.values())
    assert len(jobs) == 1
    job = jobs[0]
    assert job.job_type == FULL_PIPELINE_JOB_TYPE
    assert job.related_run_id == 7
    assert job.request_key == (
        "full-pipeline:run:7:policy:REQUIRE_IB:steps:VALIDATING_RUN,SCORING_FUNDAMENTALS,"
        "FETCHING_MARKET_DATA,SCORING_TECHNICALS,MARKET_REGIME_SNAPSHOT,"
        "COMBINING_RESULTS,RANKING_PROFILES,SECTOR_ROTATION_SNAPSHOT,"
        "CAPTURING_WINNER_PREDICTIONS"
    )
    assert job.status == JobStatus.QUEUED
    assert job.payload_json["pipeline_run_id"] == pipeline.id
    assert job.payload_json["market_cutoff_at"]
    assert job.payload_json["input_as_of_session"]
    assert job.payload_json["market_calendar_version"] == "swinglens-us-equities-v1"
    assert job.payload_json["bar_readiness_version"] == "daily-close-plus-15m-v1"
    assert {
        key: value
        for key, value in pipeline.result_json.items()
        if key
        not in {
            "market_calculation_context_id",
            "market_cutoff_at",
            "input_as_of_session",
            "market_calendar_version",
            "bar_readiness_version",
            "transition_preflight_plan_id",
            "transition_evidence_fingerprint",
        }
    } == {
        "background_job_id": job.id,
        "market_data_policy": "REQUIRE_IB",
        "ib_preflight_status": None,
        "ib_preflight_checked_at": None,
        "ib_host": None,
        "ib_port": None,
    }
    assert pipeline.result_json["input_as_of_session"] == "2026-09-04"
    assert pipeline.result_json["market_calendar_version"] == "swinglens-us-equities-v1"
    assert pipeline.result_json["bar_readiness_version"] == "daily-close-plus-15m-v1"


def test_new_pipeline_requests_running_prewarm_preemption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_run = UploadRun(id=7, filename="sample.csv", status="COMPLETED")
    db = FakeDb(upload_runs={7: upload_run})
    calls = []
    monkeypatch.setattr(
        "app.services.pipeline_service.request_active_prewarm_preemption",
        lambda db, *, pipeline_run_id: calls.append(pipeline_run_id) or [91],
    )

    pipeline = start_pipeline(db, upload_run_id=7, requested_by="local-user")

    assert calls == [pipeline.id]
    assert {
        key: value
        for key, value in pipeline.result_json.items()
        if key
        not in {
            "market_calculation_context_id",
            "market_cutoff_at",
            "input_as_of_session",
            "market_calendar_version",
            "bar_readiness_version",
            "transition_preflight_plan_id",
            "transition_evidence_fingerprint",
        }
    } == {
        "background_job_id": 1,
        "market_data_policy": "REQUIRE_IB",
        "ib_preflight_status": None,
        "ib_preflight_checked_at": None,
        "ib_host": None,
        "ib_port": None,
        "preempted_prewarm_job_ids": [91],
    }
    assert pipeline.result_json["input_as_of_session"] == "2026-09-04"


def test_start_pipeline_coalesces_matching_active_pipeline_request() -> None:
    upload_run = UploadRun(id=7, filename="sample.csv", status="COMPLETED")
    existing_pipeline = PipelineRun(
        id=3,
        upload_run_id=7,
        status=PipelineStatus.PENDING,
        current_step="VALIDATING_RUN",
        result_json={"background_job_id": 10},
    )
    existing_job = BackgroundJob(
        id=10,
        job_type=FULL_PIPELINE_JOB_TYPE,
        request_key=(
            "full-pipeline:run:7:policy:REQUIRE_IB:steps:VALIDATING_RUN,SCORING_FUNDAMENTALS,"
            "FETCHING_MARKET_DATA,SCORING_TECHNICALS,MARKET_REGIME_SNAPSHOT,"
            "COMBINING_RESULTS,RANKING_PROFILES,SECTOR_ROTATION_SNAPSHOT,"
            "CAPTURING_WINNER_PREDICTIONS"
        ),
        status=JobStatus.QUEUED,
        payload_json={"pipeline_run_id": 3},
    )
    db = FakeDb(
        upload_runs={7: upload_run},
        pipeline_runs={3: existing_pipeline},
        background_jobs={10: existing_job},
    )

    pipeline = start_pipeline(db, upload_run_id=7, requested_by="local-user")

    assert pipeline is existing_pipeline
    assert pipeline.__dict__.get("_coalesced") is True
    assert len(db.pipeline_runs) == 1
    assert len(db.background_jobs) == 1


def test_start_pipeline_reuses_preparing_pipeline_after_parent_job_finishes() -> None:
    upload_run = UploadRun(id=7, filename="sample.csv", status="COMPLETED")
    existing_pipeline = PipelineRun(
        id=3,
        upload_run_id=7,
        status=PipelineStatus.PREPARING,
        current_step="VALIDATING_RUN",
        result_json={"background_job_id": 11, "repair_job_id": 11},
    )
    repair_job = BackgroundJob(
        id=11,
        job_type="SEC_READINESS_REPAIR",
        status=JobStatus.RUNNING,
        payload_json={"pipeline_run_id": 3},
    )
    db = FakeDb(
        upload_runs={7: upload_run},
        pipeline_runs={3: existing_pipeline},
        background_jobs={11: repair_job},
    )

    returned = start_pipeline(db, upload_run_id=7)

    assert returned is existing_pipeline
    assert returned.__dict__.get("_coalesced") is True
    assert len(db.pipeline_runs) == 1
    assert len(db.background_jobs) == 1


def test_pipeline_step_names_insert_setup_lifecycle_only_when_enabled() -> None:
    assert pipeline_step_names(setup_lifecycle_pipeline_step_enabled=False) == PIPELINE_STEP_NAMES

    enabled_steps = pipeline_step_names(setup_lifecycle_pipeline_step_enabled=True)

    sector_index = enabled_steps.index("SECTOR_ROTATION_SNAPSHOT")
    winner_index = enabled_steps.index("CAPTURING_WINNER_PREDICTIONS")
    assert enabled_steps[sector_index + 1 : winner_index] == SLSE_PIPELINE_STEPS


def test_pipeline_step_names_insert_ceri_before_setup_lifecycle_and_winner() -> None:
    ceri_steps = pipeline_step_names(
        ceri_run_capture_enabled=True,
        setup_lifecycle_pipeline_step_enabled=False,
    )

    sector_index = ceri_steps.index("SECTOR_ROTATION_SNAPSHOT")
    winner_index = ceri_steps.index("CAPTURING_WINNER_PREDICTIONS")
    assert ceri_steps[sector_index + 1 : winner_index] == CERI_PIPELINE_STEPS

    combined_steps = pipeline_step_names(
        ceri_run_capture_enabled=True,
        setup_lifecycle_pipeline_step_enabled=True,
    )

    sector_index = combined_steps.index("SECTOR_ROTATION_SNAPSHOT")
    winner_index = combined_steps.index("CAPTURING_WINNER_PREDICTIONS")
    assert combined_steps[sector_index + 1 : winner_index] == (
        *CERI_PIPELINE_STEPS,
        *SLSE_PIPELINE_STEPS,
    )


def test_pipeline_step_names_can_pause_only_legacy_ceri_scheduling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.pipeline_service.ceri_flags",
        lambda: CeriFeatureFlags(True, True, True, False, True, False, False),
    )
    monkeypatch.setattr(
        "app.services.pipeline_service.get_settings",
        lambda: type(
            "SettingsStub",
            (),
            {
                "setup_lifecycle_pipeline_step_enabled": False,
                "ceri_legacy_pipeline_scheduling_enabled": False,
                "ceri_batched_workflow_enabled": False,
            },
        )(),
    )

    steps = pipeline_step_names()

    assert "CERI_PROVIDER_INGEST" not in steps
    sector_index = steps.index("SECTOR_ROTATION_SNAPSHOT")
    assert CERI_PIPELINE_STEPS == steps[sector_index + 1 : -1]


def test_start_pipeline_can_create_setup_lifecycle_steps_when_enabled() -> None:
    upload_run = UploadRun(id=7, filename="sample.csv", status="COMPLETED")
    db = FakeDb(upload_runs={7: upload_run})

    pipeline = start_pipeline(
        db,
        upload_run_id=7,
        setup_lifecycle_pipeline_step_enabled=True,
    )

    steps = db.pipeline_steps_for(pipeline.id)
    assert [step.step_name for step in steps] == list(
        pipeline_step_names(setup_lifecycle_pipeline_step_enabled=True)
    )
    assert [step.step_order for step in steps] == list(range(1, len(steps) + 1))


def test_start_pipeline_can_create_ceri_and_setup_lifecycle_steps_when_enabled() -> None:
    upload_run = UploadRun(id=7, filename="sample.csv", status="COMPLETED")
    db = FakeDb(upload_runs={7: upload_run})

    pipeline = start_pipeline(
        db,
        upload_run_id=7,
        ceri_run_capture_enabled=True,
        setup_lifecycle_pipeline_step_enabled=True,
    )

    steps = db.pipeline_steps_for(pipeline.id)
    assert [step.step_name for step in steps] == list(
        pipeline_step_names(
            ceri_run_capture_enabled=True,
            setup_lifecycle_pipeline_step_enabled=True,
        )
    )
    assert [step.step_order for step in steps] == list(range(1, len(steps) + 1))


def test_start_pipeline_raises_for_missing_upload_run() -> None:
    db = FakeDb()

    with pytest.raises(ValueError, match="Upload run 404 was not found"):
        start_pipeline(db, upload_run_id=404)


def test_certification_gate_failure_precedes_pipeline_and_job_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_run = UploadRun(id=154, filename="canary.csv", status="COMPLETED")
    db = FakeDb(upload_runs={154: upload_run})
    monkeypatch.setattr(
        "app.services.pipeline_service.get_settings",
        lambda: type(
            "CertificationSettingsStub",
            (),
            {
                "runtime_mode": RuntimeMode.CERTIFICATION,
                "setup_lifecycle_pipeline_step_enabled": True,
                "ceri_legacy_pipeline_scheduling_enabled": True,
                "ceri_batched_workflow_enabled": False,
            },
        )(),
    )
    monkeypatch.setattr(
        "app.services.pre_enqueue_operational_gate.validate_pre_enqueue_operational_gate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PreEnqueueOperationalGateError(
                "IB_API_NOT_READY",
                "IB API session unavailable. No pipeline was created.",
                plan_id=3,
                context_id=7,
            )
        ),
    )

    with pytest.raises(PreEnqueueOperationalGateError, match="IB_API_NOT_READY"):
        start_pipeline(db, upload_run_id=154, transition_preflight_plan_id=3)

    assert db.pipeline_runs == {}
    assert db.background_jobs == {}
    assert db.added == []


def test_certification_duplicate_action_returns_consumed_pipeline_without_new_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_run = UploadRun(id=154, filename="canary.csv", status="COMPLETED")
    pipeline = PipelineRun(id=145, upload_run_id=154, status=PipelineStatus.PENDING)
    db = FakeDb(upload_runs={154: upload_run}, pipeline_runs={145: pipeline})
    monkeypatch.setattr(
        "app.services.pipeline_service.get_settings",
        lambda: type(
            "CertificationSettingsStub",
            (),
            {"runtime_mode": RuntimeMode.CERTIFICATION},
        )(),
    )
    monkeypatch.setattr(
        "app.services.transition_preflight_plan_service.pipeline_for_consumed_preflight",
        lambda *_args, **_kwargs: pipeline,
    )

    returned = start_pipeline(db, upload_run_id=154, transition_preflight_plan_id=3)

    assert returned is pipeline
    assert returned.__dict__["_coalesced"] is True
    assert len(db.pipeline_runs) == 1
    assert db.background_jobs == {}


def test_get_pipeline_status_returns_status_dto() -> None:
    pipeline = PipelineRun(
        id=3,
        upload_run_id=7,
        status=PipelineStatus.PENDING,
        current_step="VALIDATING_RUN",
        requested_by="local-user",
        message="queued",
        result_json={"background_job_id": 10},
    )
    steps = [
        PipelineStep(
            id=2,
            pipeline_run_id=3,
            step_name="SCORING_FUNDAMENTALS",
            step_order=2,
            status=PipelineStepStatus.PENDING,
            retry_count=0,
        ),
        PipelineStep(
            id=1,
            pipeline_run_id=3,
            step_name="VALIDATING_RUN",
            step_order=1,
            status=PipelineStepStatus.COMPLETED,
            retry_count=0,
            message="ok",
        ),
    ]
    db = FakeDb(pipeline_runs={3: pipeline}, pipeline_steps=steps)

    status = get_pipeline_status(db, pipeline_run_id=3)

    assert status.pipeline_run_id == 3
    assert status.upload_run_id == 7
    assert status.status == PipelineStatus.PENDING
    assert status.current_step == "VALIDATING_RUN"
    assert status.background_job_id == 10
    assert status.result_json == {"background_job_id": 10}
    assert [step.step_name for step in status.steps] == [
        "VALIDATING_RUN",
        "SCORING_FUNDAMENTALS",
    ]
    assert status.steps[0].message == "ok"


def test_get_pipeline_status_raises_for_missing_pipeline() -> None:
    db = FakeDb()

    with pytest.raises(ValueError, match="Pipeline run 404 was not found"):
        get_pipeline_status(db, pipeline_run_id=404)


def test_cancel_pipeline_requests_background_job_cancel_and_marks_pending_steps() -> None:
    pipeline = PipelineRun(
        id=3,
        upload_run_id=7,
        status=PipelineStatus.PENDING,
        current_step="VALIDATING_RUN",
        result_json={"background_job_id": 10},
    )
    job = BackgroundJob(id=10, job_type=FULL_PIPELINE_JOB_TYPE, status=JobStatus.QUEUED)
    steps = [
        PipelineStep(
            id=1,
            pipeline_run_id=3,
            step_name="VALIDATING_RUN",
            step_order=1,
            status=PipelineStepStatus.PENDING,
            retry_count=0,
        ),
        PipelineStep(
            id=2,
            pipeline_run_id=3,
            step_name="SCORING_FUNDAMENTALS",
            step_order=2,
            status=PipelineStepStatus.PENDING,
            retry_count=0,
        ),
    ]
    db = FakeDb(
        pipeline_runs={3: pipeline},
        background_jobs={10: job},
        pipeline_steps=steps,
    )

    returned = cancel_pipeline(db, pipeline_run_id=3)

    assert returned is pipeline
    assert pipeline.status == PipelineStatus.CANCELLED
    assert pipeline.completed_at is not None
    assert pipeline.message == "Pipeline cancellation requested."
    assert job.requested_cancel is True
    assert job.status == JobStatus.CANCELLED
    assert {step.status for step in steps} == {PipelineStepStatus.CANCELLED}


def test_cancel_pipeline_does_not_rewrite_terminal_pipeline_status() -> None:
    pipeline = PipelineRun(
        id=3,
        upload_run_id=7,
        status=PipelineStatus.COMPLETED,
        result_json={},
    )
    db = FakeDb(pipeline_runs={3: pipeline})

    cancel_pipeline(db, pipeline_run_id=3)

    assert pipeline.status == PipelineStatus.COMPLETED
    assert pipeline.completed_at is None


def test_cancel_pipeline_running_pipeline_preserves_active_status() -> None:
    pipeline = PipelineRun(
        id=3,
        upload_run_id=7,
        status=PipelineStatus.RUNNING,
        result_json={},
    )
    db = FakeDb(pipeline_runs={3: pipeline})

    cancel_pipeline(db, pipeline_run_id=3)

    assert pipeline.status == PipelineStatus.RUNNING
    assert pipeline.completed_at is None
    assert pipeline.message == "Pipeline cancellation requested."


def test_resume_pipeline_queues_checkpoint_job_without_rewriting_completed_steps() -> None:
    pipeline = PipelineRun(
        id=3,
        upload_run_id=7,
        status=PipelineStatus.BLOCKED,
        current_step="CERI_PROVIDER_INGEST",
        result_json={"background_job_id": 10},
    )
    steps = [
        PipelineStep(
            id=1,
            pipeline_run_id=3,
            step_name="VALIDATING_RUN",
            step_order=1,
            status=PipelineStepStatus.COMPLETED,
            retry_count=0,
        ),
        PipelineStep(
            id=2,
            pipeline_run_id=3,
            step_name="CERI_PROVIDER_INGEST",
            step_order=2,
            status=PipelineStepStatus.BLOCKED,
            retry_count=0,
        ),
    ]
    db = FakeDb(pipeline_runs={3: pipeline}, pipeline_steps=steps)

    returned = resume_pipeline(db, 3)

    assert returned.status == PipelineStatus.PENDING
    assert returned.current_step == "CERI_PROVIDER_INGEST"
    assert steps[0].status == PipelineStepStatus.COMPLETED
    assert steps[0].retry_count == 0
    job = next(iter(db.background_jobs.values()))
    assert job.payload_json["pipeline_run_id"] == 3
    assert job.payload_json["resume_from_step"] == "CERI_PROVIDER_INGEST"
    assert job.payload_json["market_cutoff_at"]
    assert job.payload_json["input_as_of_session"]
    assert pipeline.result_json["background_job_id"] == job.id


class FakeScalarResult:
    def __init__(self, rows) -> None:
        self.rows = rows

    def all(self):
        return self.rows


class FakeDb:
    def __init__(
        self,
        upload_runs: dict[int, UploadRun] | None = None,
        pipeline_runs: dict[int, PipelineRun] | None = None,
        pipeline_steps: list[PipelineStep] | None = None,
        background_jobs: dict[int, BackgroundJob] | None = None,
    ) -> None:
        self.upload_runs = upload_runs or {}
        self.pipeline_runs = pipeline_runs or {}
        self.pipeline_steps = {step.id: step for step in pipeline_steps or []}
        self.background_jobs = background_jobs or {}
        self.added = []
        self.flushes = 0
        self._next_ids = {
            PipelineRun: 1,
            PipelineStep: 1,
            BackgroundJob: 1,
        }

    def add(self, row) -> None:
        self.added.append(row)

    def flush(self) -> None:
        self.flushes += 1
        for row in self.added:
            if isinstance(row, PipelineRun):
                self._store_new(row, self.pipeline_runs)
            elif isinstance(row, PipelineStep):
                self._store_new(row, self.pipeline_steps)
            elif isinstance(row, BackgroundJob):
                self._store_new(row, self.background_jobs)

    def get(self, model, row_id):
        if model is UploadRun:
            return self.upload_runs.get(row_id)
        if model is PipelineRun:
            return self.pipeline_runs.get(row_id)
        if model is BackgroundJob:
            return self.background_jobs.get(row_id)
        return None

    def scalars(self, _statement):
        return FakeScalarResult(
            sorted(self.pipeline_steps.values(), key=lambda step: step.step_order)
        )

    def pipeline_steps_for(self, pipeline_run_id: int) -> list[PipelineStep]:
        return [
            step
            for step in sorted(self.pipeline_steps.values(), key=lambda item: item.step_order)
            if step.pipeline_run_id == pipeline_run_id
        ]

    def _store_new(self, row, rows: dict[int, object]) -> None:
        if row.id is None:
            row_type = type(row)
            row.id = self._next_ids[row_type]
            self._next_ids[row_type] += 1
        rows[row.id] = row
