from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from app.services.ceri import batched_workflow
from app.services.ceri.batched_workflow import CERI_FEATURE_BATCH, CERI_PROVIDER_INGEST_BATCH
from app.services.ceri.sec.coverage import build_sec_coverage
from app.services.ceri.sec.readiness_diagnostics import (
    SecTickerReadiness,
    SecTickerReadinessCategory,
    SecUniverseReadiness,
)
from app.services.pipeline_outcome import derive_pipeline_outcome
from app.services.pipeline_prerequisites import CeriBootstrapRequiredError
from app.settings import Settings
from app.templates import templates

SIGNATURE = "sec-guidance:test"


def _case():
    readiness = SecUniverseReadiness(
        processor_signature=SIGNATURE,
        tickers=(
            SecTickerReadiness("GOOD", SecTickerReadinessCategory.READY, ciks=("0000000001",)),
            SecTickerReadiness("MISS", SecTickerReadinessCategory.UNRESOLVED_MAPPING),
        ),
    )
    pipeline = SimpleNamespace(id=56, scope_id="retained-scope", result_json={})
    coverage = build_sec_coverage(
        pipeline=pipeline,
        readiness=readiness,
        identity_results={"MISS": {"status": "UNRESOLVED", "reason": "No exact SEC match."}},
        attempted_at=datetime(2026, 11, 9, tzinfo=UTC),
    )
    pipeline.result_json = {"sec_coverage": coverage}
    return pipeline, readiness, coverage


def _scheduler(monkeypatch, pipeline, readiness):
    db = Session()
    cutoff = SimpleNamespace(
        context_id=7,
        cutoff_at=datetime(2026, 11, 9, tzinfo=UTC),
        latest_completed_session=date(2026, 11, 6),
        calendar_version="test-calendar",
    )
    monkeypatch.setattr(db, "scalar", lambda _statement: SimpleNamespace(id=7, pipeline_run_id=56))
    monkeypatch.setattr(db, "get", lambda *_args: pipeline)
    monkeypatch.setattr(
        db, "scalars", lambda _statement: [SimpleNamespace(ticker=t) for t in ("GOOD", "MISS")]
    )
    monkeypatch.setattr(db, "flush", lambda: None)
    monkeypatch.setattr(batched_workflow, "resolve_pipeline_market_context", lambda *a, **k: cutoff)
    monkeypatch.setattr(batched_workflow, "_ensure_ceri_companies", lambda *a: None)
    monkeypatch.setattr(
        batched_workflow,
        "get_settings",
        lambda: Settings(
            _env_file=None,
            sec_document_incremental_mode="ACTIVE",
            sec_readiness_policy="REQUIRE_READY",
        ),
    )
    monkeypatch.setattr(
        batched_workflow,
        "CeriProviderRegistry",
        lambda: SimpleNamespace(
            capabilities=lambda name: SimpleNamespace(
                datasets=batched_workflow.DEFAULT_PROVIDER_DATASETS[name]
            )
        ),
    )
    monkeypatch.setattr(
        batched_workflow,
        "require_deployed_processor_active",
        lambda _: SimpleNamespace(
            active_signature=SIGNATURE, deployed_signature=SIGNATURE, as_dict=lambda: {}
        ),
    )
    monkeypatch.setattr(batched_workflow, "diagnose_sec_readiness", lambda *a, **k: readiness)
    monkeypatch.setattr(
        batched_workflow, "load_ceri_config", lambda: SimpleNamespace(config_hash="test-config")
    )
    monkeypatch.setattr("app.services.source_mutation_authority._source_value", lambda *a: None)
    monkeypatch.setattr(
        "app.services.scope_refresh_adoption.require_semantic_authority",
        lambda _: SimpleNamespace(as_dict=lambda: {}),
    )
    monkeypatch.setattr(
        "app.services.scope_refresh_adoption.bind_semantic_authority", lambda *a, **k: None
    )
    queued = []

    def enqueue(_db, kind, payload, **kwargs):
        queued.append((kind, payload))
        return SimpleNamespace()

    monkeypatch.setattr(batched_workflow, "enqueue_job", enqueue)
    return db, cutoff, queued


def test_retained_unresolved_sec_identity_only_changes_sec_acquisition_scope(monkeypatch):
    pipeline, readiness, _coverage = _case()
    db, cutoff, queued = _scheduler(monkeypatch, pipeline, readiness)

    plan = batched_workflow.schedule_ceri_batched_workflow(db, 55, market_cutoff=cutoff)

    sec_jobs = [
        payload
        for kind, payload in queued
        if kind == CERI_PROVIDER_INGEST_BATCH and payload["provider"] == "sec"
    ]
    other_jobs = [
        payload
        for kind, payload in queued
        if kind == CERI_PROVIDER_INGEST_BATCH and payload["provider"] == "eodhd"
    ]
    feature_jobs = [payload for kind, payload in queued if kind == CERI_FEATURE_BATCH]
    assert sec_jobs and all(payload["tickers"] == ["GOOD"] for payload in sec_jobs)
    assert other_jobs and all(payload["tickers"] == ["GOOD", "MISS"] for payload in other_jobs)
    assert feature_jobs and feature_jobs[0]["tickers"] == ["GOOD", "MISS"]
    assert plan.run_id == 55
    assert readiness.complete is False
    db.close()


