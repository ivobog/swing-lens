"""Activate the exact reviewed pre-1.1 L5 cohort and one controlled rescore."""

from __future__ import annotations

import argparse
import json
from datetime import datetime

from app.db import SessionLocal
from app.models.tables import WinnerOutcomeDefinition, WinnerPredictionSnapshot
from app.services.winner_probability.config import load_winner_probability_config
from app.services.winner_probability.pre11_activation_service import (
    Pre11L5ActivationService,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("preview", "write"))
    parser.add_argument("--prediction-id", required=True, type=int)
    parser.add_argument("--outcome-definition-id", required=True, type=int)
    parser.add_argument("--training-cutoff-at", required=True)
    parser.add_argument("--request-key", required=True)
    parser.add_argument("--reviewed-manifest-hash", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--approve-write", action="store_true")
    args = parser.parse_args()
    if args.mode == "write" and not args.approve_write:
        raise SystemExit("--approve-write is required")
    config = load_winner_probability_config()
    with SessionLocal() as db:
        prediction = db.get(WinnerPredictionSnapshot, args.prediction_id)
        outcome = db.get(WinnerOutcomeDefinition, args.outcome_definition_id)
        if prediction is None or outcome is None:
            raise SystemExit("prediction or outcome definition was not found")
        result = Pre11L5ActivationService().activate(
            db,
            prediction=prediction,
            outcome_definition=outcome,
            training_cutoff_at=datetime.fromisoformat(args.training_cutoff_at),
            config=config,
            request_key=args.request_key,
            expected_reviewed_manifest_hash=args.reviewed_manifest_hash,
            actor=args.actor,
            approve_write=True,
        )
        if args.mode == "write":
            db.commit()
        else:
            db.rollback()
        print(
            json.dumps(
                {
                    "cohort_statistic_id": result.cohort_statistic.id,
                    "estimate_id": result.estimate.id,
                    "sample_n": len(result.evidence),
                    "reviewed_manifest_hash": result.reviewed_manifest_hash,
                    "evidence_manifest_hash": result.evidence_manifest_hash,
                    "database_write": args.mode == "write",
                },
                indent=2,
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
