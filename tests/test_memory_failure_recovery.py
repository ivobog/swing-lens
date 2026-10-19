from datetime import UTC, datetime

from test_background_job_service import FakeDb, FakeScalarResult, _running_job

from app.models.tables import PipelineRun, PipelineStep
from app.services.background_job_service import (
    JobStatus,
    reconcile_jobs_for_worker_loss,
    requeue_stalled_jobs,
)
from app.services.runtime_mutation_authority import RecoveryAuthority

AUTHORITY = RecoveryAuthority.normal("test.memory_recovery")


class MemoryRecoveryDb(FakeDb):
    def __init__(self, job):
        super().__init__(stale_jobs=[job])
        self.pipeline = PipelineRun(id=43, upload_run_id=43, status="CAPTURING_SETUP_SIGNALS")
        self.step = PipelineStep(
            id=11,
            pipeline_run_id=43,
            step_order=11,
            step_name="CAPTURING_SETUP_SIGNALS",
            status="RUNNING",
        )
        self.completed = [
            PipelineStep(id=i, step_order=i, status="COMPLETED") for i in range(1, 11)
        ]

    def scalars(self, statement):
        entity = statement.column_descriptions[0].get("entity")
        return FakeScalarResult(self.completed if entity is PipelineStep else self.stale_jobs)

    def get(self, model, row_id):
        return self.pipeline if model is PipelineRun else None

    def scalar(self, statement):
        return self.step


def _fence(db, job, *, code="WORKER_MEMORY_CRITICAL"):
    job.status = JobStatus.RUNNING
    job.worker_id = "worker-a"
    job.worker_instance_id = f"replacement-{job.progress_sequence}"
    job.execution_token = "replacement-token"
    return reconcile_jobs_for_worker_loss(
        db,
        authority=AUTHORITY,
        worker_id="worker-a",
        worker_instance_id=job.worker_instance_id,
        reason="Worker exceeded 6144 MB memory budget.",
        failure_code=code,
        now=datetime.now(UTC),
    )


def test_memory_replays_use_durable_stage_boundary_and_exhaust_existing_budget():
    job = _running_job()
    job.payload_json = {"pipeline_run_id": 43}
    job.progress_stage = "CAPTURING_SETUP_SIGNALS"
    db = MemoryRecoveryDb(job)
    for attempt, checkpoint in enumerate(
        ("PREPARING_PUBLICATION:PD", "EVIDENCE:CRGY", "BUILD:GO"), 1
    ):
        job.progress_sequence = attempt * 100
        job.progress_current_item = checkpoint
        job.progress_processed = attempt * 25
        assert _fence(db, job).fenced_job_ids == (job.id,)
        assert job.execution_token is None
        assert job.operational_metadata_json["progress_recovery"]["identical_count"] == attempt
        assert requeue_stalled_jobs(db, authority=AUTHORITY, max_identical_recoveries=3) == (
            attempt < 3
        )
    assert job.status == JobStatus.BLOCKED
    assert job.retry_count == 0
    assert job.recovery_count == 2
    assert db.pipeline.status == db.step.status == "BLOCKED"
    assert db.step.result_json["terminal_reason_code"] == "REPEATED_MEMORY_CRITICAL_LIMIT_REACHED"
    assert "6144 MB" in job.error_message
    assert "memory-critical" in db.step.error_message


def test_committed_stage_advancement_resets_memory_failure_identity():
    job = _running_job()
    job.payload_json = {"pipeline_run_id": 43}
    db = MemoryRecoveryDb(job)
    _fence(db, job)
    _fence(db, job)
    assert job.operational_metadata_json["progress_recovery"]["identical_count"] == 2
    db.step.status = "COMPLETED"
    db.completed.append(db.step)
    job.progress_stage = "EVALUATING_SETUP_LIFECYCLES"
    _fence(db, job)
    assert job.operational_metadata_json["progress_recovery"]["identical_count"] == 1


def test_nonpipeline_committed_checkpoint_changes_memory_recovery_identity():
    job = _running_job()
    job.job_type = "WINNER_OUTCOME_MATURATION"
    job.payload_json = {"after_id": 10}
    db = MemoryRecoveryDb(job)
    _fence(db, job)
    _fence(db, job)
    assert job.operational_metadata_json["progress_recovery"]["identical_count"] == 2
    job.payload_json = {"after_id": 20}
    _fence(db, job)
    assert job.operational_metadata_json["progress_recovery"]["identical_count"] == 1


def test_transient_process_loss_does_not_consume_memory_failure_budget():
    job = _running_job()
    db = MemoryRecoveryDb(job)
    assert _fence(db, job, code=None).fenced_job_ids == (job.id,)
    assert "progress_recovery" not in job.operational_metadata_json
    assert requeue_stalled_jobs(db, authority=AUTHORITY) == 1


def test_supervisor_classifies_memory_fence_before_requeue(monkeypatch):
    from test_worker_supervisor_reliability import _settings, _worker

    from app import worker_supervisor as supervisor

    worker = _worker()
    observed = []
    monkeypatch.setattr(supervisor, "get_settings", _settings)
    monkeypatch.setattr(supervisor, "_registered_worker", lambda _: worker)
    monkeypatch.setattr(supervisor, "_registered_worker_process_alive", lambda _: True)
    monkeypatch.setattr(supervisor, "_registered_worker_control_loop_alive", lambda _: True)
    monkeypatch.setattr(supervisor, "_safe_memory_status", lambda _: "CRITICAL")
    monkeypatch.setattr(supervisor, "_fence_no_progress", lambda *a, **k: [])

    def fence(*args, **kwargs):
        observed.append(kwargs["failure_code"])
        return supervisor.WorkerJobReconciliation(fenced_job_ids=(1525,))

    monkeypatch.setattr(supervisor, "_fence_worker", fence)
    monkeypatch.setattr(
        supervisor, "_terminate_worker_instance", lambda *a: observed.append("stop")
    )
    monkeypatch.setattr(supervisor, "_retire_worker_registration", lambda *a: None)
    monkeypatch.setattr(supervisor, "_requeue", lambda *a, **k: observed.append("requeue"))
    supervisor._supervise_once(worker_id="worker-a", queues="interactive", child=None)
    assert observed == ["WORKER_MEMORY_CRITICAL", "stop", "requeue"]
