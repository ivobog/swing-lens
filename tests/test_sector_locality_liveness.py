from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import psutil
import pytest
from test_worker_supervisor_reliability import FakeProcess, _settings, _worker

from app import worker_supervisor as supervisor
from app.models.tables import BackgroundJob
from app.routers.run_routes import _pipeline_status_payload
from app.services import pipeline_executor, process_identity, worker_registry
from app.services.core_mutation_authority import sector_evidence_visit_order
from app.services.pipeline_service import PipelineStatusDto


@pytest.mark.parametrize("state", ["ALIVE", "UNKNOWN", "DEAD"])
def test_delayed_registration_uses_identity_and_bounded_confirmation(monkeypatch, state):
    worker = _worker()
    worker.heartbeat_at = worker.control_loop_heartbeat_at = datetime.now(UTC) - timedelta(
        seconds=40
    )
    supervisor._liveness_suspicions.clear()
    events = []
    receipts = []
    clock = [0.0]
    monkeypatch.setattr(supervisor, "monotonic", lambda: clock[0])
    monkeypatch.setattr(supervisor, "get_settings", _settings)
    monkeypatch.setattr(supervisor, "_registered_worker", lambda _: worker)
    monkeypatch.setattr(supervisor, "_registered_worker_process_alive", lambda _: False)
    monkeypatch.setattr(supervisor, "inspect_process_identity", lambda *_: {"state": state})
    monkeypatch.setattr(supervisor, "_registered_worker_job_execution_alive", lambda _: True)
    monkeypatch.setattr(supervisor, "_safe_memory_observation", lambda _: ("OK", {}))
    monkeypatch.setattr(supervisor, "_fence_no_progress", lambda *_, **__: [])
    monkeypatch.setattr(supervisor, "append_lifecycle_event", lambda *_, **kw: receipts.append(kw))
    monkeypatch.setattr(
        supervisor, "_retire_worker_registration", lambda _: events.append("retire")
    )
    monkeypatch.setattr(supervisor, "_requeue", lambda *_, **__: events.append("requeue"))
    monkeypatch.setattr(supervisor, "_start_worker", lambda *_: FakeProcess(900, None))

    def fence(*_, **kwargs):
        events.append("fence")
        assert kwargs["liveness_evidence"]["os_process"]["state"] == state
        assert kwargs["liveness_evidence"]["registered_pid"] == worker.process_id
        assert kwargs["liveness_evidence"]["decision"]
        return supervisor.WorkerJobReconciliation(fenced_job_ids=(1882,))

    monkeypatch.setattr(supervisor, "_fence_worker", fence)
    supervisor._supervise_once(worker_id="worker-a", queues="interactive", child=None)
    if state == "ALIVE":
        assert events == []
        assert receipts[-1]["reason_code"] == "REGISTRATION_DELAYED_ACTIVE_EXECUTION"
    elif state == "UNKNOWN":
        assert events == []
        assert receipts[-1]["reason_code"] == "LIVENESS_CONFIRMATION_PENDING"
        clock[0] = 31.0
        supervisor._supervise_once(worker_id="worker-a", queues="interactive", child=None)
        assert events == ["fence", "retire", "requeue"]
        assert receipts[-1]["reason_code"] == "WORKER_INSPECTION_UNAVAILABLE"
    else:
        assert events == ["fence", "retire", "requeue"]
        assert receipts[-1]["reason_code"] == "WORKER_PROCESS_EXITED"


def test_liveness_confirmation_is_bound_to_exact_generation(monkeypatch):
    supervisor._liveness_suspicions.clear()
    monkeypatch.setattr(supervisor, "get_settings", _settings)
    clock = [0.0]
    monkeypatch.setattr(supervisor, "monotonic", lambda: clock[0])
    first = _worker(instance_id="old")
    assert supervisor._liveness_confirmation_pending(first, unknown=True)
    clock[0] = 31
    assert not supervisor._liveness_confirmation_pending(first, unknown=True)
    assert supervisor._liveness_confirmation_pending(_worker(instance_id="new"), unknown=True)


