from __future__ import annotations

import argparse
import csv
import io
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import UploadFile
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.tables import (
    BackgroundJob,
    BackgroundWorker,
    PipelineDependency,
    PipelineRun,
    PipelineStep,
    UploadRun,
)
from app.services.pipeline_service import (
    MarketDataPolicy,
    PipelineCancellationContended,
    cancel_pipeline,
    start_pipeline,
)
from app.services.upload_service import create_upload_run

TERMINAL_PIPELINES = {"CANCELLED", "COMPLETED", "FAILED", "BLOCKED"}
TERMINAL_JOBS = {"CANCELLED", "COMPLETED", "FAILED", "BLOCKED"}


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _emit(event: str, **payload: Any) -> None:
    message = {"observed_at": _utcnow(), "event": event, **payload}
    print(json.dumps(message, default=str), flush=True)


def _derived_csv(source: Path, tickers: list[str], template_ticker: str) -> bytes:
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    template = next(
        (row for row in rows if (row.get("Symbol") or row.get("Ticker")) == template_ticker),
        None,
    )
    if template is None:
        raise ValueError(f"template ticker {template_ticker} not found in {source}")
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for ticker in tickers:
        source_row = next(
            (row for row in rows if (row.get("Symbol") or row.get("Ticker")) == ticker),
            template,
        )
        row = dict(source_row)
        if "Symbol" in row:
            row["Symbol"] = ticker
        if "Ticker" in row:
            row["Ticker"] = ticker
        if source_row is template and "Description" in row:
            row["Description"] = f"SwingLens authority canary {ticker}"
        writer.writerow(row)
    return output.getvalue().encode("utf-8-sig")


def launch(
    label: str,
    tickers: list[str],
    template_run_id: int,
    template_ticker: str,
) -> tuple[int, int, int]:
    with SessionLocal() as db:
        template_run = db.get(UploadRun, template_run_id)
        if template_run is None or not template_run.file_path:
            raise ValueError(f"template upload run {template_run_id} has no source file")
        source = Path(template_run.file_path)
        payload = _derived_csv(source, tickers, template_ticker)
        upload = UploadFile(
            file=io.BytesIO(payload),
            filename=f"pipeline-authority-canary-{label.lower()}.csv",
        )
        run = create_upload_run(db, upload)
        run.notes = (
            f"Pipeline authority live canary {label}; symbols={','.join(tickers)}; "
            f"fundamental input shape derived from run {template_run_id}/{template_ticker}."
        )
        db.commit()
        pipeline = start_pipeline(
            db,
            run.id,
            requested_by=f"pipeline-authority-canary-{label}",
            market_data_policy=MarketDataPolicy.ALLOW_CACHE_FALLBACK,
        )
        db.commit()
        db.refresh(pipeline)
        _emit(
            "launched",
            label=label,
            tickers=tickers,
            upload_run_id=run.id,
            pipeline_id=pipeline.id,
            root_job_id=(pipeline.result_json or {}).get("background_job_id"),
            pipeline_status=pipeline.status,
        )
        return run.id, pipeline.id, int((pipeline.result_json or {})["background_job_id"])


def _lock_snapshot(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            """
            SELECT pid, pg_blocking_pids(pid) AS blockers, wait_event_type, wait_event,
                   EXTRACT(EPOCH FROM (clock_timestamp() - xact_start)) AS xact_age_seconds,
                   LEFT(query, 240) AS query
            FROM pg_stat_activity
            WHERE cardinality(pg_blocking_pids(pid)) > 0
            ORDER BY pid
            """
        )
    ).mappings()
    return [dict(row) for row in rows]


