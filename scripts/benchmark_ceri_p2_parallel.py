"""Run frozen Run 14 feature writer replays serially or concurrently.

Every child uses ``profile_ceri_writer_manifest.py`` and rolls its financial
transaction back.  This harness measures process/host resources and PostgreSQL
wait state without altering retained jobs, manifests, checkpoints, or outputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, sleep

import psutil
from sqlalchemy import text

from app.db import SessionLocal


def _semantic_projection(report: dict) -> dict:
    return {
        "job_id": report["job_id"],
        "source_count": report["source_count"],
        "bundle_fingerprint": report["bundle_fingerprint"],
        "selected_tickers": report["selected_tickers"],
        "results": [
            {
                key: row[key]
                for key in ("ticker", "features", "inserted", "updated", "deduplicated")
            }
            for row in report["results"]
        ],
        "feature_evidence": report["feature_evidence"],
        "source_integrity": {
            key: report["source_integrity_telemetry"].get(key)
            for key in (
                "assert_rows",
                "assert_dirty_rows",
                "full_audit_calls",
                "full_audit_rows",
                "refresh_executed_calls",
                "refresh_rows",
                "invalidation_total",
                "writer_calls",
            )
        },
    }


def _sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _db_snapshot() -> list[dict]:
    with SessionLocal() as db:
        rows = db.execute(
            text(
                """
                SELECT pid, application_name, state, wait_event_type, wait_event,
                       pg_blocking_pids(pid) AS blocking_pids,
                       EXTRACT(EPOCH FROM (clock_timestamp() - xact_start)) AS xact_age_seconds,
                       left(query, 500) AS query
                FROM pg_stat_activity
                WHERE application_name LIKE 'ceri-p2-profile-job-%'
                ORDER BY pid
                """
            )
        ).mappings().all()
        db.rollback()
        return [dict(row) for row in rows]


def _private_bytes(process: psutil.Process) -> int | None:
    try:
        return int(getattr(process.memory_full_info(), "private", 0)) or None
    except (psutil.Error, OSError):
        return None


def _process_tree(process: psutil.Process) -> list[psutil.Process]:
    try:
        return [process, *process.children(recursive=True)]
    except psutil.Error:
        return [process]


def _command(
    job_id: int,
    output: Path,
    compare_paths: bool,
    compare_fingerprint_paths: bool,
    reference_fingerprint_path: bool,
) -> list[str]:
    command = [
        sys.executable,
        "scripts/profile_ceri_writer_manifest.py",
        "--job-id",
        str(job_id),
        "--all-tickers",
        "--skip-cprofile",
        "--output",
        str(output),
    ]
    if compare_paths:
        command.append("--compare-paths")
    if compare_fingerprint_paths:
        command.append("--compare-fingerprint-paths")
    if reference_fingerprint_path:
        command.append("--reference-fingerprint-path")
    return command


def _run(
    mode: str,
    outputs: dict[int, Path],
    compare_paths: bool,
    compare_fingerprint_paths: bool,
    reference_fingerprint_path: bool,
) -> dict:
    jobs = (384, 385)
    processes: dict[int, subprocess.Popen] = {}
    process_handles: dict[int, psutil.Process] = {}
    stdout_files = {}
    peak_rss = {job: 0 for job in jobs}
    peak_private = {job: 0 for job in jobs}
    peak_combined_rss = 0
    peak_combined_private = 0
    cpu_seconds = {job: 0.0 for job in jobs}
    host_available_min = None
    host_cpu_samples = []
    db_samples = []
    lock_wait_samples = 0
    blocked_seconds = {job: 0.0 for job in jobs}
    started = monotonic()

    def launch(job_id: int) -> None:
        stdout_path = outputs[job_id].with_suffix(".stdout.log")
        stream = stdout_path.open("w", encoding="utf-8")
        stdout_files[job_id] = stream
        child = subprocess.Popen(
            _command(
                job_id,
                outputs[job_id],
                compare_paths,
                compare_fingerprint_paths,
                reference_fingerprint_path,
            ),
            cwd=Path.cwd(),
            stdout=stream,
            stderr=subprocess.STDOUT,
        )
        processes[job_id] = child
        process_handles[job_id] = psutil.Process(child.pid)

    try:
        if mode == "parallel":
            for job in jobs:
                launch(job)
        else:
            launch(jobs[0])
        completed = set()
        while len(completed) < len(jobs):
            for job_id, child in list(processes.items()):
                handle = process_handles[job_id]
                if child.poll() is None:
                    try:
                        tree = _process_tree(handle)
                        rss = sum(member.memory_info().rss for member in tree)
                        private_values = [_private_bytes(member) for member in tree]
                        private = sum(value or 0 for value in private_values)
                        peak_rss[job_id] = max(peak_rss[job_id], rss)
                        peak_private[job_id] = max(peak_private[job_id], private)
                        cpu_seconds[job_id] = sum(
                            member.cpu_times().user + member.cpu_times().system
                            for member in tree
                        )
                    except psutil.Error:
                        pass
                elif job_id not in completed:
                    completed.add(job_id)
                    stdout_files[job_id].close()
                    if child.returncode != 0:
                        log_path = outputs[job_id].with_suffix(".stdout.log")
                        raise RuntimeError(
                            f"job {job_id} replay failed; see {log_path}"
                        )
                    if mode == "serial" and job_id == jobs[0]:
                        launch(jobs[1])
            active_rss = 0
            active_private = 0
            for job_id, child in processes.items():
                if child.poll() is None:
                    active_rss += peak_rss[job_id] if mode == "serial" else sum(
                        member.memory_info().rss
                        for member in _process_tree(process_handles[job_id])
                    )
                    active_private += peak_private[job_id] if mode == "serial" else sum(
                        _private_bytes(member) or 0
                        for member in _process_tree(process_handles[job_id])
                    )
            peak_combined_rss = max(peak_combined_rss, active_rss)
            peak_combined_private = max(peak_combined_private, active_private)
            memory = psutil.virtual_memory()
            host_available_min = (
                memory.available
                if host_available_min is None
                else min(host_available_min, memory.available)
            )
            host_cpu_samples.append(psutil.cpu_percent(interval=None))
            snapshot = _db_snapshot()
            db_samples.append({"elapsed_seconds": monotonic() - started, "activity": snapshot})
            for row in snapshot:
                if row["wait_event_type"] == "Lock" or row["blocking_pids"]:
                    lock_wait_samples += 1
                    label = str(row["application_name"])
                    for job in jobs:
                        if label.endswith(str(job)):
                            blocked_seconds[job] += 0.5
            sleep(0.5)
    finally:
        for stream in stdout_files.values():
            if not stream.closed:
                stream.close()
        for child in processes.values():
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=30)

    reports = {job: json.loads(outputs[job].read_text(encoding="utf-8")) for job in jobs}
    projections = {job: _semantic_projection(reports[job]) for job in jobs}
    return {
        "mode": mode,
        "started_at": datetime.now(UTC).isoformat(),
        "makespan_seconds": monotonic() - started,
        "jobs": {
            str(job): {
                "output": str(outputs[job].resolve()),
                "writer_wall_seconds": reports[job]["writer_wall_s"],
                "writer_cpu_seconds": reports[job]["writer_cpu_s"],
                "retained_load_wall_seconds": reports[job]["retained_load_wall_s"],
                "peak_rss_bytes": peak_rss[job],
                "peak_private_bytes": peak_private[job] or None,
                "sampled_process_cpu_seconds": cpu_seconds[job],
                "blocked_seconds": blocked_seconds[job],
                "semantic_sha256": _sha256(projections[job]),
                "semantic_projection": projections[job],
                "source_integrity_telemetry": reports[job]["source_integrity_telemetry"],
            }
            for job in jobs
        },
        "peak_combined_rss_bytes": peak_combined_rss,
        "peak_combined_private_bytes": peak_combined_private,
        "minimum_host_available_bytes": host_available_min,
        "host_cpu_average_percent": (
            sum(host_cpu_samples) / len(host_cpu_samples) if host_cpu_samples else None
        ),
        "host_cpu_peak_percent": max(host_cpu_samples) if host_cpu_samples else None,
        "postgres_lock_wait_samples": lock_wait_samples,
        "postgres_samples": db_samples,
        "rollback_only": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("serial", "parallel"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--child-output-dir", type=Path, required=True)
    parser.add_argument("--compare-paths", action="store_true")
    parser.add_argument("--compare-fingerprint-paths", action="store_true")
    parser.add_argument("--reference-fingerprint-path", action="store_true")
    args = parser.parse_args()
    args.child_output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        job: args.child_output_dir / f"ceri_p2_{args.mode}_job_{job}.json"
        for job in (384, 385)
    }
    report = _run(
        args.mode,
        outputs,
        args.compare_paths,
        args.compare_fingerprint_paths,
        args.reference_fingerprint_path,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "mode": report["mode"],
                "makespan_seconds": report["makespan_seconds"],
                "peak_combined_rss_bytes": report["peak_combined_rss_bytes"],
                "minimum_host_available_bytes": report["minimum_host_available_bytes"],
                "postgres_lock_wait_samples": report["postgres_lock_wait_samples"],
                "semantic_sha256": {
                    job: report["jobs"][str(job)]["semantic_sha256"] for job in (384, 385)
                },
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