def test_fresh_leased_execution_survives_a_delayed_telemetry_collector(monkeypatch):
    worker = _worker()
    monkeypatch.setattr(supervisor, "get_settings", _settings)
    monkeypatch.setattr(supervisor, "_registered_worker", lambda _: worker)
    monkeypatch.setattr(supervisor, "_registered_worker_process_alive", lambda _: True)
    monkeypatch.setattr(supervisor, "_registered_worker_control_loop_alive", lambda _: False)
    monkeypatch.setattr(supervisor, "_registered_worker_resource_sampler_alive", lambda _: False)
    monkeypatch.setattr(supervisor, "_registered_worker_job_execution_alive", lambda _: True)
    monkeypatch.setattr(supervisor, "_safe_memory_observation", lambda _: ("OK", {}))
    monkeypatch.setattr(supervisor, "_fence_no_progress", lambda *_, **__: [])
    receipts = []
    monkeypatch.setattr(supervisor, "append_lifecycle_event", lambda *_, **kw: receipts.append(kw))
    monkeypatch.setattr(supervisor, "_fence_worker", lambda *_, **__: pytest.fail("healthy owner"))
    supervisor._supervise_once(worker_id="worker-a", queues="interactive", child=None)
    assert receipts[-1]["reason_code"] == "CONTROL_DELAYED_ACTIVE_EXECUTION"


@pytest.mark.parametrize(
    "failure,expected",
    [
        (psutil.NoSuchProcess(99), "DEAD"),
        (psutil.AccessDenied(99), "UNKNOWN"),
    ],
)
def test_os_inspection_distinguishes_death_from_unavailable(monkeypatch, failure, expected):
    monkeypatch.setattr(psutil, "Process", lambda _: (_ for _ in ()).throw(failure))
    assert process_identity.inspect_process_identity(99, datetime.now(UTC))["state"] == expected


def test_os_inspection_rejects_reused_pid(monkeypatch):
    now = datetime.now(UTC)
    monkeypatch.setattr(
        psutil,
        "Process",
        lambda _: SimpleNamespace(
            create_time=lambda: now.timestamp(),
            status=lambda: psutil.STATUS_RUNNING,
        ),
    )
    old = now - timedelta(hours=1)
    assert process_identity.inspect_process_identity(99, old)["reason"] == "PID_REUSED"
    assert process_identity.inspect_process_identity(99, now)["state"] == "ALIVE"


def test_os_inspection_matches_the_current_real_process():
    import os

    current = process_identity.process_started_at()
    assert process_identity.inspect_process_identity(os.getpid(), current)["state"] == "ALIVE"


def test_schema_capability_reflection_runs_once_per_engine_and_head(monkeypatch):
    class Engine:
        pass

    engine = Engine()
    heads = ["0095"]
    columns = [{"name": "telemetry_status"}]
    calls = []
    connection = SimpleNamespace(engine=engine, dialect=SimpleNamespace(name="postgresql"))
    db = SimpleNamespace(connection=lambda: connection, scalars=lambda _: tuple(heads))
    monkeypatch.setattr(
        worker_registry,
        "sa_inspect",
        lambda conn: SimpleNamespace(
            has_table=lambda _: True,
            get_columns=lambda _: calls.append(conn) or list(columns),
        ),
    )
    for _ in range(20):
        assert worker_registry._telemetry_columns_available(db)
    assert len(calls) == 1
    heads[:] = ["0096"]
    columns.clear()
    assert not worker_registry._telemetry_columns_available(db)
    assert len(calls) == 2
    connection.engine = Engine()
    assert not worker_registry._telemetry_columns_available(db)
    assert len(calls) == 3


def test_partial_store_without_marker_does_not_issue_an_invalid_head_query(monkeypatch):
    class Engine:
        pass

    connection = SimpleNamespace(engine=Engine(), dialect=SimpleNamespace(name="postgresql"))
    db = SimpleNamespace(
        connection=lambda: connection,
        scalars=lambda _: pytest.fail("No SELECT against a missing migration table"),
    )
    monkeypatch.setattr(
        worker_registry,
        "sa_inspect",
        lambda _: SimpleNamespace(
            has_table=lambda _: False,
            get_columns=lambda _: [{"name": "telemetry_status"}],
        ),
    )
    assert worker_registry._telemetry_columns_available(db)


def test_sector_visit_plan_retains_duplicate_roles_and_missing_addresses():
    addresses = [41, 3, 41, None, 19]
    order = sector_evidence_visit_order(addresses)
    assert [addresses[index] for index in order] == [None, 3, 19, 41, 41]
    assert sorted(order) == list(range(len(addresses)))
    assert addresses == [41, 3, 41, None, 19]


