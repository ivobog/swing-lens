from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.db import engine
from app.models.ceri_tables import CeriAlertEvent, CeriChangeEvent, CeriScoreSnapshot
from app.models.tables import (
    BackgroundJob,
    CombinedResult,
    FundamentalScore,
    PipelineRun,
    PipelineStep,
    RankingResult,
    RawCompanyRow,
    SetupLifecycleEvaluationRun,
    SetupLifecycleEvent,
    SetupSignalSnapshot,
    TechnicalScore,
    UploadRun,
    WinnerPredictionSnapshot,
)
from app.services.ceri.sec.processor_lifecycle import lifecycle_state
from app.services.ceri.sec.processor_signature import sec_guidance_processor_signature
from app.services.ceri.sec.readiness_diagnostics import diagnose_sec_readiness


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect a recovered pipeline run read-only.")
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--pipeline-id", required=True, type=int)
    parser.add_argument("--original-job-id", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    with Session(engine) as db:
        report = inspect_recovery(
            db,
            run_id=args.run_id,
            pipeline_id=args.pipeline_id,
            original_job_id=args.original_job_id,
        )
    encoded = json.dumps(report, indent=2, sort_keys=True, default=str)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
        print(f"inspection_output={args.output.resolve()}")
    print(encoded)
    return 0


def inspect_recovery(
    db: Session,
    *,
    run_id: int,
    pipeline_id: int,
    original_job_id: int | None,
) -> dict[str, Any]:
    run = db.get(UploadRun, run_id)
    pipeline = db.get(PipelineRun, pipeline_id)
    if run is None or pipeline is None or pipeline.upload_run_id != run_id:
        raise SystemExit("Run/pipeline identity was not found or does not match.")
    tickers = tuple(
        sorted(
            {
                str(value).strip().upper()
                for value in db.scalars(
                    select(RawCompanyRow.ticker).where(RawCompanyRow.run_id == run_id)
                )
                if value
            }
        )
    )
    lifecycle = lifecycle_state(db)
    readiness = diagnose_sec_readiness(
        db,
        tickers=tickers,
        processor_signature=sec_guidance_processor_signature(),
    )
    steps = list(
        db.scalars(
            select(PipelineStep)
            .where(PipelineStep.pipeline_run_id == pipeline_id)
            .order_by(PipelineStep.step_order)
        ).all()
    )
    jobs = list(
        db.scalars(
            select(BackgroundJob)
            .where(BackgroundJob.related_run_id == run_id)
            .order_by(BackgroundJob.id)
        ).all()
    )
    job_groups: dict[str, int] = {}
    for job in jobs:
        key = f"{job.job_type}:{job.status}"
        job_groups[key] = job_groups.get(key, 0) + 1
    evaluation_ids = select(SetupLifecycleEvaluationRun.id).where(
        SetupLifecycleEvaluationRun.source_run_id == run_id
    )
    snapshot_ids = select(CeriScoreSnapshot.id).where(CeriScoreSnapshot.run_id == run_id)
    change_ids = select(CeriChangeEvent.id).where(CeriChangeEvent.to_snapshot_id.in_(snapshot_ids))
    original_job = db.get(BackgroundJob, original_job_id) if original_job_id else None
    return {
        "run": {
            "id": run.id,
            "status": run.status,
            "row_count": run.row_count,
            "distinct_tickers": len(tickers),
        },
        "pipeline": {
            "id": pipeline.id,
            "status": pipeline.status,
            "current_step": pipeline.current_step,
            "message": pipeline.message,
            "error_message": pipeline.error_message,
            "background_job_id": (pipeline.result_json or {}).get("background_job_id"),
            "resumed_from_step": (pipeline.result_json or {}).get("resumed_from_step"),
            "steps": [
                {
                    "order": step.step_order,
                    "name": step.step_name,
                    "status": step.status,
                    "retry_count": step.retry_count,
                    "started_at": step.started_at,
                    "completed_at": step.completed_at,
                    "error_message": step.error_message,
                }
                for step in steps
            ],
        },
        "original_job": (
            {
                "id": original_job.id,
                "status": original_job.status,
                "retry_count": original_job.retry_count,
                "max_retries": original_job.max_retries,
                "completed_at": original_job.completed_at,
            }
            if original_job is not None
            else None
        ),
        "processor": lifecycle.as_dict(),
        "readiness": readiness.as_dict(include_tickers=False),
        "outputs": {
            "fundamental_scores": _run_count(db, FundamentalScore, run_id),
            "technical_scores": _run_count(db, TechnicalScore, run_id),
            "combined_results": _run_count(db, CombinedResult, run_id),
            "ranking_results": _run_count(db, RankingResult, run_id),
            "ceri_score_snapshots": _run_count(db, CeriScoreSnapshot, run_id),
            "ceri_change_events": int(
                db.scalar(
                    select(func.count())
                    .select_from(CeriChangeEvent)
                    .where(CeriChangeEvent.to_snapshot_id.in_(snapshot_ids))
                )
                or 0
            ),
            "ceri_alert_events": int(
                db.scalar(
                    select(func.count())
                    .select_from(CeriAlertEvent)
                    .where(CeriAlertEvent.source_change_event_id.in_(change_ids))
                )
                or 0
            ),
            "setup_evaluation_runs": int(
                db.scalar(
                    select(func.count())
                    .select_from(SetupLifecycleEvaluationRun)
                    .where(SetupLifecycleEvaluationRun.source_run_id == run_id)
                )
                or 0
            ),
            "setup_signal_snapshots": _run_count(db, SetupSignalSnapshot, run_id),
            "setup_lifecycle_events": int(
                db.scalar(
                    select(func.count())
                    .select_from(SetupLifecycleEvent)
                    .where(SetupLifecycleEvent.evaluation_run_id.in_(evaluation_ids))
                )
                or 0
            ),
            "winner_prediction_snapshots": _run_count(
                db, WinnerPredictionSnapshot, run_id
            ),
        },
        "distinct_output_tickers": {
            "fundamental_scores": _distinct_tickers(db, FundamentalScore, run_id),
            "technical_scores": _distinct_tickers(db, TechnicalScore, run_id),
            "combined_results": _distinct_tickers(db, CombinedResult, run_id),
            "ranking_results": _distinct_tickers(db, RankingResult, run_id),
            "ceri_score_snapshots": _distinct_tickers(db, CeriScoreSnapshot, run_id),
            "setup_signal_snapshots": _distinct_tickers(db, SetupSignalSnapshot, run_id),
            "winner_prediction_snapshots": _distinct_tickers(
                db, WinnerPredictionSnapshot, run_id
            ),
        },
        "background_jobs": {
            "count": len(jobs),
            "by_type_status": dict(sorted(job_groups.items())),
            "jobs": [
                {
                    "id": job.id,
                    "job_type": job.job_type,
                    "status": job.status,
                    "retry_count": job.retry_count,
                    "workflow_key": job.workflow_key,
                    "request_key": job.request_key,
                    "result": job.result_json,
                }
                for job in jobs
            ],
        },
    }


def _run_count(db: Session, model: type, run_id: int) -> int:
    return int(
        db.scalar(select(func.count()).select_from(model).where(model.run_id == run_id))
        or 0
    )


def _distinct_tickers(db: Session, model: type, run_id: int) -> int:
    return int(
        db.scalar(
            select(func.count(distinct(model.ticker))).where(model.run_id == run_id)
        )
        or 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
