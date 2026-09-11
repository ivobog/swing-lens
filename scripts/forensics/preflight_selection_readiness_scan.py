from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.models.tables import RawCompanyRow, UploadRun
from app.services.market_calculation_context_service import (
    prospective_pipeline_market_context,
)
from app.services.setup_lifecycle.transition_candidate_service import (
    TransitionCandidateDiscoveryService,
)
from app.services.technical_score_service import preview_run_technicals
from app.settings import get_settings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cutoff-at", required=True)
    args = parser.parse_args()
    cutoff_at = datetime.fromisoformat(args.cutoff_at.replace("Z", "+00:00"))
    if cutoff_at.tzinfo is None:
        raise ValueError("--cutoff-at must be timezone-aware")

    engine = create_engine(get_settings().database_url)
    with Session(engine) as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        baseline = _business_counts(db)
        latest = _latest_completed_run_by_ticker(db)
        grouped: dict[int, set[str]] = defaultdict(set)
        for ticker, run_id in latest.items():
            grouped[run_id].add(ticker)

        market_cutoff = prospective_pipeline_market_context(cutoff_at=cutoff_at)
        technical_rows = preview_run_technicals(
            db,
            max(grouped),
            tickers=sorted(latest),
            market_cutoff=market_cutoff,
        )
        cached_previewer = _CachedTechnicalPreviewer(technical_rows)
        results = []
        service = TransitionCandidateDiscoveryService(
            technical_previewer=cached_previewer,
            full_universe_technical_preview=False,
        )
        for index, (run_id, tickers) in enumerate(sorted(grouped.items()), start=1):
            print(
                json.dumps(
                    {
                        "progress": index,
                        "groups": len(grouped),
                        "run_id": run_id,
                        "selected_tickers": len(tickers),
                    }
                ),
                flush=True,
            )
            results.extend(
                service.discover_for_run(
                    db,
                    run_id,
                    market_cutoff=market_cutoff,
                    tickers=tickers,
                )
            )

        if db.new or db.dirty or db.deleted:
            raise RuntimeError("readiness scan changed ORM business state")
        after = _business_counts(db)
        if after != baseline:
            raise RuntimeError("readiness scan changed persisted business counts")

        reason_counts = Counter(row.reason for row in results)
        payload = {
            "cutoff_at": market_cutoff.cutoff_at.isoformat(),
            "latest_completed_session": market_cutoff.latest_completed_session.isoformat(),
            "calendar_version": market_cutoff.calendar_version,
            "bar_readiness_version": market_cutoff.bar_readiness_version,
            "universe_size": len(results),
            "high_candidates": sum(row.confidence == "HIGH" for row in results),
            "medium_candidates": sum(row.confidence == "MEDIUM" for row in results),
            "low_candidates": sum(row.confidence == "LOW" for row in results),
            "predicted_same_session_replacements": sum(
                row.predicted_pointer_advance for row in results
            ),
            "predicted_cross_session_current_advances": sum(
                row.predicted_current_state_advance for row in results
            ),
            "predicted_legitimate_transitions": sum(
                row.predicted_pointer_advance or row.predicted_current_state_advance
                for row in results
            ),
            "selection_key_blockers": reason_counts[
                "NEW_KEY_INITIALIZATION_DOES_NOT_ADVANCE_CURRENT_STATE"
            ],
            "legacy_provenance_blockers": 0,
            "overconstraint_blockers": 0,
            "reason_counts": dict(sorted(reason_counts.items())),
            "high_candidate_tickers": sorted(
                row.ticker for row in results if row.confidence == "HIGH"
            ),
            "production_business_writes": False,
            "baseline_counts": baseline,
            "after_counts": after,
        }
        print("FINAL " + json.dumps(payload, sort_keys=True), flush=True)
        db.rollback()


def _latest_completed_run_by_ticker(db: Session) -> dict[str, int]:
    rows = db.execute(
        select(RawCompanyRow.ticker, RawCompanyRow.run_id)
        .join(UploadRun, UploadRun.id == RawCompanyRow.run_id)
        .where(UploadRun.status == "COMPLETED")
        .order_by(RawCompanyRow.ticker, RawCompanyRow.run_id.desc())
    )
    latest: dict[str, int] = {}
    for ticker, run_id in rows:
        if ticker and ticker.strip():
            latest.setdefault(ticker.upper(), int(run_id))
    return latest


def _business_counts(db: Session) -> dict[str, int]:
    row = db.execute(
        text(
            "SELECT "
            "(SELECT count(*) FROM upload_runs), "
            "(SELECT count(*) FROM pipeline_runs), "
            "(SELECT count(*) FROM background_jobs), "
            "(SELECT count(*) FROM market_calculation_contexts), "
            "(SELECT count(*) FROM technical_scores), "
            "(SELECT count(*) FROM setup_signal_snapshots), "
            "(SELECT count(*) FROM setup_signal_snapshot_current_selections), "
            "(SELECT count(*) FROM setup_signal_snapshot_selection_events)"
        )
    ).one()
    keys = (
        "runs",
        "pipelines",
        "jobs",
        "contexts",
        "technical_scores",
        "lifecycle_snapshots",
        "session_canonical_pointers",
        "selection_events",
    )
    return dict(zip(keys, (int(value) for value in row), strict=True))


class _CachedTechnicalPreviewer:
    def __init__(self, rows) -> None:
        self.rows = {row.ticker.upper(): row for row in rows}

    def __call__(self, _db, _run_id, *, tickers=None, market_cutoff=None):
        _ = market_cutoff
        selected = set(tickers or self.rows)
        return [self.rows[ticker] for ticker in sorted(selected) if ticker in self.rows]


if __name__ == "__main__":
    main()
