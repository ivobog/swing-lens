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
