from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.models.tables import (
    RawCompanyRow,
    SetupLifecycleEpisode,
    SetupLifecycleEvent,
    SetupSignalSnapshot,
    SignalAlertEvent,
    SignalAlertRule,
    SignalChangeEvent,
    TechnicalScore,
    UploadRun,
)
from app.services.setup_lifecycle.config import load_setup_lifecycle_config
from app.services.setup_lifecycle.evaluation_service import SetupLifecycleEvaluationService
from app.services.setup_lifecycle.query_service import snapshot_payload
from app.settings import Settings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Certify SLSE against chronological preserved SwingLens source runs."
    )
    parser.add_argument("--run-ids", required=True, help="Comma-separated chronological run IDs")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    run_ids = tuple(int(value.strip()) for value in args.run_ids.split(",") if value.strip())
    if not run_ids or len(run_ids) != len(set(run_ids)):
        raise SystemExit("--run-ids must contain unique run IDs")

    settings = Settings()
    config = load_setup_lifecycle_config()
    engine = create_engine(settings.database_url)
    report: dict[str, object] = {
        "certification": "SLSE natural multi-date real-source",
        "started_at": datetime.now(UTC).isoformat(),
        "database": {"host": engine.url.host, "name": engine.url.database},
        "engine_version": config.engine.version,
        "config_version": config.engine.config_version,
        "config_hash": config.config_hash,
        "engine_enabled": config.engine.enabled,
        "source_run_ids": list(run_ids),
    }

    with Session(engine) as db:
        runs = list(
            db.scalars(
                select(UploadRun)
                .where(UploadRun.id.in_(run_ids))
                .order_by(UploadRun.processed_at, UploadRun.id)
            )
        )
        if [row.id for row in runs] != list(run_ids):
            raise SystemExit("run IDs must exist and be supplied in processed-time order")
        if any(row.status != "COMPLETED" or row.processed_at is None for row in runs):
            raise SystemExit("all natural-certification runs must be completed preserved runs")
        source_rows = {
            row.id: {
                "processed_at": row.processed_at.isoformat(),
                "declared_rows": row.row_count,
                "raw_rows": db.scalar(
                    select(func.count())
                    .select_from(RawCompanyRow)
                    .where(RawCompanyRow.run_id == row.id)
                ),
                "technical_rows": db.scalar(
                    select(func.count())
                    .select_from(TechnicalScore)
                    .where(TechnicalScore.run_id == row.id)
                ),
            }
            for row in runs
        }
        report["source_runs"] = source_rows
        report["before"] = _counts(db)

        evaluator = SetupLifecycleEvaluationService(config=config)
        evaluations = []
        started = time.perf_counter()
        for run in runs:
            run_started = time.perf_counter()
            result = evaluator.evaluate_run(
                db,
                run.id,
                requester="slse-natural-certification",
            )
            db.commit()
            evaluations.append(
                {
                    "run_id": run.id,
                    "processed_at": run.processed_at.isoformat(),
                    "duration_seconds": round(time.perf_counter() - run_started, 6),
                    **result.as_dict(),
                }
            )
            if result.failed:
                raise SystemExit(f"natural certification failed for source run {run.id}")
        report["duration_seconds"] = round(time.perf_counter() - started, 6)
        report["evaluations"] = evaluations
        report["after"] = _counts(db)
        evaluation_ids = [item["evaluation_run_id"] for item in evaluations]

        snapshots = list(
            db.scalars(
                select(SetupSignalSnapshot).where(
                    SetupSignalSnapshot.evaluation_run_id.in_(evaluation_ids),
                )
            )
        )
        snapshot_ids = tuple(row.id for row in snapshots)
        events = (
            list(
                db.scalars(
                    select(SetupLifecycleEvent).where(
                        SetupLifecycleEvent.snapshot_id.in_(snapshot_ids)
                    )
                )
            )
            if snapshot_ids
            else []
        )
        changes = (
            list(
                db.scalars(
                    select(SignalChangeEvent).where(
                        SignalChangeEvent.current_snapshot_id.in_(snapshot_ids)
                    )
                )
            )
            if snapshot_ids
            else []
        )
        alerts = list(
            db.execute(
                select(SignalAlertEvent, SignalAlertRule.rule_id)
                .join(SignalAlertRule, SignalAlertEvent.alert_rule_id == SignalAlertRule.id)
                .where(SignalAlertEvent.evaluation_run_id.in_(evaluation_ids))
            )
        )
        episodes = (
            list(
                db.scalars(
                    select(SetupLifecycleEpisode).where(
                        SetupLifecycleEpisode.config_hash == config.config_hash,
                        SetupLifecycleEpisode.current_snapshot_id.in_(snapshot_ids),
                    )
                )
            )
            if snapshot_ids
            else []
        )

        lineage_checks = [_lineage_check(db, row) for row in snapshots]
        report["natural_output"] = {
            "snapshots": len(snapshots),
            "canonical_snapshots": sum(row.is_canonical for row in snapshots),
            "states": dict(
                sorted(
                    Counter(row.lifecycle_state_candidate or "NONE" for row in snapshots).items()
                )
            ),
            "actionability": dict(
                sorted(Counter(row.actionability_candidate or "NONE" for row in snapshots).items())
            ),
            "data_quality": dict(
                sorted(Counter(row.data_quality_label for row in snapshots).items())
            ),
            "lifecycle_event_types": dict(
                sorted(Counter(row.event_type for row in events).items())
            ),
            "lifecycle_to_states": dict(
                sorted(Counter(row.to_state for row in events if row.to_state).items())
            ),
            "signal_change_keys": dict(sorted(Counter(row.signal_key for row in changes).items())),
            "alert_types": dict(sorted(Counter(rule_id for _, rule_id in alerts).items())),
            "episode_states": dict(sorted(Counter(row.current_state for row in episodes).items())),
        }
        report["lineage_contract"] = {
            "checked": len(lineage_checks),
            "passed": sum(lineage_checks),
            "failed": len(lineage_checks) - sum(lineage_checks),
            "rule": (
                "query DTO and persisted snapshot equal preserved raw/technical "
                "PIT source identity and values"
            ),
        }
        if not lineage_checks or not all(lineage_checks):
            raise SystemExit("natural certification lineage contract failed")

    engine.dispose()
    report["completed_at"] = datetime.now(UTC).isoformat()
    report["status"] = "PASS"
    rendered = json.dumps(report, indent=2, sort_keys=True, default=str)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


