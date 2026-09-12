from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models.tables import BackgroundJob, BackgroundWorker
from app.observability.metrics import operational_metrics
from app.services import background_job_service
from app.services.background_job_service import JobStatus, claim_next_job
from app.services.lifecycle_quiesce import (
    WorkerQuiesceIdentity,
    WorkerQuiesceIdentityConflict,
    request_worker_quiesce,
    resume_worker_claims,
    worker_quiesce_identity,
)
from app.services.worker_registry import heartbeat_worker


class FakeSession:
    def __init__(self, worker: BackgroundWorker, job: BackgroundJob) -> None:
        self.worker = worker
        self.job = job
        self.flushes = 0

    def scalar(self, statement):
        return self.worker if "background_workers" in str(statement) else self.job.id

    def get(self, model, identity):
        if model is BackgroundWorker and identity == self.worker.worker_id:
            return self.worker
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


def test_repeated_quiesce_request_preserves_request_and_acknowledgement() -> None:
    worker, job = _rows()
    control = FakeSession(worker, job)
    requested_at = datetime(2026, 9, 12, 10, 50, tzinfo=UTC)
    acknowledged_at = datetime(2026, 9, 12, 10, 50, 5, tzinfo=UTC)

    request_worker_quiesce(control, worker.worker_id, now=requested_at)
    worker.quiesced_at = acknowledged_at
    request_worker_quiesce(
        control,
        worker.worker_id,
        now=datetime(2026, 9, 12, 10, 51, tzinfo=UTC),
    )

    assert worker.quiesce_requested_at == requested_at
    assert worker.quiesced_at == acknowledged_at


def test_wrong_worker_generation_cannot_establish_quiesce_fence() -> None:
    worker, job = _rows()
    control = FakeSession(worker, job)
    wrong_identity = WorkerQuiesceIdentity(
        instance_id=worker.instance_id,
        generation=worker.generation + 1,
        process_id=worker.process_id,
        process_started_at=worker.process_started_at,
    )

    with pytest.raises(WorkerQuiesceIdentityConflict, match="changed before quiescence"):
        request_worker_quiesce(
            control,
            worker.worker_id,
            expected_identity=wrong_identity,
        )

    assert worker.quiesce_requested_at is None
    assert worker_quiesce_identity(worker) != wrong_identity


def test_worker_acknowledgement_preserves_first_timestamp_and_records_latency() -> None:
    worker, job = _rows()
    worker.hostname = "quiesce-test"
    worker.process_id = 101
    control = FakeSession(worker, job)
    requested_at = datetime(2026, 9, 12, 10, 50, tzinfo=UTC)
    request_worker_quiesce(control, worker.worker_id, now=requested_at)
    operational_metrics.reset()

    heartbeat_worker(
        control,
        worker.worker_id,
        hostname=worker.hostname,
        process_id=worker.process_id,
        instance_id=worker.instance_id,
        now=requested_at + timedelta(seconds=5),
    )
    heartbeat_worker(
        control,
        worker.worker_id,
        hostname=worker.hostname,
        process_id=worker.process_id,
        instance_id=worker.instance_id,
        now=requested_at + timedelta(seconds=10),
    )

    assert worker.quiesced_at == requested_at + timedelta(seconds=5)
    exposition = operational_metrics.as_prometheus()
    assert "swinglens_worker_quiesce_ack_latency_seconds_count 1.0" in exposition
    assert "swinglens_worker_quiesce_ack_latency_seconds_sum 5.0" in exposition
