from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.routers import run_routes
from app.routers.export_responses import attachment_response
from app.services.cleanup_service import cleanup_rebuildable_artifacts
from app.settings import Settings


def test_attachment_response_streams_and_refuses_oversized_exports() -> None:
    response = attachment_response(
        "ticker\nMSFT\n",
        media_type="text/csv",
        filename="small.csv",
        max_bytes=100,
    )

    assert isinstance(response, StreamingResponse)
    assert response.body == b"ticker\nMSFT\n"

    with pytest.raises(HTTPException) as exc:
        attachment_response(
            "ticker\nMSFT\n",
            media_type="text/csv",
            filename="too_large.csv",
            max_bytes=5,
        )

    assert exc.value.status_code == 413
    assert exc.value.detail["code"] == "BYTE_LIMIT_EXCEEDED"


def test_failed_fetch_export_refuses_over_row_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        run_routes,
        "get_settings",
        lambda: SimpleNamespace(max_export_rows=1),
    )
    monkeypatch.setattr(
        run_routes,
        "run_ib_fetch_progress",
        lambda run_id, fetch_run_id, db: {
            "items": [
                {
                    "ticker": "MSFT",
                    "what_to_show": "TRADES",
                    "status": "FAILED",
                    "error_message": "No contract",
                },
                {
                    "ticker": "AAPL",
                    "what_to_show": "TRADES",
                    "status": "FAILED",
                    "error_message": "No contract",
                },
            ]
        },
    )

    with pytest.raises(HTTPException) as exc:
        run_routes.export_failed_fetch_items(
            run_id=7,
            fetch_run_id=11,
            db=SimpleNamespace(),
        )

    assert exc.value.status_code == 413
    assert exc.value.detail["code"] == "EXPORT_ROW_LIMIT_EXCEEDED"


def test_cleanup_dry_run_and_execute_only_rebuildable_artifacts(tmp_path) -> None:
    now = datetime(2026, 8, 3, tzinfo=UTC)
    export_dir = tmp_path / "exports"
    cache_dir = tmp_path / "cache"
    upload_dir = tmp_path / "uploads"
    export_dir.mkdir()
    cache_dir.mkdir()
    upload_dir.mkdir()

    old_export = _old_file(export_dir / "old.csv", now=now)
    fresh_export = _fresh_file(export_dir / "fresh.csv", now=now)
    old_cache = _old_file(cache_dir / "ohlcv.parquet", now=now)
    orphan_upload = _old_file(upload_dir / "orphan.csv", now=now)
    referenced_upload = _old_file(upload_dir / "referenced.csv", now=now)

    old_job = SimpleNamespace(
        id=10,
        status="COMPLETED",
        completed_at=now - timedelta(days=120),
    )
    running_job = SimpleNamespace(
        id=11,
        status="RUNNING",
        completed_at=None,
    )
    db = _CleanupFakeDb(
        upload_runs=[SimpleNamespace(file_path=str(referenced_upload))],
        background_jobs=[old_job, running_job],
    )
    settings = Settings(
        _env_file=None,
        export_dir=export_dir,
        cache_dir=cache_dir,
        upload_dir=upload_dir,
        cleanup_export_retention_days=30,
        cleanup_cache_retention_days=30,
        cleanup_orphan_upload_grace_days=7,
        cleanup_job_retention_days=90,
    )

    preview = cleanup_rebuildable_artifacts(db, settings, dry_run=True, now=now)

    assert {candidate.kind for candidate in preview.candidates} == {
        "export_file",
        "cache_file",
        "orphan_upload_file",
        "terminal_background_job",
    }
    assert old_export.exists()
    assert old_cache.exists()
    assert orphan_upload.exists()
    assert referenced_upload.exists()

    executed = cleanup_rebuildable_artifacts(db, settings, dry_run=False, now=now)

    assert not old_export.exists()
    assert fresh_export.exists()
    assert not old_cache.exists()
    assert not orphan_upload.exists()
    assert referenced_upload.exists()
    assert db.deleted == [old_job]
    assert len(executed.errors) == 0


def _old_file(path, *, now: datetime):
    path.write_text("data", encoding="utf-8")
    old_timestamp = (now - timedelta(days=45)).timestamp()
    os.utime(path, (old_timestamp, old_timestamp))
    return path


def _fresh_file(path, *, now: datetime):
    path.write_text("data", encoding="utf-8")
    fresh_timestamp = (now - timedelta(days=1)).timestamp()
    os.utime(path, (fresh_timestamp, fresh_timestamp))
    return path


class _CleanupFakeDb:
    def __init__(self, *, upload_runs, background_jobs) -> None:
        self.upload_runs = upload_runs
        self.background_jobs = background_jobs
        self.deleted = []
        self.flushes = 0

    def delete(self, item) -> None:
        self.deleted.append(item)

    def flush(self) -> None:
        self.flushes += 1
