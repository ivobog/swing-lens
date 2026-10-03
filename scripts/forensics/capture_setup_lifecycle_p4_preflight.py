"""Read-only reconciliation preflight over retained setup snapshots."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.models.tables import SetupSignalSnapshot
from app.services.setup_lifecycle.episode_service import (
    SetupLifecycleEpisodeService,
    normalized_snapshot_from_row,
)
from app.services.setup_lifecycle.repository import SetupLifecycleRepository


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repository = SetupLifecycleRepository()
    service = SetupLifecycleEpisodeService(repository=repository)

    with SessionLocal() as db:
        snapshots = list(
            db.scalars(
                select(SetupSignalSnapshot)
                .where(SetupSignalSnapshot.run_id == args.run_id)
                .order_by(SetupSignalSnapshot.ticker, SetupSignalSnapshot.timeframe)
            )
        )
        keys = {(row.ticker, row.timeframe) for row in snapshots}
        episodes_by_key = repository.lifecycle_episodes_for_keys(db, keys)
        cutoffs = {(row.ticker, row.timeframe): row.data_as_of_date for row in snapshots}
        prior_by_key = repository.canonical_snapshot_histories_before(
            db,
            cutoffs=cutoffs,
            limit=service.config.episodes.history_window_sessions,
        )
        rows = []
        unique_active_ids: set[int] = set()
        for snapshot in snapshots:
            key = (snapshot.ticker, snapshot.timeframe)
            active = tuple(row for row in episodes_by_key.get(key, ()) if row.status == "ACTIVE")
            unique_active_ids.update(row.id for row in active)
            plan = service.plan_reconciliation(
                db,
                snapshot,
                prior_snapshots=tuple(
                    normalized_snapshot_from_row(row) for row in prior_by_key.get(key, ())
                ),
                preloaded_episodes=active,
            )
            rows.append(
                {
                    **plan.as_dict(),
                    "active_families": [row.setup_family for row in active],
                    "multiple_active_families": len(active) > 1,
                    "classification": (
                        "NO_CURRENT_FAMILY"
                        if plan.current_family is None
                        else "DISPLACED_FAMILY"
                        if plan.displaced_episode_ids
                        else "SAME_FAMILY"
                        if plan.retained_episode_id is not None
                        else "NEW_FAMILY"
                    ),
                }
            )
        classifications = Counter(row["classification"] for row in rows)
        report = {
            "run_id": args.run_id,
            "current_setup_snapshots": len(snapshots),
            "pre_existing_active_episodes": len(unique_active_ids),
            "same_family": classifications["SAME_FAMILY"],
            "displaced_family": classifications["DISPLACED_FAMILY"],
            "no_current_family": classifications["NO_CURRENT_FAMILY"],
            "new_family": classifications["NEW_FAMILY"],
            "multiple_family": sum(row["multiple_active_families"] for row in rows),
            "planned_reconciliation_actions": sum(
                len(row["displaced_episode_ids"]) for row in rows
            ),
            "rows": rows,
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "rows"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
