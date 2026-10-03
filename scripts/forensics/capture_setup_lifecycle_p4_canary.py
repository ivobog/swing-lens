"""Capture bounded production evidence for the Setup Lifecycle P4 canary."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models.ceri_tables import CeriFeatureSourceManifest
from app.models.tables import (
    BackgroundJob,
    PipelineRun,
    PipelineStep,
    SetupLifecycleEpisode,
    SetupLifecycleEvaluationEvidence,
    SetupLifecycleEvaluationRun,
    SetupLifecycleEvent,
    SetupLifecycleTransitionEvidence,
    SetupSignalSnapshot,
    WinnerPredictionSnapshot,
)


def _duration(started: datetime | None, completed: datetime | None) -> float | None:
    return (completed - started).total_seconds() if started and completed else None


def _identity_value(payload: dict, section: str, field: str):
    return ((payload.get(section) or {}).get(field) or {}).get("value")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pipeline-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    with SessionLocal() as db:
        pipeline = db.get(PipelineRun, args.pipeline_id)
        if pipeline is None:
            raise ValueError("pipeline not found")
        run_id = pipeline.upload_run_id
        steps = list(
            db.scalars(
                select(PipelineStep)
                .where(PipelineStep.pipeline_run_id == pipeline.id)
                .order_by(PipelineStep.step_order)
            )
        )
        jobs = list(
            db.scalars(
                select(BackgroundJob)
                .where(BackgroundJob.related_run_id == run_id)
                .order_by(BackgroundJob.id)
            )
        )
        snapshots = list(
            db.scalars(
                select(SetupSignalSnapshot)
                .where(SetupSignalSnapshot.run_id == run_id)
                .order_by(SetupSignalSnapshot.ticker)
            )
        )
        lifecycle_runs = list(
            db.scalars(
                select(SetupLifecycleEvaluationRun)
                .where(SetupLifecycleEvaluationRun.source_run_id == run_id)
                .order_by(SetupLifecycleEvaluationRun.id)
            )
        )
        lifecycle = next((row for row in reversed(lifecycle_runs) if row.mode == "LIVE"), None)
        evaluations = (
            list(
                db.scalars(
                    select(SetupLifecycleEvaluationEvidence)
                    .where(SetupLifecycleEvaluationEvidence.evaluation_run_id == lifecycle.id)
                    .order_by(SetupLifecycleEvaluationEvidence.ticker)
                )
            )
            if lifecycle is not None
            else []
        )
        evaluation_ids = [row.id for row in evaluations]
        transitions = (
            list(
                db.scalars(
                    select(SetupLifecycleTransitionEvidence)
                    .where(
                        SetupLifecycleTransitionEvidence.evaluation_evidence_id.in_(evaluation_ids)
                    )
                    .order_by(SetupLifecycleTransitionEvidence.ticker)
                )
            )
            if evaluation_ids
            else []
        )
        events = (
            list(
                db.scalars(
                    select(SetupLifecycleEvent)
                    .where(SetupLifecycleEvent.evaluation_run_id == lifecycle.id)
                    .order_by(SetupLifecycleEvent.ticker, SetupLifecycleEvent.id)
                )
            )
            if lifecycle is not None
            else []
        )
        snapshot_ids = [row.id for row in snapshots]
        episodes = (
            list(
                db.scalars(
                    select(SetupLifecycleEpisode)
                    .where(SetupLifecycleEpisode.current_snapshot_id.in_(snapshot_ids))
                    .order_by(SetupLifecycleEpisode.ticker, SetupLifecycleEpisode.id)
                )
            )
            if snapshot_ids
            else []
        )
        latest = {
            row.id: db.get(SetupLifecycleEvaluationEvidence, row.latest_evaluation_evidence_id)
            for row in episodes
        }
        snapshot_by_id = {row.id: row for row in snapshots}
        episode_rows = []
        mixed_scope = []
        for episode in episodes:
            evaluation = latest[episode.id]
            snapshot = snapshot_by_id.get(episode.current_snapshot_id)
            identity = dict((evaluation.payload_json or {}).get("calculation_identity") or {})
            row = {
                "ticker": episode.ticker,
                "episode_id": episode.id,
                "family": episode.setup_family,
                "status": episode.status,
                "state": episode.current_state,
                "phase": episode.current_phase,
                "is_primary": episode.is_primary,
                "primary_rank": episode.primary_rank,
                "snapshot_id": episode.current_snapshot_id,
                "evaluation_id": evaluation.id,
                "evaluation_cutoff": evaluation.calculation_cutoff_at,
                "evaluation_context_id": _identity_value(
                    identity, "calculation_context", "market_calculation_context_id"
                ),
                "evaluation_run_id": _identity_value(identity, "ownership", "run_id"),
                "evaluation_pipeline_id": _identity_value(identity, "ownership", "pipeline_id"),
                "snapshot_cutoff": getattr(snapshot, "calculation_cutoff_at", None),
                "snapshot_context_id": getattr(snapshot, "calculation_context_id", None),
                "reconciliation": (evaluation.payload_json or {}).get("reconciliation"),
            }
            if snapshot is not None and (
                evaluation.calculation_cutoff_at != snapshot.calculation_cutoff_at
                or row["evaluation_context_id"] != snapshot.calculation_context_id
            ):
                mixed_scope.append(row)
            episode_rows.append(row)

        winner_rows = list(
            db.scalars(
                select(WinnerPredictionSnapshot)
                .where(WinnerPredictionSnapshot.run_id == run_id)
                .order_by(WinnerPredictionSnapshot.ticker)
            )
        )
        manifests = list(
            db.scalars(
                select(CeriFeatureSourceManifest)
                .where(CeriFeatureSourceManifest.run_id == run_id)
                .order_by(CeriFeatureSourceManifest.batch_index)
            )
        )
        plans = list((lifecycle.audit_json or {}).get("plans") or []) if lifecycle else []
        report = {
            "pipeline": {
                "id": pipeline.id,
                "upload_run_id": run_id,
                "status": pipeline.status,
                "current_step": pipeline.current_step,
                "message": pipeline.message,
                "error_message": pipeline.error_message,
                "ceri_completion_state": (pipeline.result_json or {}).get("ceri_completion_state"),
                "ceri_certified_capture_count": (pipeline.result_json or {}).get(
                    "ceri_certified_capture_count"
                ),
            },
            "steps": [
                {
                    "order": row.step_order,
                    "name": row.step_name,
                    "status": row.status,
                    "started_at": row.started_at,
                    "completed_at": row.completed_at,
                    "duration_seconds": _duration(row.started_at, row.completed_at),
                    "message": row.message,
                    "error_message": row.error_message,
                }
                for row in steps
            ],
            "jobs": {
                "count": len(jobs),
                "statuses": dict(Counter(row.status for row in jobs)),
                "retry_total": sum(row.retry_count for row in jobs),
                "failed": [row.id for row in jobs if row.status in {"FAILED", "BLOCKED"}],
            },
            "ceri": {
                "manifest_count": len(manifests),
                "bundle_fingerprints": [row.bundle_fingerprint for row in manifests],
                "snapshot_count": (pipeline.result_json or {}).get("ceri_certified_capture_count"),
            },
            "setup_snapshot_count": len(snapshots),
            "setup_contexts": sorted(
                {(row.calculation_context_id, str(row.calculation_cutoff_at)) for row in snapshots}
            ),
            "lifecycle_run": (
                {
                    "id": lifecycle.id,
                    "status": lifecycle.status,
                    "phase": lifecycle.current_phase,
                    "counts": lifecycle.counts_json,
                    "errors": lifecycle.error_summary_json,
                    "audit_contract": (lifecycle.audit_json or {}).get("contract"),
                }
                if lifecycle
                else None
            ),
            "reconciliation": {
                "plans": plans,
                "planned_actions": sum(
                    len(row.get("displaced_episode_ids") or []) for row in plans
                ),
                "evaluation_count": len(evaluations),
                "transition_evidence_count": len(transitions),
                "event_count": len(events),
                "event_types": dict(Counter(row.event_type for row in events)),
            },
            "episodes": episode_rows,
            "mixed_scope_participants": mixed_scope,
            "winner": {
                "prediction_count": len(winner_rows),
                "eligible_count": sum(row.eligibility_status == "ELIGIBLE" for row in winner_rows),
                "source_cutoffs": sorted({str(row.source_data_cutoff_at) for row in winner_rows}),
                "tickers": [row.ticker for row in winner_rows],
            },
            "database_counts": {
                "winner_estimates": db.scalar(
                    select(func.count())
                    .select_from(WinnerPredictionSnapshot)
                    .where(WinnerPredictionSnapshot.run_id == run_id)
                )
            },
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
