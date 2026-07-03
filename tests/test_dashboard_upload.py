from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import UploadFile

from app.models.tables import UploadRun
from app.routers.upload_routes import _dashboard_summary, _next_action
from app.services.upload_service import UploadProcessingError, _validate_upload_size
from app.templates import templates


def test_validate_upload_size_rejects_large_file_and_resets_pointer() -> None:
    upload = UploadFile(filename="big.csv", file=BytesIO(b"x" * 11))

    with pytest.raises(UploadProcessingError, match="too large"):
        _validate_upload_size(upload, max_size_mb=0)

    assert upload.file.tell() == 0


def test_validate_upload_size_allows_file_within_limit_and_resets_pointer() -> None:
    upload = UploadFile(filename="small.csv", file=BytesIO(b"x" * 10))

    _validate_upload_size(upload, max_size_mb=1)

    assert upload.file.tell() == 0


def test_next_action_guides_latest_run_state() -> None:
    assert _next_action(_run("FAILED"), combined_count=0, ready_count=0).startswith("Review")
    assert _next_action(_run("COMPLETED"), combined_count=0, ready_count=0).startswith("Fetch")
    assert _next_action(_run("COMPLETED"), combined_count=0, ready_count=1).startswith("Refresh")
    assert _next_action(_run("COMPLETED"), combined_count=1, ready_count=1).startswith("Review")


def test_dashboard_summary_handles_empty_state() -> None:
    summary = _dashboard_summary(db=SimpleNamespace(), latest_run=None)

    assert summary["latest_run_id"] is None
    assert summary["latest_status"] == "No runs"
    assert summary["next_action"] == "Upload a daily screener CSV."


def test_upload_template_handles_missing_dashboard_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        templates.env.globals,
        "url_for",
        lambda _name, path: path,
    )
    template = templates.get_template("upload.html")

    html = template.render(
        settings=SimpleNamespace(
            max_upload_size_mb=20,
            app_host="127.0.0.1",
            app_port=8000,
            upload_dir="data/uploads",
            export_dir="data/exports",
            ib_host="127.0.0.1",
            ib_port=4002,
            ib_client_id=21,
        ),
        ib_status="Not tested",
        latest_run=None,
        recent_runs=[],
        error=None,
    )

    assert "Upload a daily screener CSV." in html
    assert "No runs yet." in html


def _run(status: str) -> UploadRun:
    return UploadRun(id=1, filename="sample.csv", status=status, row_count=1)