def _lineage_check(db: Session, snapshot: SetupSignalSnapshot) -> bool:
    raw = db.get(RawCompanyRow, snapshot.raw_row_id) if snapshot.raw_row_id else None
    technical = (
        db.get(TechnicalScore, snapshot.technical_score_id) if snapshot.technical_score_id else None
    )
    dto = snapshot_payload(snapshot)
    return bool(
        raw is not None
        and raw.run_id == snapshot.run_id
        and raw.ticker == snapshot.ticker
        and technical is not None
        and technical.run_id == snapshot.run_id
        and technical.ticker == snapshot.ticker
        and snapshot.dual_score == technical.dual_score
        and snapshot.setup_score == technical.setup_score
        and snapshot.technical_classification == technical.classification
        and dto["id"] == snapshot.id
        and dto["ticker"] == snapshot.ticker
        and dto["engine_version"] == snapshot.engine_version
        and dto["config_hash"] == snapshot.config_hash
        and dto["source_data_hash"] == snapshot.source_data_hash
    )


def _counts(db: Session) -> dict[str, int]:
    return {
        model.__tablename__: db.scalar(select(func.count()).select_from(model)) or 0
        for model in (
            SetupSignalSnapshot,
            SetupLifecycleEpisode,
            SetupLifecycleEvent,
            SignalChangeEvent,
            SignalAlertEvent,
        )
    }


if __name__ == "__main__":
    raise SystemExit(main())
