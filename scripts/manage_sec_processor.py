from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import engine
from app.models.tables import RawCompanyRow
from app.services.ceri.sec.processor_lifecycle import (
    certify_processor,
    lifecycle_state,
    promote_processor,
    register_deployed_processor,
)
from app.services.ceri.sec.processor_signature import sec_guidance_processor_signature
from app.services.ceri.sec.readiness_diagnostics import diagnose_sec_readiness


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect, certify, and promote SEC processors.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--run-id", type=int)
    certify_parser = subparsers.add_parser("certify")
    certify_parser.add_argument("--run-id", required=True, type=int)
    certify_parser.add_argument("--evidence", required=True, type=Path)
    certify_parser.add_argument("--actor", required=True)
    promote_parser = subparsers.add_parser("promote")
    promote_parser.add_argument("--signature", default=sec_guidance_processor_signature())
    promote_parser.add_argument("--actor", required=True)
    args = parser.parse_args()

    with Session(engine) as db:
        register_deployed_processor(db)
        if args.command == "status":
            report = {"processor": lifecycle_state(db).as_dict()}
            if args.run_id is not None:
                tickers = _run_tickers(db, args.run_id)
                report["readiness"] = diagnose_sec_readiness(
                    db,
                    tickers=tickers,
                    processor_signature=sec_guidance_processor_signature(),
                ).as_dict()
            db.rollback()
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0

        if args.command == "certify":
            evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
            signature = sec_guidance_processor_signature()
            if evidence.get("processor_signature") != signature:
                raise SystemExit("Certification evidence signature does not match deployed code.")
            if not evidence.get("passed"):
                raise SystemExit("Certification evidence did not pass all checks.")
            readiness = diagnose_sec_readiness(
                db,
                tickers=_run_tickers(db, args.run_id),
                processor_signature=signature,
            )
            if not readiness.complete:
                raise SystemExit(
                    f"Cannot certify: readiness is {readiness.ready_tickers}/"
                    f"{readiness.requested_tickers}."
                )
            certify_processor(
                db,
                processor_signature=signature,
                evidence={
                    "run_id": args.run_id,
                    "certification_file": str(args.evidence.resolve()),
                    "certification": evidence,
                    "readiness": readiness.as_dict(include_tickers=False),
                },
                actor=args.actor,
            )
            db.commit()
            print(json.dumps(lifecycle_state(db).as_dict(), indent=2, sort_keys=True))
            return 0

        promote_processor(
            db,
            processor_signature=args.signature,
            actor=args.actor,
        )
        db.commit()
        print(json.dumps(lifecycle_state(db).as_dict(), indent=2, sort_keys=True))
        return 0


def _run_tickers(db: Session, run_id: int) -> tuple[str, ...]:
    return tuple(
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


if __name__ == "__main__":
    raise SystemExit(main())
