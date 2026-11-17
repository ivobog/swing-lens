from datetime import UTC, datetime

import pytest
from test_background_job_service import FakeDb, FakeScalarResult, _running_job

from app.models.tables import PipelineRun, PipelineStep
from app.services.background_job_service import (
    JobStatus,
    _finalize_terminal_pipeline_publication,
    reconcile_jobs_for_worker_loss,
    requeue_stalled_jobs,
)
from app.services.runtime_mutation_authority import RecoveryAuthority

AUTHORITY = RecoveryAuthority.normal("test.memory_recovery")


class MemoryScalarResult(FakeScalarResult):
    def __iter__(self):
        return iter(self.all())


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
        if entity is PipelineStep:
            values = statement.compile().params.values()
            return MemoryScalarResult([self.step] if "RUNNING" in values else self.completed)
        if "STALLED" in statement.compile().params.values():
            return FakeScalarResult([job for job in self.stale_jobs if job.status == "STALLED"])
        return FakeScalarResult(self.stale_jobs)

    def get(self, model, row_id):
        return self.pipeline if model is PipelineRun else None

    def scalar(self, statement):
        return self.step


def _fence(db, job, *, code="WORKER_MEMORY_CRITICAL", memory_evidence=None):
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
        memory_evidence=memory_evidence,
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


@pytest.mark.parametrize("entry_hint", [None, "VALIDATING_RUN", "FETCHING_MARKET_DATA"])
@pytest.mark.parametrize("cause", [None, "WORKER_MEMORY_CRITICAL"])
def test_sector_worker_loss_preserves_checkpoint_and_archives_actual_cause(entry_hint, cause):
    job = _running_job()
    job.payload_json = {"pipeline_run_id": 43, "scope_id": "frozen-scope", "context": 104}
    if entry_hint:
        job.payload_json["resume_from_step"] = entry_hint
    job.progress_stage = "SECTOR_ROTATION_SNAPSHOT"
    job.progress_current_item = "sector-snapshot"
    job.operational_metadata_json = {
        "current_attempt": {"attempt_number": 2, "started_at": "2026-11-10T15:00:00Z"},
        "sector_progress": {"phase": "PERSISTENCE_STARTED"},
    }
    db = MemoryRecoveryDb(job)
    db.pipeline.current_step = db.step.step_name = "SECTOR_ROTATION_SNAPSHOT"
    db.pipeline.result_json = {"ib_fetch_plan": {"immutable": "retained"}}
    before = [(row.id, row.status) for row in db.completed]
    assert _fence(db, job, code=cause, memory_evidence={"private_bytes": 6442450945})
    assert job.payload_json == {
        "pipeline_run_id": 43,
        "scope_id": "frozen-scope",
        "context": 104,
        "resume_from_step": "SECTOR_ROTATION_SNAPSHOT",
    }
    assert db.pipeline.result_json["ib_fetch_plan"] == {"immutable": "retained"}
    assert [(row.id, row.status) for row in db.completed] == before
    assert db.step.status == "INTERRUPTED"
    attempt = db.step.result_json["attempt_history"][-1]
    assert attempt["reason"] == (cause or "WORKER_PROCESS_LOST")
    assert attempt["finished_at"] == db.step.completed_at.isoformat()
    assert db.pipeline.result_json["recovery_continuation"]["validation_state"] == "PENDING"
    assert requeue_stalled_jobs(db, authority=AUTHORITY) == 1
    if cause:
        evidence = job.operational_metadata_json["progress_recovery"]["memory_evidence"]
        assert evidence["private_bytes"] == 6442450945
        assert evidence["stage"] == "SECTOR_ROTATION_SNAPSHOT"
        assert evidence["sector_phase"] == "PERSISTENCE_STARTED"
        assert evidence["attempt_number"] == 2
        assert evidence["attempt_started_at"] == "2026-11-10T15:00:00Z"


@pytest.mark.parametrize("stale_hint", ["VALIDATING_RUN", "FETCHING_MARKET_DATA"])
def test_memory_stage_follows_running_sector_instead_of_stale_job_hint(stale_hint):
    job = _running_job()
    db = MemoryRecoveryDb(job)
    job.payload_json = {"pipeline_run_id": 43, "resume_from_step": stale_hint}
    job.progress_stage = stale_hint
    job.progress_current_item = stale_hint + ":OLD"
    job.operational_metadata_json = {"sector_progress": {"phase": "PERSISTENCE_STARTED"}}
    db.pipeline.current_step = db.step.step_name = "SECTOR_ROTATION_SNAPSHOT"
    _fence(db, job, memory_evidence={"private_bytes": 6442450945})
    recovery = job.operational_metadata_json["progress_recovery"]
    assert recovery["stall_identity"]["stage"] == "SECTOR_ROTATION_SNAPSHOT"
    assert recovery["memory_evidence"]["stage"] == "SECTOR_ROTATION_SNAPSHOT"
    assert recovery["memory_evidence"]["sector_phase"] is None
    assert recovery["memory_evidence"]["subphase"] is None
    assert job.payload_json["resume_from_step"] == "SECTOR_ROTATION_SNAPSHOT"


