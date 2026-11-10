from datetime import UTC, datetime

import pytest

from app.models.tables import BackgroundJob, UploadRun
from app.routers.run_routes import _pipeline_recovery_fields, _pipeline_status_payload
from app.services.pipeline_service import PipelineStatusDto
from app.templates import templates


def _job(status="RUNNING"):
    return BackgroundJob(
        id=1882, job_type="FULL_PIPELINE", status=status,
        payload_json={"resume_from_step": "SECTOR_ROTATION_SNAPSHOT"},
        operational_metadata_json={
            "progress_recovery": {
                "failure_reason": "Worker exceeded 6144 MB memory budget.",
                "stall_identity": {"decision": "WORKER_MEMORY_CRITICAL"},
                "memory_evidence": {
                    "observed_at": "2026-11-10T15:52:24+00:00", "registered_pid": 14492,
                    "private_bytes": 7000 * 1024**2, "rss_bytes": 6500 * 1024**2,
                    "measured_bytes": 7000 * 1024**2, "critical_mb": 6144,
                    "stage": "SECTOR_ROTATION_SNAPSHOT", "sector_phase": "PERSISTENCE_STARTED",
                    "attempt_number": 1, "attempt_started_at": "2026-11-10T15:04:28+00:00",
                    "execution_token": "must-not-be-exposed", "unapproved_nested": {"secret": 1},
                },
            },
            "recovery_continuation": {
                "boundary": "SECTOR_ROTATION_SNAPSHOT", "validation_state": "VALIDATED",
                "unapproved": "must-not-be-exposed",
            },
        },
    )


def test_status_exposes_original_memory_reason_after_replacement_worker_is_running():
    job = _job()

    class Db:
        def get(self, model, row_id):
            return job if model is BackgroundJob else None

    status = PipelineStatusDto(
        pipeline_run_id=59, upload_run_id=57, status="SECTOR_ROTATION_SNAPSHOT",
        current_step="SECTOR_ROTATION_SNAPSHOT", requested_by=None, started_at=datetime.now(UTC),
        completed_at=None, created_at=None, message=None, error_message=None,
        background_job_id=1882, steps=[],
    )
    result = _pipeline_status_payload(Db(), status)
    assert result["job_status"] == "RUNNING"
    assert result["recovery_reason_code"] == "WORKER_MEMORY_CRITICAL"
    assert "Sector Rotation Snapshot (Persistence Started)" in result["recovery_message"]
    assert "Observed 7,000 MiB; limit 6,144 MiB" in result["recovery_message"]
    assert "Recovery resumed from Sector Rotation Snapshot" in result["recovery_message"]
    assert result["recovery_memory_evidence"]["registered_pid"] == 14492
    assert "execution_token" not in result["recovery_memory_evidence"]
    assert "unapproved_nested" not in result["recovery_memory_evidence"]
    assert result["recovery_continuation"] == {
        "boundary": "SECTOR_ROTATION_SNAPSHOT", "validation_state": "VALIDATED",
    }


@pytest.mark.parametrize("status,expected", [
    ("QUEUED", "Automatic recovery will continue from Sector Rotation Snapshot."),
    ("STALLED", "Automatic recovery will continue from Sector Rotation Snapshot."),
    ("RECOVERING", "Automatic recovery will continue from Sector Rotation Snapshot."),
    ("BLOCKED", "Automatic recovery stopped; review the job error."),
])
def test_memory_recovery_message_distinguishes_pending_and_exhausted_recovery(status, expected):
    result = _pipeline_recovery_fields(_job(status))
    assert expected in result["recovery_message"]


def test_cancelled_job_never_claims_automatic_recovery_resumed():
    result = _pipeline_recovery_fields(_job("CANCELLED"))
    assert "The run was cancelled; automatic recovery stopped." in result["recovery_message"]
    assert "resumed" not in result["recovery_message"]
    assert "restarted" not in result["recovery_message"]


def test_running_unvalidated_checkpoint_is_still_being_validated():
    job = _job()
    job.operational_metadata_json["recovery_continuation"]["validation_state"] = "PENDING"
    result = _pipeline_recovery_fields(job)
    assert "validating the checkpoint for Sector Rotation Snapshot" in result["recovery_message"]
    assert "resumed" not in result["recovery_message"]


def test_legacy_memory_recovery_does_not_invent_missing_measurement():
    job = _job()
    job.operational_metadata_json["progress_recovery"].pop("memory_evidence")
    result = _pipeline_recovery_fields(job)
    assert result["recovery_memory_evidence"] is None
    assert "Observed" not in result["recovery_message"]
    assert result["recovery_reason"] == "Worker exceeded 6144 MB memory budget."
    assert _pipeline_recovery_fields(None)["recovery_message"] is None


