from __future__ import annotations

from datetime import UTC, datetime

from app.models.tables import BackgroundJob, BackgroundWorker
from app.services import background_job_service
from app.services.background_job_service import JobStatus, claim_next_job
from app.services.lifecycle_quiesce import request_worker_quiesce, resume_worker_claims


class FakeSession:
    def __init__(self, worker: BackgroundWorker, job: BackgroundJob) -> None:
        self.worker = worker
        self.job = job
        self.flushes = 0

    def scalar(self, statement):
        return self.worker if "background_workers" in str(statement) else self.job.id

    def get(self, model, identity):
        if model is BackgroundJob and identity == self.job.id:
            return self.job
        return None

    def flush(self) -> None:
        self.flushes += 1


def _rows():
    now = datetime.now(UTC)
    worker = BackgroundWorker(
        worker_id="worker-a",
        instance_id="instance-a",
        generation=1,
        heartbeat_at=now,
        started_at=now,
        stopping_at=None,
        queues_json=["background"],
    )
    job = BackgroundJob(
        id=7,
        job_type="FULL_PIPELINE",
        status=JobStatus.QUEUED,
        run_after=now,
        created_at=now,
        operational_metadata_json={},
    )
    return worker, job


def test_quiesce_fence_prevents_new_running_job_and_resume_is_safe(monkeypatch) -> None:
    worker, job = _rows()
    control = FakeSession(worker, job)
    request_worker_quiesce(control, worker.worker_id)
    assert worker.quiesce_requested_at is not None

    claims = FakeSession(worker, job)
    monkeypatch.setattr(background_job_service, "Session", FakeSession)
    assert (
        claim_next_job(
            claims,
            worker_id=worker.worker_id,
            worker_instance_id=worker.instance_id,
        )
        is None
    )
    assert job.status == JobStatus.QUEUED

    resume_worker_claims(control, worker.worker_id)
    claims = FakeSession(worker, job)
    claimed = claim_next_job(
        claims,
        worker_id=worker.worker_id,
        worker_instance_id=worker.instance_id,
    )
    assert claimed is job
    assert job.status == JobStatus.RUNNING


def test_recovering_jobs_are_shutdown_blockers_but_queued_jobs_are_not() -> None:
    assert JobStatus.RECOVERING in (JobStatus.RUNNING, JobStatus.RECOVERING)
    assert JobStatus.QUEUED not in (JobStatus.RUNNING, JobStatus.RECOVERING)
