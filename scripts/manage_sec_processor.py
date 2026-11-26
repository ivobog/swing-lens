from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.db import engine
from app.models.ceri_tables import CeriSecProcessorRelease
from app.services.ceri.sec.processor_lifecycle import (
    certify_processor,
    lifecycle_state,
    promote_processor,
    register_deployed_processor,
)
from app.services.ceri.sec.processor_rollback import (
    PROMOTION_RECEIPT,
    certified_run_tickers,
    seal_prepared_scope,
)
from app.services.ceri.sec.processor_signature import (
    requires_prepared_scope,
    sec_guidance_processor_signature,
)
from app.services.ceri.sec.readiness_diagnostics import diagnose_sec_readiness
from app.services.runtime_mutation_authority import RuntimeMutationAuthority
from scripts.ops.sec_processor_rollback import ROOT, _checkout_identity


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
    promote_parser.add_argument("--rollback-run-id", type=int)
    transition_parser = subparsers.add_parser("transition-status")
    transition_parser.add_argument("--candidate", required=True)
    args = parser.parse_args()

    with Session(engine) as db:
        if args.command == "transition-status":
            release = db.get(CeriSecProcessorRelease, args.candidate)
            receipt = (
                release.certification_evidence_json.get(PROMOTION_RECEIPT) if release else None
            )
            if receipt is None:
                raise SystemExit("No recorded transition for this candidate.")
            print(
                json.dumps(
                    {"processor": lifecycle_state(db).as_dict(), "promotion": receipt}, indent=2
                )
            )
            return 0
        register_deployed_processor(
            db,
            authority=RuntimeMutationAuthority.normal("cli.sec_processor.register"),
        )
        if args.command == "status":
            report = {"processor": lifecycle_state(db).as_dict()}
            if args.run_id is not None:
                tickers = certified_run_tickers(db, args.run_id)
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
            tickers = certified_run_tickers(db, args.run_id)
            readiness = diagnose_sec_readiness(
                db,
                tickers=tickers,
                processor_signature=signature,
            )
            if not readiness.complete:
                raise SystemExit(
                    f"Cannot certify: readiness is {readiness.ready_tickers}/"
                    f"{readiness.requested_tickers}."
                )
            preparation_scope = {}
            if requires_prepared_scope(signature):
                digest = evidence.get("preparation_scope_sha256")
                preparation_scope = {"preparation_scope": seal_prepared_scope(
                    db, tickers=tickers, signature=signature, preparation_sha256=digest,
                )}
            certify_processor(
                db,
                processor_signature=signature,
                evidence={
                    "run_id": args.run_id,
                    "scope_tickers": list(tickers),
                    "certification_file": str(args.evidence.resolve()),
                    "certification": evidence,
                    **preparation_scope,
                    "readiness": readiness.as_dict(include_tickers=False),
                },
                actor=args.actor,
            )
            db.commit()
            print(json.dumps(lifecycle_state(db).as_dict(), indent=2, sort_keys=True))
            return 0

        if (
            args.rollback_run_id is not None
            and args.signature != sec_guidance_processor_signature()
        ):
            raise SystemExit(
                "Recoverable promotion must match the processor in the current runtime."
            )
        release = promote_processor(
            db,
            processor_signature=args.signature,
            actor=args.actor,
            rollback_run_id=args.rollback_run_id,
            rollback_candidate_git=(
                _checkout_identity(ROOT) if args.rollback_run_id is not None else None
            ),
        )
        db.commit()
        result = lifecycle_state(db).as_dict()
        if args.rollback_run_id is not None:
            result["promotion"] = release.certification_evidence_json.get(PROMOTION_RECEIPT)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
