"""Benchmark the production CERI retained-manifest retry path, rollback-only.

The command reconstructs a completed feature job through ``prepare_batch`` (the same
production retry loader), validates its immutable manifest, profiles the authority
operations, and rolls the transaction back. It never invokes a feature writer or
changes job state.
"""

from __future__ import annotations

import argparse
import cProfile
import io
import json
import os
import pstats
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path
from time import perf_counter, process_time

import psutil
from sqlalchemy import inspect, text

from app.db import SessionLocal
from app.models import ceri_tables as _ceri_tables  # noqa: F401 - registers ORM models
from app.models.tables import BackgroundJob
from app.services.ceri.artifact_lineage import CeriArtifactOwnership
from app.services.ceri.feature_rebuild_service import (
    CeriFeatureRebuildRequest,
    CeriFeatureRebuildService,
)
from app.services.ceri.source_manifest_service import load_feature_source_manifest
from app.services.scope_refresh_adoption import require_semantic_authority


def _measure(callback):
    process = psutil.Process()
    rss_before = process.memory_info().rss
    wall_started = perf_counter()
    cpu_started = process_time()
    callback()
    wall_seconds = perf_counter() - wall_started
    cpu_seconds = process_time() - cpu_started
    return {
        "wall_seconds": wall_seconds,
        "cpu_seconds": cpu_seconds,
        "cpu_to_wall_ratio": cpu_seconds / wall_seconds if wall_seconds else 0.0,
        "rss_before_bytes": rss_before,
        "rss_after_bytes": process.memory_info().rss,
    }


def _parse_date(value: object) -> date | None:
    return date.fromisoformat(str(value)) if value else None


def _parse_datetime(value: object) -> datetime | None:
    return datetime.fromisoformat(str(value)) if value else None


def _different_value(value: object) -> object:
    if isinstance(value, bool):
        return not value
    if isinstance(value, datetime):
        return value + timedelta(microseconds=1)
    if isinstance(value, date):
        return value + timedelta(days=1)
    if isinstance(value, str):
        return value + "__authority_probe__"
    if isinstance(value, int):
        return value + 1
    if isinstance(value, float):
        return value + 1.0
    return None


def _dirty_retained_row(bundle) -> tuple[object, str, object]:
    for row in bundle._rows.values():
        mapper = inspect(type(row))
        primary_keys = {column.key for column in mapper.primary_key}
        for column in mapper.columns:
            if column.key in primary_keys:
                continue
            original = getattr(row, column.key)
            changed = _different_value(original)
            if changed is not None and changed != original:
                setattr(row, column.key, changed)
                return row, column.key, original
    raise ValueError("no retained scalar source field is available for the dirty-row probe")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--skip-cprofile",
        action="store_true",
        help="Measure clean phase timings without deterministic-profiler overhead.",
    )
    args = parser.parse_args()
    profiler = cProfile.Profile()
    payload: dict[str, object] = {
        "job_id": args.job_id,
        "process_id": os.getpid(),
        "logical_cpu_count": os.cpu_count(),
        "git_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, encoding="utf-8"
        ).strip(),
    }
    with SessionLocal() as db:
        try:
            job = db.get(BackgroundJob, args.job_id)
            if job is None or job.job_type != "CERI_FEATURE_BATCH":
                raise ValueError("job must be a retained CERI_FEATURE_BATCH")
            manifest = load_feature_source_manifest(db, job.id)
            if manifest is None:
                raise ValueError("feature source manifest is missing")
            payload_json = job.payload_json or {}
            bundle_box: dict[str, object] = {}

            def prepare():
                bundle_box["context"] = CeriFeatureRebuildService().prepare_batch(
                    db,
                    CeriFeatureRebuildRequest(
                        tickers=tuple(payload_json["tickers"]),
                        run_id=int(payload_json.get("run_id") or job.related_run_id),
                        mode="AS_KNOWN",
                        as_of_session=_parse_date(payload_json.get("as_of_session")),
                        cutoff_at=_parse_datetime(payload_json.get("cutoff_at")),
                        calculation_context_id=int(payload_json["calculation_context_id"]),
                        calendar_version=str(payload_json["calendar_version"]),
                        ownership_mode=CeriArtifactOwnership.PIPELINE.value,
                        semantic_authority=require_semantic_authority(job),
                        source_manifest_json=manifest.manifest_json,
                    ),
                )

            if not args.skip_cprofile:
                profiler.enable()
            payload["retained_retry_load"] = _measure(prepare)
            context = bundle_box["context"]
            bundle = context.source_bodies
            if bundle is None:
                raise ValueError("production retry did not construct a source bundle")
            payload["source_count"] = len(bundle.bodies)
            payload["rows_loaded"] = context.rows_loaded
            payload["manifest_fingerprint"] = manifest.manifest_json["bundle_fingerprint"]
            payload["reconstructed_fingerprint"] = bundle.body_fingerprint
            if payload["source_count"] != int(manifest.manifest_json["source_count"]):
                raise ValueError("production retry source count does not match retained manifest")
            if payload["reconstructed_fingerprint"] != payload["manifest_fingerprint"]:
                raise ValueError("production retry fingerprint does not match retained manifest")
            table_counts: dict[str, int] = {}
            for entry in manifest.manifest_json.get("entries") or ():
                table = str(entry["table"])
                table_counts[table] = table_counts.get(table, 0) + 1
            payload["manifest_table_counts"] = dict(sorted(table_counts.items()))
            payload["clean_assert_once"] = _measure(bundle.assert_unchanged_in_memory)
            payload["noop_refresh_once"] = _measure(bundle.refresh)
            payload["full_boundary_audit_once"] = _measure(bundle.full_audit_unchanged_in_memory)

            def force_refresh():
                bundle._requires_revalidation = True
                bundle.refresh()

            payload["forced_refresh_once"] = _measure(force_refresh)
            payload["durable_manifest_once"] = _measure(bundle.durable_manifest)

            dirty_probe: dict[str, object] = {}

            def assert_dirty_row_is_rejected():
                row, field, original = _dirty_retained_row(bundle)
                dirty_probe.update(table=inspect(type(row)).local_table.name, field=field)
                try:
                    bundle.assert_unchanged_in_memory()
                except ValueError as exc:
                    dirty_probe["detected"] = str(exc).startswith(
                        "MUTATION_SOURCE_BUNDLE_CHANGED_IN_MEMORY:"
                    )
                    if not dirty_probe["detected"]:
                        raise
                else:
                    raise AssertionError("dirty retained source row escaped authority validation")
                finally:
                    setattr(row, field, original)

            payload["dirty_row_assert_once"] = _measure(assert_dirty_row_is_rejected)
            payload["dirty_row_probe"] = dirty_probe
            if not args.skip_cprofile:
                profiler.disable()
            payload["source_authority_telemetry"] = bundle.telemetry_snapshot()
            payload["postgres_version"] = db.execute(text("select version() ")).scalar_one()
        finally:
            db.rollback()

    if args.skip_cprofile:
        payload["profile_top_cumulative"] = "not collected (--skip-cprofile)"
    else:
        stream = io.StringIO()
        pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats("cumulative").print_stats(60)
        payload["profile_top_cumulative"] = stream.getvalue()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
