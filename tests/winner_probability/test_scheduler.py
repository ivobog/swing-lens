from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

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
        db.existing = job
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
    db.existing = completed
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

    db.existing = None
    tomorrow = datetime(2026, 8, 15, 2, 34, 21, tzinfo=UTC)
    scheduled = scheduler.schedule_primary_h5_maturation(db, now=tomorrow)
    assert scheduled.request_key == "winner:h5-next-open:session:2026-08-14"
    assert len(roots) == 1
    assert len(enqueued) == 1


class FakeSchedulerDb:
    existing = None

    def scalar(self, _statement):
        return self.existing