def _snapshot(db: Session, pipeline_ids: list[int]) -> dict[str, Any]:
    pipelines: list[dict[str, Any]] = []
    for pipeline_id in pipeline_ids:
        pipeline = db.get(PipelineRun, pipeline_id)
        if pipeline is None:
            pipelines.append({"id": pipeline_id, "missing": True})
            continue
        dependencies = list(
            db.scalars(
                select(PipelineDependency)
                .where(PipelineDependency.pipeline_run_id == pipeline_id)
                .order_by(PipelineDependency.id)
            )
        )
        jobs = list(
            db.scalars(
                select(BackgroundJob)
                .where(BackgroundJob.related_run_id == pipeline.upload_run_id)
                .order_by(BackgroundJob.id)
            )
        )
        steps = list(
            db.scalars(
                select(PipelineStep)
                .where(PipelineStep.pipeline_run_id == pipeline_id)
                .order_by(PipelineStep.step_order)
            )
        )
        pipelines.append(
            {
                "id": pipeline.id,
                "upload_run_id": pipeline.upload_run_id,
                "status": pipeline.status,
                "current_step": pipeline.current_step,
                "message": pipeline.message,
                "error_message": pipeline.error_message,
                "dependencies": [
                    {
                        "id": item.id,
                        "type": item.dependency_type,
                        "state": item.state,
                        "root_job_id": item.root_job_id,
                        "child_job_id": item.child_job_id,
                        "continuation_job_id": item.continuation_job_id,
                        "root_worker_instance_id": item.root_worker_instance_id,
                        "required_subjects": item.required_subjects_json,
                    }
                    for item in dependencies
                ],
                "jobs": [
                    {
                        "id": job.id,
                        "type": job.job_type,
                        "status": job.status,
                        "root_job_id": job.root_job_id,
                        "parent_job_id": job.parent_job_id,
                        "dependency_id": job.pipeline_dependency_id,
                        "worker_instance_id": job.worker_instance_id,
                        "heartbeat_at": job.heartbeat_at,
                        "requested_cancel": job.requested_cancel,
                        "retry_count": job.retry_count,
                    }
                    for job in jobs
                ],
                "steps": [
                    {
                        "name": step.step_name,
                        "status": step.status,
                        "retry_count": step.retry_count,
                        "started_at": step.started_at,
                        "completed_at": step.completed_at,
                    }
                    for step in steps
                    if step.status != "PENDING" or step.step_name == "VALIDATING_RUN"
                ],
            }
        )
    workers = list(
        db.scalars(
            select(BackgroundWorker)
            .where(BackgroundWorker.heartbeat_at >= datetime.now(UTC) - timedelta(minutes=2))
            .order_by(BackgroundWorker.worker_id)
        )
    )
    return {
        "pipelines": pipelines,
        "workers": [
            {
                "worker_id": worker.worker_id,
                "instance_id": worker.instance_id,
                "generation": worker.generation,
                "heartbeat_at": worker.heartbeat_at,
                "control_loop_heartbeat_at": worker.control_loop_heartbeat_at,
            }
            for worker in workers
        ],
        "blocking": _lock_snapshot(db),
    }


def _is_continued(pipeline: dict[str, Any]) -> bool:
    dependency = next(iter(pipeline.get("dependencies") or []), {})
    return bool(
        dependency.get("state") == "COMPLETED"
        and dependency.get("continuation_job_id")
        and pipeline.get("status") != "WAITING_DEPENDENCY"
        and pipeline.get("current_step") != "VALIDATING_RUN"
    )


def _state_fingerprint(snapshot: dict[str, Any]) -> str:
    material = {
        "pipelines": [
            {
                "id": pipeline["id"],
                "status": pipeline.get("status"),
                "current_step": pipeline.get("current_step"),
                "dependencies": [
                    (
                        item.get("id"),
                        item.get("state"),
                        item.get("child_job_id"),
                        item.get("continuation_job_id"),
                    )
                    for item in pipeline.get("dependencies") or []
                ],
                "jobs": [
                    (job.get("id"), job.get("status"), job.get("requested_cancel"))
                    for job in pipeline.get("jobs") or []
                ],
                "steps": [
                    (step.get("name"), step.get("status"), step.get("retry_count"))
                    for step in pipeline.get("steps") or []
                ],
            }
            for pipeline in snapshot["pipelines"]
        ],
        "blocking": snapshot["blocking"],
    }
    return json.dumps(material, sort_keys=True, default=str)


def _cancel(pipeline_id: int, reason: str) -> None:
    started = time.monotonic()
    with SessionLocal() as db:
        try:
            pipeline = cancel_pipeline(db, pipeline_id)
            db.commit()
            _emit(
                "cancel_returned",
                pipeline_id=pipeline_id,
                reason=reason,
                elapsed_seconds=round(time.monotonic() - started, 6),
                status=pipeline.status,
            )
        except PipelineCancellationContended as exc:
            db.rollback()
            _emit(
                "cancel_contended",
                pipeline_id=pipeline_id,
                reason=reason,
                elapsed_seconds=round(time.monotonic() - started, 6),
                diagnostics=exc.diagnostics,
            )


