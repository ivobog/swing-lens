from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from types import SimpleNamespace

from app.services.background_job_service import JobStatus
from app.services.winner_probability import scheduler


def test_scheduler_is_idempotent_per_completed_us_session(monkeypatch) -> None:
    db = FakeSchedulerDb()
    enqueued = []

    def fake_enqueue(_db, *, payload, **kwargs):
        job = SimpleNamespace(
            id=1,
            job_type="WINNER_OUTCOME_MATURATION",
            payload_json=payload,
            request_key=kwargs["request_key"],
        )
        enqueued.append(job)
        return job

    monkeypatch.setattr(scheduler, "enqueue_outcome_maturation_workflow", fake_enqueue)
    now = datetime(2026, 8, 14, 2, 34, 21, tzinfo=UTC)

    first = scheduler.schedule_primary_h5_maturation(db, now=now)
    second = scheduler.schedule_primary_h5_maturation(db, now=now)

    assert first is second
    assert len(enqueued) == 1
    assert first.request_key == "winner:h5-next-open:session:2026-08-13"
    assert first.payload_json["entry_model"] == "NEXT_OPEN"
    assert first.payload_json["horizon_sessions"] == 5
    assert first.payload_json["due_session"] == "2026-08-13"


def test_completed_session_idle_polls_create_no_audit_evidence_and_next_day_schedules(
    monkeypatch,
) -> None:
    completed = SimpleNamespace(id=7, request_key="winner:h5-next-open:session:2026-08-13")
    db = FakeSchedulerDb()
    completed.status = JobStatus.COMPLETED
    completed.result_json = {}
    db.lineage = [completed]
    roots: list[tuple[str, str]] = []
    enqueued: list[object] = []

    @contextmanager
    def root_scope(kind: str, name: str):
        roots.append((kind, name))
        yield

    def fake_enqueue(_db, *, payload, **kwargs):
        job = SimpleNamespace(id=8, payload_json=payload, request_key=kwargs["request_key"])
        enqueued.append(job)
        return job

    monkeypatch.setattr(scheduler, "root_action_scope", root_scope)
    monkeypatch.setattr(scheduler, "enqueue_outcome_maturation_workflow", fake_enqueue)
    today = datetime(2026, 8, 14, 2, 34, 21, tzinfo=UTC)

    for _ in range(5_000):
        assert scheduler.schedule_primary_h5_maturation(db, now=today) is completed

    assert roots == []
    assert enqueued == []

    db.lineage = []
    tomorrow = datetime(2026, 8, 15, 2, 34, 21, tzinfo=UTC)
    scheduled = scheduler.schedule_primary_h5_maturation(db, now=tomorrow)
    assert scheduled.request_key == "winner:h5-next-open:session:2026-08-14"
    assert len(roots) == 1
    assert len(enqueued) == 1


def test_waiting_lineage_uses_deterministic_readiness_key_without_duplicate_enqueue(
    monkeypatch,
) -> None:
    now = datetime(2026, 8, 14, 2, 34, 21, tzinfo=UTC)
    base_key = "winner:h5-next-open:session:2026-08-13"
    waiting = SimpleNamespace(
        id=7,
        status=JobStatus.PARTIAL,
        request_key=base_key,
        result_json={"continuation_reason": "WAITING_FOR_ACQUISITION"},
    )
    db = FakeSchedulerDb()
    db.lineage = [waiting]
    enqueued = []

    class ReadyService:
        def maturation_ready_outcome_ids(self, *_args, **_kwargs):
            return (11, 12)

    def fake_enqueue(_db, *, payload, **kwargs):
        job = SimpleNamespace(
            id=8,
            status=JobStatus.QUEUED,
            payload_json=payload,
            request_key=kwargs["request_key"],
            result_json={},
        )
        enqueued.append(job)
        return job

    monkeypatch.setattr(scheduler, "H5NextOpenOrchestrationService", ReadyService)
    monkeypatch.setattr(scheduler, "enqueue_outcome_maturation_workflow", fake_enqueue)

    scheduled = scheduler.schedule_primary_h5_maturation(db, now=now)

    fingerprint = sha256(b"11,12").hexdigest()[:24]
    assert scheduled.request_key == f"{base_key}:ready:{fingerprint}"
    assert len(enqueued) == 1

    restarted = FakeSchedulerDb()
    restarted.lineage = [scheduled, waiting]
    repeated = scheduler.schedule_primary_h5_maturation(restarted, now=now)
    assert repeated is scheduled
    assert len(enqueued) == 1


class FakeSchedulerDb:
    active = None
    lineage = ()

    def scalar(self, _statement):
        return self.active

    def scalars(self, _statement):
        return self.lineage
