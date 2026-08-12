from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.models.tables import (
    CombinedResult,
    FundamentalScore,
    PriceBar,
    RawCompanyRow,
    SetupLifecycleEpisode,
    SetupLifecycleEvaluationRun,
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
from app.services.setup_lifecycle.purge_service import (
    SetupLifecyclePurgeExecuteRequest,
    SetupLifecyclePurgeService,
)
from app.services.setup_lifecycle.repository import PurgeScope, SetupLifecycleRepository
from app.settings import Settings

DERIVED_MODELS = (
    SetupSignalSnapshot,
    SetupLifecycleEpisode,
    SetupLifecycleEvent,
    SignalChangeEvent,
    SignalAlertEvent,
    SetupLifecycleEvaluationRun,
)
UPSTREAM_MODELS = (
    UploadRun,
    RawCompanyRow,
    FundamentalScore,
    TechnicalScore,
    CombinedResult,
    PriceBar,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audited dev-only SLSE clean rebuild")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--database-name", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--progress", type=Path, required=True)
    args = parser.parse_args()

    if not args.execute:
        raise SystemExit("refusing rebuild without --execute")
    settings = Settings()
    config = load_setup_lifecycle_config()
    engine = create_engine(settings.database_url)
    if engine.url.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("dev rebuild is restricted to local PostgreSQL")
    if engine.url.database != args.database_name or args.database_name != "swinglens":
        raise SystemExit("explicit --database-name swinglens confirmation is required")
    if not settings.debug:
        raise SystemExit("dev rebuild requires debug/dev settings")
    if config.engine.enabled:
        raise SystemExit("SLSE engine must remain disabled during rebuild")

    report: dict[str, object] = {
        "operation": "SLSE dev/QA clean chronological rebuild",
        "database": {"host": engine.url.host, "name": engine.url.database},
        "started_at": datetime.now(UTC).isoformat(),
        "engine_version": config.engine.version,
        "config_version": config.engine.config_version,
        "config_hash": config.config_hash,
        "engine_enabled": config.engine.enabled,
    }
    args.progress.parent.mkdir(parents=True, exist_ok=True)

    with Session(engine) as db:
        upstream_before = _counts(db, UPSTREAM_MODELS)
        report["upstream_before"] = upstream_before
        report["before"] = _metrics(db)

        purge_config = replace(
            config,
            retention=replace(config.retention, purge_enabled=True),
        )
        repository = SetupLifecycleRepository(config=purge_config)
        purge = SetupLifecyclePurgeService(config=purge_config, repository=repository)
        preview = purge.preview(db, PurgeScope())
        report["purge_preview"] = {
            "counts": preview.counts,
            "token_hash": repository.hash_token(preview.token),
        }
        deleted = purge.execute(
            db,
            SetupLifecyclePurgeExecuteRequest(
                preview=preview,
                confirmation_token=preview.token,
                requester="codex-slse-closure",
                reason=(
                    "Authorized dev/QA rebuild after 25-scenario golden and natural "
                    "multi-date certification passed"
                ),
            ),
        )
        db.commit()
        report["deleted"] = deleted
        empty_counts = _counts(db, DERIVED_MODELS)
        if any(empty_counts.values()):
            raise SystemExit(f"derived-table purge incomplete: {empty_counts}")

        runs = list(
            db.scalars(
                select(UploadRun)
                .where(UploadRun.status == "COMPLETED")
                .where(UploadRun.processed_at.is_not(None))
                .order_by(UploadRun.processed_at, UploadRun.id)
            )
        )
        report["source_run_ids"] = [row.id for row in runs]
        evaluator = SetupLifecycleEvaluationService(config=config)
        results: list[dict[str, object]] = []
        started = time.perf_counter()
        for index, run in enumerate(runs, start=1):
            run_started = time.perf_counter()
            result = evaluator.evaluate_run(
                db,
                run.id,
                requester="slse-dev-history-rebuild",
            )
            db.commit()
            item = {
                "ordinal": index,
                "run_id": run.id,
                "processed_at": run.processed_at.isoformat(),
                "duration_seconds": round(time.perf_counter() - run_started, 6),
                **result.as_dict(),
            }
            results.append(item)
            _write_json(
                args.progress,
                {
                    "status": "RUNNING",
                    "completed_runs": index,
                    "total_runs": len(runs),
                    "last": item,
                    "elapsed_seconds": round(time.perf_counter() - started, 6),
                },
            )
            print(
                f"[{index}/{len(runs)}] run={run.id} "
                f"captured={result.snapshots_captured} failed={result.failed} "
                f"seconds={item['duration_seconds']}",
                flush=True,
            )
            if result.failed:
                raise SystemExit(f"rebuild failed for source run {run.id}")

        report["duration_seconds"] = round(time.perf_counter() - started, 6)
        report["evaluations"] = results
        report["after"] = _metrics(db)
        report["upstream_after"] = _counts(db, UPSTREAM_MODELS)
        report["upstream_preserved"] = report["upstream_after"] == upstream_before
        report["defect_signatures_after"] = _defect_signatures(db)
        expected_zero = report["defect_signatures_after"]
        if not report["upstream_preserved"] or any(expected_zero.values()):
            raise SystemExit("rebuild verification failed")

    engine.dispose()
    report["completed_at"] = datetime.now(UTC).isoformat()
    report["status"] = "PASS"
    _write_json(args.output, report)
    _write_json(
        args.progress,
        {
            "status": "PASS",
            "completed_runs": len(report["source_run_ids"]),
            "total_runs": len(report["source_run_ids"]),
            "duration_seconds": report["duration_seconds"],
        },
    )
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


def _counts(db: Session, models: tuple[type, ...]) -> dict[str, int]:
    return {
        model.__tablename__: db.scalar(select(func.count()).select_from(model)) or 0
        for model in models
    }


def _metrics(db: Session) -> dict[str, object]:
    snapshots = list(db.scalars(select(SetupSignalSnapshot)))
    episodes = list(db.scalars(select(SetupLifecycleEpisode)))
    alerts = list(
        db.execute(
            select(SignalAlertEvent, SignalAlertRule.rule_id).join(
                SignalAlertRule,
                SignalAlertEvent.alert_rule_id == SignalAlertRule.id,
            )
        )
    )
    return {
        "counts": _counts(db, DERIVED_MODELS),
        "engine_config_distribution": _counter(
            (row.engine_version, row.config_version) for row in snapshots
        ),
        "coverage_distribution": _counter(str(row.required_feature_coverage) for row in snapshots),
        "confidence_distribution": _counter(
            str(row.confidence_score) if row.confidence_score is not None else "NONE"
            for row in snapshots
        ),
        "actionability_distribution": _counter(
            row.actionability_candidate or "NONE" for row in snapshots
        ),
        "state_distribution": _counter(
            row.lifecycle_state_candidate or "NONE" for row in snapshots
        ),
        "episode_state_distribution": _counter(row.current_state for row in episodes),
        "alert_type_severity_distribution": _counter(
            (rule_id, alert.severity) for alert, rule_id in alerts
        ),
    }


def _defect_signatures(db: Session) -> dict[str, int]:
    snapshots = list(db.scalars(select(SetupSignalSnapshot)))
    changes = list(db.scalars(select(SignalChangeEvent)))
    alerts = list(db.scalars(select(SignalAlertEvent)))
    return {
        "false_missing_required_close_price": sum(
            row.close_price is not None
            and "MISSING_REQUIRED_CLOSE_PRICE" in (row.warning_flags_json or ())
            for row in snapshots
        ),
        "fabricated_perfect_confidence": sum(
            ((row.evidence_json or {}).get("confidence_score") == 100)
            and ((row.evidence_json or {}).get("current_confidence_score") not in {None, 100})
            for row in changes
        ),
        "missing_change_confidence_evidence": sum(
            (row.evidence_json or {}).get("confidence_score") is None for row in changes
        ),
        "incorrect_rank_direction": sum(
            row.signal_key == "sector_rank"
            and row.rank_delta is not None
            and row.normalized_delta is not None
            and row.rank_delta != row.normalized_delta
            for row in changes
        ),
        "initial_gate_blocked": sum(
            (row.evidence_json or {}).get("source") == "actionability_change"
            and not (row.evidence_json or {}).get("semantic_key")
            for row in alerts
        ),
    }


def _counter(values) -> dict[str, int]:
    return dict(
        sorted(
            Counter(
                " | ".join(map(str, value)) if isinstance(value, tuple) else str(value)
                for value in values
            ).items()
        )
    )


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
