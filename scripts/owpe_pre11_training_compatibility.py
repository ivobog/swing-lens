"""Dry-run or explicitly persist a scoped pre-1.1 OWPE compatibility manifest."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.models.tables import WinnerOutcomeDefinition
from app.services.winner_probability.config import load_winner_probability_config
from app.services.winner_probability.pre11_compatibility_service import (
    TRAINING_FAMILY,
    Pre11CompatibilityScope,
    Pre11CompatibilityService,
    Pre11CompatibilityWriteService,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Classify immutable pre-1.1 snapshots for OWPE 1.1 training"
    )
    parser.add_argument("mode", choices=("dry-run", "write"))
    parser.add_argument("--training-family", required=True)
    parser.add_argument("--outcome-definition-id", required=True, type=int)
    parser.add_argument("--cutoff-at", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--manifest-path", required=True, type=Path)
    parser.add_argument("--request-key")
    parser.add_argument("--actor")
    parser.add_argument("--approve-write", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    scope = Pre11CompatibilityScope(
        training_family=args.training_family,
        outcome_definition_id=args.outcome_definition_id,
        cutoff_at=datetime.fromisoformat(args.cutoff_at),
        start_date=date.fromisoformat(args.start_date),
        end_date=date.fromisoformat(args.end_date),
    )
    if scope.training_family != TRAINING_FAMILY:
        raise SystemExit("unsupported training family")
    config = load_winner_probability_config()
    with SessionLocal() as db:
        outcome = db.scalar(
            select(WinnerOutcomeDefinition).where(
                WinnerOutcomeDefinition.id == scope.outcome_definition_id
            )
        )
        if outcome is None:
            raise SystemExit("outcome definition not found")
        result = Pre11CompatibilityService().dry_run(
            db, scope=scope, outcome_definition=outcome, config=config
        )
        if args.mode == "dry-run":
            # Deliberately no db.add(), flush(), commit(), or mutation.
            args.manifest_path.parent.mkdir(parents=True, exist_ok=True)
            args.manifest_path.write_text(
                json.dumps(result.manifest_payload(), indent=2, sort_keys=True),
                encoding="utf-8",
            )
            print(json.dumps(result.manifest_payload(), indent=2, sort_keys=True))
            return 0

        if not args.approve_write or not args.request_key or not args.actor:
            raise SystemExit(
                "write requires --approve-write, --request-key, --actor, and the reviewed manifest"
            )
        decisions, replays = Pre11CompatibilityWriteService().persist_decisions_and_replays(
            db,
            dry_run=result,
            reviewed_manifest_path=args.manifest_path,
            request_key=args.request_key,
            approve_write=True,
            actor=args.actor,
            outcome_definition=outcome,
            config=config,
        )
        db.commit()
        print(json.dumps({"decisions": len(decisions), "replays": len(replays)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