def test_current_checkpoint_validation_state_supersedes_fence_planning_receipt():
    job = _job()
    job.operational_metadata_json["recovery_continuation"]["validation_state"] = "PENDING"
    published = {"recovery_continuation": {
        "boundary": "SECTOR_ROTATION_SNAPSHOT", "background_job_id": 1882,
        "validation_state": "INVALID",
    }}
    result = _pipeline_recovery_fields(job, pipeline_result=published)
    assert result["recovery_continuation"]["validation_state"] == "INVALID"
    assert "checkpoint validation failed" in result["recovery_message"]
    published["recovery_continuation"]["background_job_id"] = 999
    result = _pipeline_recovery_fields(job, pipeline_result=published)
    assert result["recovery_continuation"]["validation_state"] == "PENDING"


def test_terminal_publication_recovery_preserves_result_without_claiming_replay():
    job = _job("COMPLETED")
    job.operational_metadata_json["terminal_publication_recovery"] = {
        "version": "pipeline-terminal-publication-recovery-v1",
        "pipeline_status": "COMPLETED", "cause_code": "WORKER_MEMORY_CRITICAL",
        "reason": "Worker exceeded 6144 MB memory budget.",
    }
    result = _pipeline_recovery_fields(job)
    assert "finished result was preserved" in result["recovery_message"]
    assert "job record finalized" in result["recovery_message"]
    assert "resumed" not in result["recovery_message"]
    assert "restarted" not in result["recovery_message"]
    assert result["recovery_memory_evidence"] is None  # previous attempt's sample is historical
    assert result["recovery_continuation"] is None
    assert result["terminal_publication_recovery"]["pipeline_status"] == "COMPLETED"


def test_legacy_ranking_memory_evidence_ignores_stale_sector_phase():
    job = _job()
    evidence = job.operational_metadata_json["progress_recovery"]["memory_evidence"]
    evidence.update(stage="RANKING_PROFILES", subphase="BUILD:AAA",
                    sector_phase="PERSISTENCE_STARTED")
    result = _pipeline_recovery_fields(job)
    assert "Ranking Profiles (Build:Aaa)" in result["recovery_message"]
    assert "Persistence Started" not in result["recovery_message"]


def test_explicit_unknown_fenced_phase_does_not_reuse_old_sector_progress():
    job = _job()
    job.operational_metadata_json["progress_recovery"]["memory_evidence"].update(
        stage="SECTOR_ROTATION_SNAPSHOT", sector_phase=None, subphase=None,
    )
    job.operational_metadata_json["sector_progress"] = {"phase": "PERSISTENCE_STARTED"}
    result = _pipeline_recovery_fields(job)
    assert "during Sector Rotation Snapshot." in result["recovery_message"]
    assert "Persistence Started" not in result["recovery_message"]


def test_observed_continuation_does_not_inherit_an_unrelated_root_recovery(monkeypatch):
    from app.routers import run_routes

    root = _job()
    child = BackgroundJob(id=1883, job_type="FULL_PIPELINE", status="RUNNING", payload_json={},
        operational_metadata_json={"recovery_continuation": {
            "boundary": "CAPTURING_SETUP_SIGNALS", "validation_state": "PENDING",
        }})
    monkeypatch.setattr(run_routes, "_pipeline_observability_job", lambda *args: child)

    class Db:
        def get(self, model, row_id):
            return root if model is BackgroundJob else None

    status = PipelineStatusDto(
        pipeline_run_id=59, upload_run_id=57, status="CAPTURING_SETUP_SIGNALS",
        current_step="CAPTURING_SETUP_SIGNALS", requested_by=None, started_at=None,
        completed_at=None, created_at=None, message=None, error_message=None,
        background_job_id=1882, steps=[],
    )
    result = _pipeline_status_payload(Db(), status)
    assert result["observed_background_job_id"] == 1883
    assert result["recovery_reason_code"] is None
    assert result["recovery_continuation"]["boundary"] == "CAPTURING_SETUP_SIGNALS"


def test_recovery_cause_is_visible_in_initial_progress_page_while_running(monkeypatch):
    monkeypatch.setitem(templates.env.globals, "url_for", lambda _name, path: path)
    pipeline = {
        "pipeline_run_id": 59, "status": "SCORING_TECHNICALS", "job_status": "RUNNING",
        "current_step": "SCORING_TECHNICALS", "steps": [], "result": {},
        "percentage": 50, "completed_steps": 7, "total_steps": 14,
        **_pipeline_recovery_fields(_job()),
    }
    html = templates.get_template("pipeline_progress.html").render(
        run=UploadRun(id=57, filename="sample.csv", row_count=466), pipeline=pipeline,
        terminal_statuses=["COMPLETED", "PARTIAL", "FAILED", "BLOCKED"], status_url="/status",
    )
    notice = html.split("data-pipeline-worker-health", 1)[1].split("</div>", 1)[0]
    assert "hidden" not in notice
    assert "The worker exceeded its memory limit" in notice
    assert "Observed 7,000 MiB; limit 6,144 MiB" in notice
