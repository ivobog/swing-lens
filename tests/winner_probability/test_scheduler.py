from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from app.services.winner_probability import scheduler


def test_scheduler_is_idempotent_per_completed_us_session(monkeypatch) -> None:
    db = FakeSchedulerDb()
    enqueued = []

    def fake_enqueue(_db, job_type, payload, **kwargs):
        job = SimpleNamespace(
            id=1,
            job_type=job_type,
            payload_json=payload,
            request_key=kwargs["request_key"],
        )
        enqueued.append(job)
        db.existing = job
        return job

    monkeypatch.setattr(scheduler, "enqueue_job", fake_enqueue)
    now = datetime(2026, 8, 14, 2, 34, 21, tzinfo=UTC)

    first = scheduler.schedule_primary_h5_maturation(db, now=now)
    second = scheduler.schedule_primary_h5_maturation(db, now=now)

    assert first is second
    assert len(enqueued) == 1
    assert first.request_key == "winner:h5-next-open:session:2026-08-13"
    assert first.payload_json["entry_model"] == "NEXT_OPEN"
    assert first.payload_json["horizon_sessions"] == 5
    assert first.payload_json["due_session"] == "2026-08-13"


class FakeSchedulerDb:
    existing = None

    def scalar(self, _statement):
        return self.existing
