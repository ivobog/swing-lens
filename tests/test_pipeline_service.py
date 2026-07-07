import pytest

from app.models.tables import BackgroundJob, PipelineRun, PipelineStep, UploadRun
from app.services.background_job_service import JobStatus
from app.services.pipeline_service import (
    FULL_PIPELINE_JOB_TYPE,
    PIPELINE_STEP_NAMES,
    PipelineStatus,
    PipelineStepStatus,
    cancel_pipeline,
    get_pipeline_status,
    start_pipeline,
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
    assert [step.step_order for step in steps] == [1, 2, 3, 4, 5]
    assert {step.status for step in steps} == {PipelineStepStatus.PENDING}

    jobs = list(db.background_jobs.values())
    assert len(jobs) == 1
    job = jobs[0]
    assert job.job_type == FULL_PIPELINE_JOB_TYPE
    assert job.related_run_id == 7
    assert job.status == JobStatus.QUEUED
    assert job.payload_json == {"pipeline_run_id": pipeline.id}
    assert pipeline.result_json == {"background_job_id": job.id}


def test_start_pipeline_raises_for_missing_upload_run() -> None:
    db = FakeDb()

    with pytest.raises(ValueError, match="Upload run 404 was not found"):
        start_pipeline(db, upload_run_id=404)


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
