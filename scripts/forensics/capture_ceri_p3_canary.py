"""Capture concise durable evidence for the P3 CERI production canary."""

from __future__ import annotations

import argparse
import json
import math
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.models.ceri_tables import CeriFeatureSourceManifest
from app.models.tables import BackgroundJob, PipelineRun


def _seconds(started: datetime | None, completed: datetime | None) -> float | None:
    if started is None or completed is None:
        return None
    return (completed - started).total_seconds()


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def _invocation_percentiles(invocations: list[dict[str, object]]) -> dict[str, object]:
    owners: dict[str, object] = {}
    for owner in sorted({str(item["owner"]) for item in invocations}):
        rows = [item for item in invocations if item["owner"] == owner]
        fingerprint = [float(item["fingerprint_ms"]) for item in rows]
        manifest = [float(item["manifest_ms"]) for item in rows]
        owners[owner] = {
            "calls": len(rows),
            "canonical_bytes": sum(int(item["canonical_bytes"]) for item in rows),
            "fingerprint_wall_ms": {
                "min": min(fingerprint),
                "median": _percentile(fingerprint, 0.5),
                "p90": _percentile(fingerprint, 0.9),
                "p95": _percentile(fingerprint, 0.95),
                "max": max(fingerprint),
            },
            "manifest_wall_ms": {
                "min": min(manifest),
                "median": _percentile(manifest, 0.5),
                "p90": _percentile(manifest, 0.9),
                "p95": _percentile(manifest, 0.95),
                "max": max(manifest),
            },
        }
    return owners


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pipeline-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    with SessionLocal() as db:
        pipeline = db.get(PipelineRun, args.pipeline_id)
        if pipeline is None:
            raise ValueError("pipeline not found")
        jobs = list(
            db.scalars(
                select(BackgroundJob)
                .where(BackgroundJob.related_run_id == pipeline.upload_run_id)
                .order_by(BackgroundJob.id)
            )
        )
        feature_jobs = [job for job in jobs if job.job_type == "CERI_FEATURE_BATCH"]
        finalizers = [job for job in jobs if job.job_type == "CERI_RUN_FINALIZE"]
        alerts = [job for job in jobs if job.job_type == "CERI_ALERT_REBUILD"]
        if len(feature_jobs) != 2 or not finalizers or not alerts:
            raise ValueError("CERI canary graph is incomplete")

        job_rows = [
            {
                "id": job.id,
                "job_type": job.job_type,
                "status": job.status,
                "retry_count": job.retry_count,
                "started_at": job.started_at,
                "completed_at": job.completed_at,
                "duration_seconds": _seconds(job.started_at, job.completed_at),
            }
            for job in jobs
        ]
        feature_rows = []
        for job in feature_jobs:
            telemetry = dict((job.result_json or {}).get("telemetry") or {})
            source = dict(telemetry.get("source_integrity_telemetry") or {})
            invocations = list(source.pop("writer_invocations", []) or [])
            source.pop("writer_top_invocations", None)
            feature_rows.append(
                {
                    "job_id": job.id,
                    "duration_seconds": _seconds(job.started_at, job.completed_at),
                    "ticker_count": telemetry.get("ticker_count"),
                    "batch_total_ms": telemetry.get("batch_total_ms"),
                    "batch_cpu_ms": telemetry.get("batch_cpu_ms"),
                    "persistence_ms": telemetry.get("persistence_ms"),
                    "feature_compute_ms": sum(
                        float(value)
                        for value in (telemetry.get("family_runtime_ms") or {}).values()
                    ),
                    "source_integrity_telemetry": source,
                    "writer_owner_percentiles": _invocation_percentiles(invocations),
                }
            )

        manifests = list(
            db.scalars(
                select(CeriFeatureSourceManifest)
                .where(CeriFeatureSourceManifest.run_id == pipeline.upload_run_id)
                .order_by(CeriFeatureSourceManifest.batch_index)
            )
        )
        first_ceri_job = next(
            job for job in jobs if job.job_type == "CERI_PROVIDER_INGEST_BATCH"
        )
        report = {
            "captured_at": datetime.now(UTC),
            "pipeline_id": pipeline.id,
            "upload_run_id": pipeline.upload_run_id,
            "pipeline_status": pipeline.status,
            "pipeline_current_step": pipeline.current_step,
            "ceri_completion_state": (pipeline.result_json or {}).get(
                "ceri_completion_state"
            ),
            "certified_capture_count": (pipeline.result_json or {}).get(
                "ceri_certified_capture_count"
            ),
            "ceri_total_seconds": _seconds(first_ceri_job.started_at, alerts[-1].completed_at),
            "provider_normalize_seconds": _seconds(
                first_ceri_job.started_at, feature_jobs[0].started_at
            ),
            "feature_wall_seconds": _seconds(
                feature_jobs[0].started_at, feature_jobs[-1].completed_at
            ),
            "post_feature_seconds": _seconds(
                finalizers[0].started_at, alerts[-1].completed_at
            ),
            "feature_jobs": feature_rows,
            "source_manifests": [
                {
                    "id": manifest.id,
                    "background_job_id": manifest.background_job_id,
                    "batch_index": manifest.batch_index,
                    "source_count": manifest.source_count,
                    "bundle_fingerprint": manifest.bundle_fingerprint,
                    "calculation_context_id": manifest.calculation_context_id,
                }
                for manifest in manifests
            ],
            "jobs": job_rows,
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