def test_other_pipeline_coverage_cannot_bypass_batched_preflight(monkeypatch):
    pipeline, readiness, _coverage = _case()
    pipeline.id = 57
    db, cutoff, queued = _scheduler(monkeypatch, pipeline, readiness)

    with pytest.raises(CeriBootstrapRequiredError):
        batched_workflow.schedule_ceri_batched_workflow(db, 55, market_cutoff=cutoff)

    assert not queued
    db.close()


def test_retained_identity_gap_does_not_hide_later_bootstrap_failure(monkeypatch):
    pipeline, readiness, _coverage = _case()
    readiness = SecUniverseReadiness(
        SIGNATURE,
        (
            readiness.tickers[0],
            SecTickerReadiness(
                "MISS", SecTickerReadinessCategory.SYNC_STATE_MISSING, ciks=("0000000002",)
            ),
        ),
    )
    db, cutoff, queued = _scheduler(monkeypatch, pipeline, readiness)

    with pytest.raises(CeriBootstrapRequiredError):
        batched_workflow.schedule_ceri_batched_workflow(db, 55, market_cutoff=cutoff)

    assert not queued
    db.close()


def test_readiness_coverage_reports_active_gap_without_counting_it_ready(monkeypatch):
    pipeline, readiness, coverage = _case()
    monkeypatch.setattr(batched_workflow, "diagnose_sec_readiness", lambda *a, **k: readiness)

    result = batched_workflow.sec_readiness_coverage(
        SimpleNamespace(),
        tickers=["GOOD", "MISS"],
        processor_signature=SIGNATURE,
        pipeline=pipeline,
    )

    assert result.complete is False
    assert result.ready_tickers == 1
    assert result.missing_tickers == ("MISS",)
    assert result.sec_unresolved_count == 1
    assert result.sec_coverage == coverage


@pytest.mark.parametrize("status", ["COMPLETED", "PARTIAL"])
def test_final_outcome_keeps_sec_gap_without_changing_execution_status(status):
    _pipeline, _readiness, coverage = _case()
    result = derive_pipeline_outcome(
        status,
        {
            "uploaded_rows": 2,
            "combined_results": 2,
            "sec_coverage": coverage,
        },
    )

    assert result["pipeline_status"] == status
    assert result["coverage"]["gaps"]["sec_unresolved_count"] == 1
    assert result["coverage"]["sources"]["sec"]["gaps"][0]["ticker"] == "MISS"
    assert result["coverage"]["excluded_tickers"] == []
    assert result["integrity"]["status"] == "NO_INTEGRITY_HAZARD"


def test_resolved_later_identity_does_not_count_retained_history_as_current_gap():
    _pipeline, _readiness, coverage = _case()
    outcome = derive_pipeline_outcome(
        "COMPLETED",
        {
            "sec_coverage": coverage,
            "sec_unresolved_count": 0,
        },
    )
    assert "sec_unresolved_count" not in outcome["coverage"]["gaps"]
    assert outcome["coverage"]["sources"]["sec"] == coverage


def test_completed_pipeline_keeps_unresolved_sec_source_visible(monkeypatch):
    _pipeline, _readiness, coverage = _case()
    monkeypatch.setitem(templates.env.globals, "url_for", lambda _name, path: path)
    html = templates.get_template("pipeline_progress.html").render(
        run=SimpleNamespace(id=55, filename="run.csv"),
        pipeline={
            "pipeline_run_id": 56,
            "status": "COMPLETED",
            "steps": [],
            "completed_steps": 13,
            "total_steps": 13,
            "percentage": 100,
            "result": {"sec_coverage": coverage},
        },
        terminal_statuses=["COMPLETED"],
        status_url="/runs/55/pipeline/56/status",
    )
    assert "data-pipeline-sec-coverage >" in html
    assert "MISS: SEC identity unresolved." in html
    assert "SEC guidance remains unavailable" in html


def test_resolved_gap_does_not_remain_in_live_progress(monkeypatch):
    _pipeline, _readiness, coverage = _case()
    monkeypatch.setitem(templates.env.globals, "url_for", lambda _name, path: path)
    html = templates.get_template("pipeline_progress.html").render(
        run=SimpleNamespace(id=55, filename="run.csv"),
        pipeline={
            "pipeline_run_id": 56,
            "status": "COMPLETED",
            "steps": [],
            "completed_steps": 13,
            "total_steps": 13,
            "percentage": 100,
            "result": {"sec_coverage": coverage, "sec_unresolved_tickers": []},
        },
        terminal_statuses=["COMPLETED"],
        status_url="/runs/55/pipeline/56/status",
    )
    assert "data-pipeline-sec-coverage hidden" in html
    assert "MISS: SEC identity unresolved." not in html