def observe(
    pipeline_ids: list[int],
    timeout_seconds: float,
    cancel_on_waiting: bool,
    cancel_after_continued: bool,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    cancelled: set[int] = set()
    continued: set[int] = set()
    prior: str | None = None
    maximum_blockers = 0
    while time.monotonic() < deadline:
        with SessionLocal() as db:
            snapshot = _snapshot(db, pipeline_ids)
        encoded = _state_fingerprint(snapshot)
        if encoded != prior:
            _emit("snapshot", **snapshot)
            prior = encoded
        maximum_blockers = max(maximum_blockers, len(snapshot["blocking"]))
        for pipeline in snapshot["pipelines"]:
            pipeline_id = int(pipeline["id"])
            if _is_continued(pipeline):
                continued.add(pipeline_id)
            dependency = next(iter(pipeline.get("dependencies") or []), {})
            should_cancel_waiting = (
                cancel_on_waiting
                and pipeline.get("status") == "WAITING_DEPENDENCY"
                and dependency.get("child_job_id") is not None
            )
            should_cancel_continued = cancel_after_continued and pipeline_id in continued
            if pipeline_id not in cancelled and (should_cancel_waiting or should_cancel_continued):
                _cancel(
                    pipeline_id,
                    "WAITING_DEPENDENCY" if should_cancel_waiting else "CONTINUED",
                )
                cancelled.add(pipeline_id)
        all_terminal = all(
            pipeline.get("status") in TERMINAL_PIPELINES for pipeline in snapshot["pipelines"]
        )
        jobs_terminal = all(
            job.get("status") in TERMINAL_JOBS
            for pipeline in snapshot["pipelines"]
            for job in pipeline.get("jobs") or []
        )
        if all_terminal and jobs_terminal:
            _emit(
                "observation_complete",
                pipeline_ids=pipeline_ids,
                continued_pipeline_ids=sorted(continued),
                cancelled_pipeline_ids=sorted(cancelled),
                maximum_blocked_sessions=maximum_blockers,
                final=snapshot,
            )
            return
        time.sleep(0.2)
    _emit(
        "observation_timeout",
        pipeline_ids=pipeline_ids,
        continued_pipeline_ids=sorted(continued),
        cancelled_pipeline_ids=sorted(cancelled),
        maximum_blocked_sessions=maximum_blockers,
    )
    raise SystemExit(2)


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    launch_parser = subparsers.add_parser("launch")
    launch_parser.add_argument("--label", required=True)
    launch_parser.add_argument("--tickers", nargs="+", required=True)
    launch_parser.add_argument("--template-run-id", type=int, default=189)
    launch_parser.add_argument("--template-ticker", default="AAPL")
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--label", required=True)
    run_parser.add_argument("--tickers", nargs="+", required=True)
    run_parser.add_argument("--template-run-id", type=int, default=189)
    run_parser.add_argument("--template-ticker", default="AAPL")
    run_parser.add_argument("--timeout-seconds", type=float, default=900)
    run_parser.add_argument("--cancel-on-waiting", action="store_true")
    run_parser.add_argument("--cancel-after-continued", action="store_true")
    pair_parser = subparsers.add_parser("run-pair")
    pair_parser.add_argument("--label", required=True)
    pair_parser.add_argument("--tickers", nargs=2, required=True)
    pair_parser.add_argument("--template-run-id", type=int, default=189)
    pair_parser.add_argument("--template-ticker", default="AAPL")
    pair_parser.add_argument("--timeout-seconds", type=float, default=900)
    pair_parser.add_argument("--cancel-after-continued", action="store_true")
    observe_parser = subparsers.add_parser("observe")
    observe_parser.add_argument("--pipeline-ids", nargs="+", type=int, required=True)
    observe_parser.add_argument("--timeout-seconds", type=float, default=900)
    observe_parser.add_argument("--cancel-on-waiting", action="store_true")
    observe_parser.add_argument("--cancel-after-continued", action="store_true")
    args = parser.parse_args()
    if args.command == "launch":
        launch(args.label, args.tickers, args.template_run_id, args.template_ticker)
    elif args.command == "observe":
        observe(
            args.pipeline_ids,
            args.timeout_seconds,
            args.cancel_on_waiting,
            args.cancel_after_continued,
        )
    elif args.command == "run":
        _run_id, pipeline_id, _root_id = launch(
            args.label,
            args.tickers,
            args.template_run_id,
            args.template_ticker,
        )
        observe(
            [pipeline_id],
            args.timeout_seconds,
            args.cancel_on_waiting,
            args.cancel_after_continued,
        )
    else:
        pipeline_ids = []
        for index, ticker in enumerate(args.tickers, start=1):
            _run_id, pipeline_id, _root_id = launch(
                f"{args.label}-{index}",
                [ticker],
                args.template_run_id,
                args.template_ticker,
            )
            pipeline_ids.append(pipeline_id)
        observe(
            pipeline_ids,
            args.timeout_seconds,
            False,
            args.cancel_after_continued,
        )


if __name__ == "__main__":
    main()