def test_phase_history_is_bounded_and_distinguishes_operation_counts(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(pipeline_executor, "perf_counter", lambda: clock[0])
    observed = []
    checkpoint = pipeline_executor._sector_checkpoint_callback(
        object(),
        should_cancel=lambda: False,
        progress_callback=lambda *_, **kw: observed.append(kw),
    )
    for index in range(40):
        clock[0] = float(index)
        checkpoint(
            phase=f"PHASE_{index}",
            processed=index,
            total=50,
            metrics={"evidence_body_rows_loaded": index * 32},
        )
    sector = observed[-1]["operational_metadata_patch"]["sector_progress"]
    assert sector["processed"] == 39 and sector["total"] == 50
    assert len(sector["phase_history"]) == 32
    assert sector["phase_history"][-1]["elapsed_ms"] == 1000
    assert sector["phase_history"][-1]["phase"] == "PHASE_38"


@pytest.mark.parametrize("stage", ["SECTOR_ROTATION_SNAPSHOT", "RANKING_PROFILES"])
def test_status_shows_current_operation_count_instead_of_previous_pass(stage):
    job = BackgroundJob(
        id=1882,
        job_type="FULL_PIPELINE",
        status="RUNNING",
        progress_stage=stage,
        progress_processed=3728,
        progress_total=3728,
        operational_metadata_json={
            "sector_progress": {
                "phase": "PERSISTENCE_VALIDATION_SOURCE_ROLES",
                "processed": 75,
                "total": 2332,
                "phase_elapsed_ms": 3000,
            },
        },
    )
    db = SimpleNamespace(get=lambda model, _: job if model is BackgroundJob else None)
    status = PipelineStatusDto(
        pipeline_run_id=59,
        upload_run_id=57,
        status=stage,
        current_step=stage,
        requested_by=None,
        started_at=None,
        completed_at=None,
        created_at=None,
        message=None,
        error_message=None,
        background_job_id=1882,
        steps=[],
    )
    data = _pipeline_status_payload(db, status)
    if stage == "SECTOR_ROTATION_SNAPSHOT":
        assert (data["processed_item_count"], data["total_item_count"]) == (75, 2332)
        assert data["sector_progress"]["label"] == "Checking sources before publication"
    else:
        assert data["processed_item_count"] == 3728
        assert not data["sector_progress"]


def test_current_liveness_recovery_does_not_reuse_historical_memory_reason():
    from test_pipeline_recovery_visibility import _job

    from app.routers.run_routes import _pipeline_recovery_fields

    job = _job()
    job.operational_metadata_json["last_worker_liveness"] = {
        "decision": "WORKER_HEARTBEAT_STALE",
        "worker_heartbeat_age_seconds": 40,
        "fenced_at": "2026-11-10T21:54:54Z",
    }
    job.operational_metadata_json["recovery_continuation"]["cause_code"] = "WORKER_PROCESS_LOST"
    job.operational_metadata_json["recovery_continuation"]["fenced_at"] = "2026-11-10T21:54:54Z"
    data = _pipeline_recovery_fields(job)
    assert data["recovery_reason_code"] == "WORKER_HEARTBEAT_STALE"
    assert "heartbeats stopped" in data["recovery_message"]
    assert "memory" not in data["recovery_message"]
    assert data["recovery_memory_evidence"] is None


def test_hash_operation_exposes_actual_bytes_instead_of_an_unchanging_fraction():
    job = BackgroundJob(
        id=1882,
        job_type="FULL_PIPELINE",
        status="RUNNING",
        progress_stage="SECTOR_ROTATION_SNAPSHOT",
        operational_metadata_json={
            "sector_progress": {
                "phase": "PERSISTENCE_CANONICAL_HASH",
                "processed": 0,
                "total": 1,
                "validation_metrics": {
                    "PERSISTENCE_CANONICAL_HASH": {
                        "canonical_bytes_hashed": 64 * 1024**2,
                    }
                },
            },
        },
    )
    db = SimpleNamespace(get=lambda model, _: job if model is BackgroundJob else None)
    status = PipelineStatusDto(
        pipeline_run_id=59,
        upload_run_id=57,
        status="SECTOR_ROTATION_SNAPSHOT",
        current_step="SECTOR_ROTATION_SNAPSHOT",
        requested_by=None,
        started_at=None,
        completed_at=None,
        created_at=None,
        message=None,
        error_message=None,
        background_job_id=1882,
        steps=[],
    )
    result = _pipeline_status_payload(db, status)
    assert result["sector_progress"]["progress_label"] == "64.0 MiB processed"
