"""Rollback-only CERI P2 source-overlap and PostgreSQL contention diagnostics.

The script treats retained jobs/manifests as immutable evidence.  ``overlap``
compares their exact manifest entries.  ``contention`` reconstructs each batch
through the production expected-manifest loader in separate processes, holds the
first transaction open, observes PostgreSQL while the second loads, and rolls
both transactions back.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import subprocess
import traceback
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from time import monotonic, perf_counter, sleep

import psutil
from sqlalchemy import select, text

from app.db import SessionLocal
from app.models.tables import BackgroundJob, PriceBar
from app.services.ceri.artifact_lineage import CeriArtifactOwnership
from app.services.ceri.feature_rebuild_service import (
    CeriFeatureRebuildRequest,
    CeriFeatureRebuildService,
)
from app.services.ceri.source_manifest_service import load_feature_source_manifest
from app.services.scope_refresh_adoption import require_semantic_authority


def _parse_date(value: object) -> date | None:
    return date.fromisoformat(str(value)) if value else None


def _parse_datetime(value: object) -> datetime | None:
    return datetime.fromisoformat(str(value)) if value else None


def _request(db, job_id: int) -> tuple[BackgroundJob, object, CeriFeatureRebuildRequest]:
    job = db.get(BackgroundJob, job_id)
    if job is None or job.job_type != "CERI_FEATURE_BATCH":
        raise ValueError(f"job {job_id} is not a retained CERI_FEATURE_BATCH")
    manifest = load_feature_source_manifest(db, job.id)
    if manifest is None:
        raise ValueError(f"job {job_id} has no retained source manifest")
    payload = dict(job.payload_json or {})
    request = CeriFeatureRebuildRequest(
        tickers=tuple(str(value) for value in payload["tickers"]),
        run_id=int(payload.get("run_id") or job.related_run_id),
        mode="AS_KNOWN",
        as_of_session=_parse_date(payload.get("as_of_session")),
        cutoff_at=_parse_datetime(payload.get("cutoff_at")),
        calculation_context_id=int(payload["calculation_context_id"]),
        calendar_version=str(payload["calendar_version"]),
        ownership_mode=CeriArtifactOwnership.PIPELINE.value,
        semantic_authority=require_semantic_authority(job),
        source_manifest_json=manifest.manifest_json,
    )
    return job, manifest, request


def _base_report(command: str, job_ids: tuple[int, int]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "command": command,
        "job_ids": list(job_ids),
        "git_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, encoding="utf-8"
        ).strip(),
        "rollback_only": True,
        "immutable_run_ids": [11, 13, 14],
    }


def overlap(job_ids: tuple[int, int]) -> dict[str, object]:
    report = _base_report("overlap", job_ids)
    with SessionLocal() as db:
        manifests = []
        for job_id in job_ids:
            job, manifest, _request_value = _request(db, job_id)
            entries = {
                (str(item["table"]), tuple(item["address"])): str(item["row_fingerprint"])
                for item in manifest.manifest_json["entries"]
            }
            manifests.append((job, manifest, entries))
        left, right = manifests
        shared_keys = sorted(set(left[2]) & set(right[2]), key=str)
        mismatches = [key for key in shared_keys if left[2][key] != right[2][key]]
        shared_price_ids = [int(key[1][0]) for key in shared_keys if key[0] == "price_bars"]
        price_rows = {
            row.id: row
            for row in db.scalars(
                select(PriceBar).where(PriceBar.id.in_(shared_price_ids)).order_by(PriceBar.id)
            )
        }
        report.update(
            {
                "jobs": [
                    {
                        "job_id": item[0].id,
                        "run_id": item[0].related_run_id,
                        "status": item[0].status,
                        "manifest_id": item[1].id,
                        "source_count": item[1].source_count,
                        "table_counts": dict(
                            sorted(Counter(key[0] for key in item[2]).items())
                        ),
                    }
                    for item in manifests
                ],
                "shared_identity_count": len(shared_keys),
                "shared_table_counts": dict(
                    sorted(Counter(key[0] for key in shared_keys).items())
                ),
                "left_overlap_percent": 100 * len(shared_keys) / len(left[2]),
                "right_overlap_percent": 100 * len(shared_keys) / len(right[2]),
                "fingerprint_mismatch_count": len(mismatches),
                "fingerprint_mismatches": [
                    {
                        "table": key[0],
                        "address": list(key[1]),
                        "left": left[2][key],
                        "right": right[2][key],
                    }
                    for key in mismatches
                ],
                "shared_entries": [
                    {
                        "table": key[0],
                        "address": list(key[1]),
                        "row_fingerprint": left[2][key],
                        **(
                            {
                                "ticker": price_rows[int(key[1][0])].ticker,
                                "bar_date": price_rows[int(key[1][0])].bar_date.isoformat(),
                                "timeframe": price_rows[int(key[1][0])].timeframe,
                                "what_to_show": price_rows[int(key[1][0])].what_to_show,
                            }
                            if key[0] == "price_bars" and int(key[1][0]) in price_rows
                            else {}
                        ),
                    }
                    for key in shared_keys
                ],
            }
        )
        db.rollback()
    return report


def _loader(job_id: int, label: str, ready, release, messages) -> None:
    process = psutil.Process()
    with SessionLocal() as db:
        try:
            db.execute(
                text("SELECT set_config('application_name', :name, true)"),
                {"name": label},
            )
            backend_pid = int(db.scalar(text("SELECT pg_backend_pid()")))
            messages.put(
                {
                    "event": "started",
                    "label": label,
                    "job_id": job_id,
                    "os_pid": os.getpid(),
                    "backend_pid": backend_pid,
                    "rss_bytes": process.memory_info().rss,
                }
            )
            _job, manifest, request = _request(db, job_id)
            started = perf_counter()
            context = CeriFeatureRebuildService().prepare_batch(db, request)
            elapsed = perf_counter() - started
            bundle = context.source_bodies
            if bundle is None or bundle.body_fingerprint != manifest.bundle_fingerprint:
                raise ValueError("retained source fingerprint mismatch")
            messages.put(
                {
                    "event": "prepared",
                    "label": label,
                    "job_id": job_id,
                    "backend_pid": backend_pid,
                    "wall_seconds": elapsed,
                    "rss_bytes": process.memory_info().rss,
                    "source_count": manifest.source_count,
                    "bundle_fingerprint": bundle.body_fingerprint,
                }
            )
            ready.set()
            release.wait(180)
        except BaseException as exc:
            messages.put(
                {
                    "event": "error",
                    "label": label,
                    "job_id": job_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }
            )
            ready.set()
        finally:
            db.rollback()
            messages.put({"event": "rolled_back", "label": label, "job_id": job_id})


def _activity_snapshot(pids: list[int]) -> dict[str, object]:
    with SessionLocal() as db:
        activity = db.execute(
            text(
                """
                SELECT pid, application_name, state, wait_event_type, wait_event,
                       pg_blocking_pids(pid) AS blocking_pids,
                       EXTRACT(EPOCH FROM (clock_timestamp() - xact_start)) AS xact_age_seconds,
                       EXTRACT(EPOCH FROM (clock_timestamp() - query_start)) AS query_age_seconds,
                       left(query, 1000) AS query
                FROM pg_stat_activity
                WHERE pid = ANY(:pids)
                ORDER BY pid
                """
            ),
            {"pids": pids},
        ).mappings().all()
        locks = db.execute(
            text(
                """
                SELECT l.pid, l.locktype, l.mode, l.granted,
                       l.relation::regclass::text AS relation,
                       l.page, l.tuple, l.transactionid, l.virtualxid
                FROM pg_locks l
                WHERE l.pid = ANY(:pids)
                ORDER BY l.pid, l.granted, l.locktype, l.mode, relation, l.page, l.tuple
                """
            ),
            {"pids": pids},
        ).mappings().all()
        result = {
            "captured_at": db.scalar(text("SELECT clock_timestamp()")),
            "activity": [dict(row) for row in activity],
            "locks": [dict(row) for row in locks],
        }
        db.rollback()
        return result


def contention(job_ids: tuple[int, int], timeout_seconds: float) -> dict[str, object]:
    report = _base_report("contention", job_ids)
    context = mp.get_context("spawn")
    messages = context.Queue()
    ready_a, ready_b, release = context.Event(), context.Event(), context.Event()
    process_a = context.Process(
        target=_loader,
        args=(job_ids[0], "ceri-p2-holder-a", ready_a, release, messages),
        name="ceri-p2-holder-a",
    )
    process_b = context.Process(
        target=_loader,
        args=(job_ids[1], "ceri-p2-contender-b", ready_b, release, messages),
        name="ceri-p2-contender-b",
    )
    events: list[dict[str, object]] = []
    snapshots: list[dict[str, object]] = []
    process_a.start()
    if not ready_a.wait(timeout_seconds):
        release.set()
        process_a.join(10)
        raise TimeoutError("first retained batch did not prepare before timeout")
    while not messages.empty():
        events.append(messages.get())
    if any(item["event"] == "error" for item in events):
        release.set()
        process_a.join(10)
        raise RuntimeError("first retained batch failed")
    process_b.start()
    deadline = monotonic() + timeout_seconds
    blocker_observed = False
    backend_pids: dict[str, int] = {
        str(item["label"]): int(item["backend_pid"])
        for item in events
        if item["event"] == "started"
    }
    try:
        while monotonic() < deadline:
            while not messages.empty():
                event = messages.get()
                events.append(event)
                if "backend_pid" in event:
                    backend_pids[str(event["label"])] = int(event["backend_pid"])
            if len(backend_pids) == 2:
                snapshot = _activity_snapshot(sorted(backend_pids.values()))
                snapshots.append(snapshot)
                if any(row["blocking_pids"] for row in snapshot["activity"]):
                    blocker_observed = True
                    break
            if ready_b.is_set():
                break
            sleep(0.5)
    finally:
        report["blocked_duration_before_release_seconds"] = max(
            0.0, timeout_seconds - max(0.0, deadline - monotonic())
        )
        report["blocker_observed"] = blocker_observed
        report["pre_release_snapshots"] = snapshots
        release.set()
        process_a.join(60)
        process_b.join(60)
        while not messages.empty():
            events.append(messages.get())
        report["events"] = events
        report["process_exit_codes"] = {"a": process_a.exitcode, "b": process_b.exitcode}
        if len(backend_pids) == 2:
            report["post_release_snapshot"] = _activity_snapshot(sorted(backend_pids.values()))
    return report


def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("overlap", "contention"))
    parser.add_argument("--job-a", type=int, default=384)
    parser.add_argument("--job-b", type=int, default=385)
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    job_ids = (args.job_a, args.job_b)
    report = (
        overlap(job_ids)
        if args.command == "overlap"
        else contention(job_ids, args.timeout_seconds)
    )
    encoded = json.dumps(report, indent=2, sort_keys=True, default=_json_default) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(encoded, encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
