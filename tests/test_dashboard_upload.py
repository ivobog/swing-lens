from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import UploadFile

from app.models.tables import UploadRun
from app.routers.upload_routes import _dashboard_summary, _next_action
from app.services.upload_service import (
    UploadProcessingError,
    _safe_filename,
    _validate_upload_size,
    create_upload_run,
)
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


def test_validate_upload_size_wraps_non_seekable_stream() -> None:
    upload = UploadFile(filename="stream.csv", file=NonSeekableBytesIO(b"Symbol\nMSFT\n"))

    with pytest.raises(UploadProcessingError, match="could not be inspected"):
        _validate_upload_size(upload, max_size_mb=1)


def test_safe_filename_strips_path_caps_length_and_avoids_reserved_names() -> None:
    long_name = "a" * 260 + ".csv"

    assert _safe_filename("..\\..\\evil.csv") == "evil.csv"
    assert _safe_filename("CON.csv") == "upload_CON.csv"
    assert _safe_filename(long_name).endswith(".csv")
    assert len(_safe_filename(long_name)) == 180


def test_create_upload_run_cleans_saved_file_when_db_flush_fails(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.services.upload_service.get_settings",
        lambda: SimpleNamespace(max_upload_size_mb=1, upload_dir=tmp_path),
    )
    upload = UploadFile(
        filename="sample.csv",
        file=BytesIO(b"Symbol,Description\nMSFT,Microsoft\n"),
    )

    with pytest.raises(RuntimeError, match="flush failed"):
        create_upload_run(FlushFailDb(), upload)

    assert list(tmp_path.iterdir()) == []


def test_create_upload_run_retains_artifact_for_committed_failed_parse(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.services.upload_service.get_settings",
        lambda: SimpleNamespace(max_upload_size_mb=1, upload_dir=tmp_path),
    )
    db = UploadRunFakeDb()
    upload = UploadFile(
        filename="bad.csv",
        file=BytesIO(b"Symbol,Symbol\nMSFT,AAPL\n"),
    )

    run = create_upload_run(db, upload)

    assert run.status == "FAILED"
    assert "duplicate column" in run.error_message
    assert db.committed is True
    assert len(list(tmp_path.iterdir())) == 1


def test_create_upload_run_fails_duplicate_tickers_before_scoring(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.services.upload_service.get_settings",
        lambda: SimpleNamespace(max_upload_size_mb=1, upload_dir=tmp_path),
    )
    db = UploadRunFakeDb()
    upload = UploadFile(
        filename="duplicates.csv",
        file=BytesIO(b"Symbol,Description\nmsft,Microsoft A\nMSFT,Microsoft B\n"),
    )

    run = create_upload_run(db, upload)

    assert run.status == "FAILED"
    assert "duplicate ticker 'MSFT'" in run.error_message
    assert db.added_models == []


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
    assert "<h2>Local App</h2>" not in html
    assert "<h2>IB Gateway</h2>" not in html


def _run(status: str) -> UploadRun:
    return UploadRun(id=1, filename="sample.csv", status=status, row_count=1)


class NonSeekableBytesIO(BytesIO):
    def seek(self, *_args, **_kwargs):
        raise OSError("not seekable")

    def tell(self):
        raise OSError("not seekable")


class FlushFailDb:
    def add(self, _model):
        return None

    def flush(self):
        raise RuntimeError("flush failed")


class UploadRunFakeDb:
    def __init__(self) -> None:
        self.committed = False
        self.added_models = []

    def add(self, model):
        model.id = 1

    def flush(self):
        return None

    def commit(self):
        self.committed = True

    def refresh(self, _model):
        return None

    def add_all(self, models):
        self.added_models.extend(models)
