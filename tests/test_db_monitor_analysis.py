from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from app.observability.db_monitor_analysis import analyze_logs, render_text


def test_analysis_reports_fingerprints_routes_jobs_n_plus_one_and_writes(
    tmp_path: Path,
) -> None:
    timestamp = datetime.now(UTC).isoformat()
    fingerprint = "select-fingerprint"
    records = []
    for _ in range(3):
        records.append(
            {
                "record_type": "sql",
                "timestamp": timestamp,
                "duration_ms": 12.5,
                "operation": "SELECT",
                "query_fingerprint": fingerprint,
                "normalized_sql": "SELECT value FROM sample WHERE id = ?",
                "success": True,
                "origin_type": "HTTP",
                "request_id": "request-1",
                "http_method": "GET",
                "http_path": "/ceri",
                "route_path": "/ceri",
                "python_caller": {
                    "source_file": "app/services/ceri/query_service.py",
                    "line_number": 122,
                    "function": "CeriQueryService.current_scores",
                },
            }
        )
    records.extend(
        [
            {
                "record_type": "sql",
                "timestamp": timestamp,
                "duration_ms": 7,
                "operation": "UPDATE",
                "query_fingerprint": "update-fingerprint",
                "normalized_sql": "UPDATE sample SET value = ? WHERE id = ?",
                "success": True,
                "origin_type": "BACKGROUND_JOB",
                "job_id": 91,
                "job_type": "CERI_FEATURE_BATCH",
                "run_id": 108,
                "python_caller": {
                    "source_file": "app/services/ceri/feature_rebuild_service.py",
                    "line_number": 381,
                    "function": "CeriFeatureRebuildService._existing_feature",
                },
            },
            {
                "record_type": "request_summary",
                "timestamp": timestamp,
                "request_id": "request-1",
                "http_method": "GET",
                "http_path": "/ceri",
                "route_path": "/ceri",
                "total_duration_ms": 100,
                "total_sql_ms": 37.5,
                "sql_query_count": 3,
            },
            {
                "record_type": "job_summary",
                "timestamp": timestamp,
                "job_id": 91,
                "job_type": "CERI_FEATURE_BATCH",
                "run_id": 108,
                "total_duration_ms": 80,
                "total_sql_ms": 7,
                "sql_query_count": 1,
                "maximum_sql_ms": 7,
            },
        ]
    )
    path = tmp_path / "sql-2026-08-15-p1.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")

    report = analyze_logs(
        tmp_path,
        limit=10,
        min_average_calls=2,
        n_plus_one_threshold=3,
        fingerprint_detail=fingerprint,
    )

    assert report["most_expensive_fingerprints"][0]["calls"] == 3
    assert report["worst_gui_routes"][0]["route"] == "GET /ceri"
    assert report["worst_background_jobs"][0]["job_type"] == "CERI_FEATURE_BATCH"
    assert report["n_plus_one_candidates"][0]["calls_in_request"] == 3
    assert report["write_heavy_areas"][0]["UPDATE"] == 1
    assert report["query_detail"]["primary_origins"][0][1] == 3
    assert "SQL Flight Recorder" in render_text(report)


def test_analysis_skips_incomplete_jsonl_tail(tmp_path: Path) -> None:
    path = tmp_path / "sql-2026-08-15-p1.jsonl"
    path.write_text(
        json.dumps(
            {
                "record_type": "sql",
                "timestamp": datetime.now(UTC).isoformat(),
                "duration_ms": 1,
                "operation": "SELECT",
                "query_fingerprint": "ok",
                "normalized_sql": "SELECT ?",
            }
        )
        + "\n{incomplete",
        encoding="utf-8",
    )

    report = analyze_logs(tmp_path)

    assert report["record_counts"] == {"sql": 1}
