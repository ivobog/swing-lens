"""Replay reviewed SEC identity rejections into a new processing run."""

import argparse
import json

from app.db import SessionLocal
from app.models.ceri_tables import CeriProcessingRun
from app.services.ceri.normalization_service import CeriNormalizationService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processing-run", required=True, type=int)
    parser.add_argument("--ingestion-run", required=True, type=int)
    parser.add_argument("--actor", required=True)
    args = parser.parse_args()
    with SessionLocal.begin() as db:
        original = db.get(CeriProcessingRun, args.processing_run)
        if original is None:
            parser.error("Processing run does not exist")
        outcome = CeriNormalizationService().replay_sec_identity(
            db,
            original=original,
            ingestion_run_id=args.ingestion_run,
            actor=args.actor,
        )
    print(json.dumps(outcome.as_dict()))


if __name__ == "__main__":
    main()
