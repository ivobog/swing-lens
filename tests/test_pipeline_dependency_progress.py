from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.services.ceri.sec.coverage import build_sec_coverage
from app.services.ceri.sec.readiness_diagnostics import (
    SecTickerReadiness,
    SecTickerReadinessCategory,
    SecUniverseReadiness,
)
from app.services.pipeline_dependency_service import (
    _project_terminal_sec_repair,
    _reconcile_dependency_job,
)


@pytest.mark.parametrize("status", ["BLOCKED", "FAILED", "CANCELLED", "COMPLETED"])
def test_terminal_child_reconciliation_replaces_stale_parent_progress(monkeypatch, status):
    pipeline = SimpleNamespace(
        id=51,
        status="WAITING_DEPENDENCY",
        result_json={
            "unrelated": "retained",
            "sec_repair": {
                "repair_stage": "QUEUED",
                "ready_tickers": 100,
                "total_tickers": 131,
                "telemetry": {},
            },
        },
    )
    dependency = SimpleNamespace(
        id=70,
        child_job_id=1762,
        pipeline_run_id=51,
        continuation_step="VALIDATING_RUN",
        result_json={"processor_signature": "test"},
    )
    readiness = {
        "requested_tickers": 131,
        "ready_tickers": 130,
        "counts": {"READY": 130, "UNRESOLVED_MAPPING": 1},
    }
    job = SimpleNamespace(
        id=1762,
        pipeline_dependency_id=70,
        status=status,
        error_message="TOWN unresolved",
        operational_metadata_json={},
        result_json={
            "reason_code": "SEC_IDENTITY_UNRESOLVED",
            "diagnostics": {
                "readiness": readiness,
                "unresolved_tickers": {"TOWN": "missing"},
                "telemetry": {"filing_downloads": 150},
            },
        },
    )
    if status == "COMPLETED":
        job.error_message = None
        job.result_json = {
            "readiness": {**readiness, "ready_tickers": 131, "counts": {"READY": 131}},
            "telemetry": {"filing_downloads": 150},
        }
    responses = iter((dependency, pipeline, SimpleNamespace(status="PENDING")))
    db = SimpleNamespace(scalar=lambda _statement: next(responses))
    continuations = []
    monkeypatch.setattr(
        "app.services.pipeline_dependency_service.pipeline_is_in_activation_scope", lambda _: True
    )
    monkeypatch.setattr(
        "app.services.pipeline_dependency_service._publish_dependency_latency", lambda *a, **k: None
    )

    def transition(_db, row, target, **kwargs):
        row.status = target
        row.completed_at = kwargs.get("completed_at")

    monkeypatch.setattr("app.services.pipeline_dependency_service.transition_pipeline", transition)
    monkeypatch.setattr(
        "app.services.pipeline_dependency_service.transition_pipeline_step", transition
    )
    monkeypatch.setattr(
        "app.services.pipeline_dependency_service._pipeline_step", lambda *a: SimpleNamespace()
    )
    monkeypatch.setattr(
        "app.services.pipeline_dependency_service._cancel_incomplete_steps", lambda *a: None
    )
    monkeypatch.setattr(
        "app.services.pipeline_dependency_service._enqueue_dependency_continuation",
        lambda *a: continuations.append(True),
    )
    monkeypatch.setattr(
        "app.services.pipeline_dependency_service._utcnow",
        lambda: datetime(2026, 11, 7, tzinfo=UTC),
    )

    _reconcile_dependency_job(db, job)

    repair = pipeline.result_json["sec_repair"]
    assert repair["repair_stage"] == status
    assert repair["ready_tickers"] == (131 if status == "COMPLETED" else 130)
    assert repair["total_tickers"] == 131
    assert repair["telemetry"]["filing_downloads"] == 150
    assert pipeline.result_json["dependency_state"] == dependency.state
    assert pipeline.result_json["unrelated"] == "retained"
    assert bool(continuations) == (status == "COMPLETED")
    if status in {"BLOCKED", "FAILED"}:
        assert pipeline.status == status
        assert pipeline.result_json["blocked_reason"] == "SEC_IDENTITY_UNRESOLVED"
        assert repair["unresolved_tickers"] == [{"ticker": "TOWN", "reason": "missing"}]


def test_successful_repair_projects_unresolved_source_gap_before_continuation():
    now = datetime(2026, 11, 9, tzinfo=UTC)
    pipeline = SimpleNamespace(id=56, scope_id="scope-56", result_json={"unrelated": "kept"})
    readiness = SecUniverseReadiness("sig", (
        SecTickerReadiness("GOOD", SecTickerReadinessCategory.READY, ciks=("0000000001",)),
        SecTickerReadiness("MISSING", SecTickerReadinessCategory.UNRESOLVED_MAPPING),
    ))
    coverage = build_sec_coverage(
        pipeline=pipeline,
        readiness=readiness,
        identity_results={"MISSING": {"status": "UNRESOLVED", "reason": "No exact SEC match."}},
        attempted_at=now,
    )
    dependency = SimpleNamespace(result_json={"processor_signature": "sig"})
    job = SimpleNamespace(
        status="COMPLETED", error_message=None, operational_metadata_json={},
        result_json={
            "readiness": readiness.as_dict(include_tickers=False),
            "sec_coverage": coverage,
            "unresolved_tickers": {"MISSING": "No exact SEC match."},
            "telemetry": {"filing_downloads": 1},
        },
    )
    _project_terminal_sec_repair(pipeline, dependency, job, now=now)
    assert pipeline.result_json["sec_coverage"] == coverage
    assert dependency.result_json["sec_coverage"] == coverage
    assert pipeline.result_json["sec_unresolved_count"] == 1
    assert pipeline.result_json["sec_repair"]["ready_tickers"] == 1
    assert pipeline.result_json["sec_repair"]["unresolved_tickers"] == [
        {"ticker": "MISSING", "reason": "No exact SEC match."}
    ]
    assert pipeline.result_json["blocked_reason"] is None
    assert pipeline.result_json["unrelated"] == "kept"


def test_late_failed_child_cannot_reblock_new_dependency(monkeypatch):
    pipeline = SimpleNamespace(
        id=56, status="WAITING_DEPENDENCY",
        result_json={"pipeline_dependency_id": 74, "sec_unresolved_count": 1},
    )
    old_dependency = SimpleNamespace(id=73, pipeline_run_id=56, child_job_id=1818)
    responses = iter((old_dependency, pipeline))
    db = SimpleNamespace(scalar=lambda _statement: next(responses))
    job = SimpleNamespace(id=1818, pipeline_dependency_id=73, status="BLOCKED")
    monkeypatch.setattr(
        "app.services.pipeline_dependency_service.pipeline_is_in_activation_scope", lambda _: True
    )
    _reconcile_dependency_job(db, job)
    assert pipeline.status == "WAITING_DEPENDENCY"
    assert pipeline.result_json == {"pipeline_dependency_id": 74, "sec_unresolved_count": 1}
