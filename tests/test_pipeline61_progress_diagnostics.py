import logging
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.services.job_progress_diagnostics import JobProgressDiagnostics
from app.settings import Settings


def test_stalled_live_handler_has_bounded_diagnostic_without_local_values(monkeypatch, caplog):
    now = datetime(2026, 11, 12, tzinfo=UTC)
    row = {
        "progress_stage": "FREEZING_DECISION_HANDOFF_MANIFEST",
        "progress_sequence": 2,
        "progress_current_item": "evidence:sector",
        "progress_processed": 1,
        "progress_total": 159,
        "last_progress_at": now - timedelta(seconds=1400),
    }
    original = dict(row)
    monkeypatch.setattr(
        "app.services.job_progress_diagnostics.process_memory_snapshot",
        lambda: SimpleNamespace(rss_bytes=100, private_bytes=80),
    )
    diagnostic = JobProgressDiagnostics(
        job_id=2067, handler_thread_id=threading.get_ident(), settings=Settings(),
    )
    with caplog.at_level(logging.WARNING):
        assert diagnostic.observe(row, now=now)
        assert not diagnostic.observe(row, now=now + timedelta(seconds=100))
    record = caplog.records[-1]
    payload = record.progress_diagnostic
    assert record.job_id == 2067
    assert 1 <= len(payload["stack_leaf_first"]) <= 32
    assert all(set(frame) == {"file", "function", "line"} for frame in payload["stack_leaf_first"])
    assert payload["private_bytes"] == 80
    assert row == original


def test_healthy_or_new_attempt_does_not_inherit_old_progress_age(caplog):
    now = datetime(2026, 11, 12, tzinfo=UTC)
    diagnostic = JobProgressDiagnostics(
        job_id=2067, handler_thread_id=threading.get_ident(), settings=Settings(),
    )
    assert not diagnostic.observe({
        "progress_stage": "FREEZING_DECISION_HANDOFF_MANIFEST",
        "last_progress_at": None,
        "started_at": now - timedelta(hours=2),
        "operational_metadata_json": {"current_attempt": {"started_at": now.isoformat()}},
    }, now=now)
    assert not diagnostic.observe({"progress_stage": "FREEZING_DECISION_HANDOFF_MANIFEST"}, now=now)
    assert not caplog.records


def test_advanced_progress_can_produce_new_evidence_after_another_stall(monkeypatch):
    now = datetime(2026, 11, 12, tzinfo=UTC)
    monkeypatch.setattr(
        "app.services.job_progress_diagnostics.process_memory_snapshot",
        lambda: SimpleNamespace(rss_bytes=100, private_bytes=None),
    )
    diagnostic = JobProgressDiagnostics(
        job_id=2067, handler_thread_id=-1, settings=Settings(),
    )
    row = {"progress_stage": "FREEZING_DECISION_HANDOFF_MANIFEST", "progress_sequence": 1,
           "last_progress_at": now - timedelta(seconds=1400)}
    assert diagnostic.observe(row, now=now)
    row.update(progress_sequence=2, last_progress_at=now)
    assert not diagnostic.observe(row, now=now)
    assert diagnostic.observe(row, now=now + timedelta(seconds=1400))
