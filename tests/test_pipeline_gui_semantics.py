from pathlib import Path
from types import SimpleNamespace

from app.models.tables import BackgroundJob, PipelineRun
from app.services.pipeline_service import _ceri_async_visibility
from app.templates import templates


class AsyncVisibilityDb:
    def __init__(self, jobs):
        self.jobs = jobs

    def scalars(self, _statement):
        return self.jobs

    def scalar(self, _statement):
        return None


def test_ceri_async_progress_does_not_mix_unknown_totals_into_denominator() -> None:
    jobs = [
        BackgroundJob(
            id=1,
            job_type="CERI_NORMALIZE_BATCH",
            workflow_key="ceri:pipeline:30:test",
            status="COMPLETED",
            progress_processed=20,
            progress_total=20,
        ),
        BackgroundJob(
            id=2,
            job_type="CERI_NORMALIZE_BATCH",
            workflow_key="ceri:pipeline:30:test",
            status="COMPLETED",
            progress_processed=9,
            progress_total=None,
        ),
    ]
    pipeline = PipelineRun(
        id=30,
        upload_run_id=30,
        status="PARTIAL",
        current_step="CERI_PROVIDER_INGEST",
        result_json={"ceri_provider_workflow_key": "ceri:pipeline:30:test"},
    )

    payload = _ceri_async_visibility(  # noqa: SLF001
        AsyncVisibilityDb(jobs),  # type: ignore[arg-type]
        pipeline,
    )

    phase = payload["phases"]["normalization"]
    assert phase["processed"] == 20
    assert phase["total"] == 20
    assert phase["unknown_total_processed"] == 9
    assert phase["unknown_total_jobs"] == 1


def test_terminal_pipeline_labels_last_job_stage(monkeypatch) -> None:
    monkeypatch.setitem(templates.env.globals, "url_for", lambda _name, path: path)
    html = templates.get_template("pipeline_progress.html").render(
        run=SimpleNamespace(id=30, filename="run.csv"),
        pipeline={
            "pipeline_run_id": 30,
            "status": "PARTIAL",
            "result": {},
            "steps": [],
            "completed_steps": 12,
            "total_steps": 13,
            "percentage": 92.3,
        },
        terminal_statuses=["COMPLETED", "PARTIAL", "FAILED", "BLOCKED", "CANCELLED"],
        status_url="/runs/30/pipeline/30/status",
    )

    assert "Last job stage" in html
    assert ">Active job stage<" not in html


def test_global_hidden_rule_is_authoritative() -> None:
    css = Path("app/static/app.css").read_text(encoding="utf-8")

    assert "[hidden]" in css
    assert "display: none !important" in css


def test_market_data_block_offers_recovery_and_explicit_partial_scope(monkeypatch):
    monkeypatch.setitem(templates.env.globals, "url_for", lambda _name, path: path)
    html = templates.get_template("pipeline_progress.html").render(
        run=SimpleNamespace(id=51, filename="run.csv"),
        pipeline={
            "pipeline_run_id": 53, "status": "BLOCKED", "current_step": "FETCHING_MARKET_DATA",
            "result": {"market_data_session_readiness": {
                "technical_tickers": ["MSFT"], "blockers": ["ROKU"],
            }},
            "steps": [], "completed_steps": 2, "total_steps": 13, "percentage": 15.4,
        },
        terminal_statuses=["COMPLETED", "PARTIAL", "FAILED", "BLOCKED", "CANCELLED"],
        status_url="/runs/51/pipeline/53/status",
    )
    assert "Retry missing market data" in html
    assert 'action="/runs/51/pipeline/53/partial"' in html
    assert 'name="excluded_tickers" data-pipeline-partial-tickers value="ROKU"' in html
    assert "Start partial run — exclude ROKU" in html
    assert "The original pipeline will stay blocked" in html


def test_partial_pipeline_progress_discloses_original_denominator(monkeypatch):
    monkeypatch.setitem(templates.env.globals, "url_for", lambda _name, path: path)
    html = templates.get_template("pipeline_progress.html").render(
        run=SimpleNamespace(id=52, filename="partial.csv"),
        pipeline={
            "pipeline_run_id": 54, "status": "PARTIAL",
            "result": {"partial_scope": {"source_pipeline_id": 53,
                "included_tickers": ["MSFT"], "requested_tickers": ["MSFT", "ROKU"],
                "excluded_tickers": ["ROKU"]}},
            "steps": [], "completed_steps": 13, "total_steps": 13, "percentage": 100,
        },
        terminal_statuses=["COMPLETED", "PARTIAL", "FAILED", "BLOCKED", "CANCELLED"],
        status_url="/runs/52/pipeline/54/status",
    )
    assert 'data-pipeline-partial-included>1</span>' in html
    assert 'data-pipeline-partial-requested>2</span>' in html
    assert 'data-pipeline-partial-excluded>ROKU</span>' in html
    assert "incomplete for the original universe" in html