def test_ranking_memory_fence_does_not_copy_old_sector_phase():
    job = _running_job()
    db = MemoryRecoveryDb(job)
    job.payload_json = {"pipeline_run_id": 43, "resume_from_step": "VALIDATING_RUN"}
    job.progress_stage = "RANKING_PROFILES"
    job.progress_current_item = "CALCULATING:ACME"
    job.operational_metadata_json = {"sector_progress": {"phase": "PERSISTENCE_STARTED"}}
    db.pipeline.current_step = db.step.step_name = "RANKING_PROFILES"
    _fence(db, job, memory_evidence={"private_bytes": 6442450945})
    recovery = job.operational_metadata_json["progress_recovery"]
    assert recovery["stall_identity"]["stage"] == "RANKING_PROFILES"
    assert recovery["memory_evidence"]["stage"] == "RANKING_PROFILES"
    assert recovery["memory_evidence"]["sector_phase"] is None
    assert recovery["memory_evidence"]["subphase"] == "CALCULATING:ACME"
    assert db.pipeline.current_step == "RANKING_PROFILES"
    assert job.payload_json["resume_from_step"] == "VALIDATING_RUN"


@pytest.mark.parametrize("status", ["COMPLETED", "PARTIAL", "CANCELLED", "FAILED", "BLOCKED"])
def test_worker_loss_does_not_reopen_terminal_pipeline(status):
    job = _running_job()
    job.payload_json = {"pipeline_run_id": 43}
    job.progress_stage = "SECTOR_ROTATION_SNAPSHOT"
    db = MemoryRecoveryDb(job)
    db.pipeline.status = status
    db.pipeline.result_json = {"retained": "published"}
    _fence(db, job)
    assert requeue_stalled_jobs(db, authority=AUTHORITY) == 1
    assert db.pipeline.status == status
    assert db.pipeline.result_json == {"retained": "published"}


def _terminal_owner(job):
    db = MemoryRecoveryDb(job)
    db.pipeline.status = "COMPLETED"
    db.pipeline.execution_authority_state = "ACTIVE"
    job.related_run_id = db.pipeline.upload_run_id
    job.payload_json = {"pipeline_run_id": db.pipeline.id}
    for name, value in (
        ("scope_id", "scope"),
        ("refresh_cycle_id", "cycle"),
        ("acquisition_plan_id", "plan"),
    ):
        setattr(job, name, value)
        setattr(db.pipeline, name, value)
        job.payload_json[name] = value
    return db


@pytest.mark.parametrize("cause", [None, "WORKER_MEMORY_CRITICAL"])
@pytest.mark.parametrize(
    "pipeline_status,job_status",
    [
        ("COMPLETED", "COMPLETED"),
        ("PARTIAL", "COMPLETED"),
        ("FAILED", "FAILED"),
        ("BLOCKED", "BLOCKED"),
        ("CANCELLED", "CANCELLED"),
    ],
)
def test_terminal_publication_closes_job_without_requeue(pipeline_status, job_status, cause):
    job = _running_job()
    db = _terminal_owner(job)
    db.pipeline.status = pipeline_status
    db.pipeline.result_json = {"published": "unchanged"}
    result = _fence(db, job, code=cause)
    assert result.finalized_job_ids == (job.id,)
    assert result.fenced_job_ids == ()
    assert job.status == job_status
    assert job.execution_token is job.worker_id is job.lease_owner is None
    assert requeue_stalled_jobs(db, authority=AUTHORITY) == 0
    assert db.pipeline.status == pipeline_status
    assert db.pipeline.result_json == {"published": "unchanged"}


@pytest.mark.parametrize("cause", [None, "WORKER_MEMORY_CRITICAL"])
def test_published_completion_wins_over_late_job_cancellation(cause):
    job = _running_job()
    db = _terminal_owner(job)
    job.requested_cancel = True
    result = _fence(db, job, code=cause)
    assert result.finalized_job_ids == (job.id,)
    assert result.cancelled_job_ids == result.fenced_job_ids == ()
    assert job.status == db.pipeline.status == "COMPLETED"
    assert job.requested_cancel is True
    assert requeue_stalled_jobs(db, authority=AUTHORITY) == 0


@pytest.mark.parametrize(
    "damage",
    [
        "scope",
        "payload_scope",
        "upload",
        "inactive",
        "dependency",
        "missing",
        "malformed",
        "overrange",
        "boolean",
    ],
)
def test_terminal_publication_closure_rejects_foreign_or_malformed_owner(damage):
    job = _running_job()
    db = _terminal_owner(job)
    if damage == "scope":
        job.scope_id = "foreign"
    elif damage == "payload_scope":
        job.payload_json["scope_id"] = "foreign"
    elif damage == "upload":
        job.related_run_id = 999
    elif damage == "inactive":
        db.pipeline.execution_authority_state = "REVOKED"
    elif damage == "dependency":
        job.pipeline_dependency_id = 999
    elif damage == "missing":
        job.payload_json.pop("pipeline_run_id")
    else:
        job.payload_json["pipeline_run_id"] = {
            "malformed": "not-an-id",
            "overrange": "9223372036854775808",
            "boolean": True,
        }[damage]
    assert not _finalize_terminal_pipeline_publication(
        db,
        job,
        now=datetime.now(UTC),
        cause_code="WORKER_PROCESS_LOST",
        reason="Worker exited.",
    )
    assert job.status == "RUNNING"


def test_supervisor_classifies_memory_fence_before_requeue(monkeypatch):
    from test_worker_supervisor_reliability import _settings, _worker

    from app import worker_supervisor as supervisor

    worker = _worker()
    observed = []
    monkeypatch.setattr(supervisor, "get_settings", _settings)
    monkeypatch.setattr(supervisor, "_registered_worker", lambda _: worker)
    monkeypatch.setattr(supervisor, "_registered_worker_process_alive", lambda _: True)
    monkeypatch.setattr(supervisor, "_registered_worker_control_loop_alive", lambda _: True)
    monkeypatch.setattr(supervisor, "_safe_memory_observation", lambda _: ("CRITICAL", {}))
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
