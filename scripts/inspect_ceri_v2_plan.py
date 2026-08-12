from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import SessionLocal  # noqa: E402
from app.models.tables import RawCompanyRow  # noqa: E402
from app.services.ceri.batched_workflow import (  # noqa: E402
    build_ceri_batched_workflow_plan,
)
from app.services.ceri.config import load_ceri_config  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect a read-only CERI v2 batch plan.")
    parser.add_argument("run_id", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with SessionLocal() as db:
        tickers = list(
            db.scalars(
                select(RawCompanyRow.ticker)
                .where(RawCompanyRow.run_id == args.run_id)
                .order_by(RawCompanyRow.ticker)
            )
        )
    plan = build_ceri_batched_workflow_plan(
        run_id=args.run_id,
        tickers=tickers,
        config_hash=load_ceri_config().config_hash,
    )
    report = {
        "run_id": args.run_id,
        "ticker_count": len(set(tickers)),
        "workflow_key": plan.workflow_key,
        "provider_batches": plan.provider_batches,
        "normalization_batches": plan.normalization_batches,
        "feature_batches": plan.feature_batches,
        "initial_job_count": plan.initial_job_count,
        "expected_total_job_count": plan.expected_total_job_count,
        "jobs": [
            {
                "job_type": spec.job_type,
                "request_key": spec.request_key,
                "priority": spec.priority,
                "ticker_count": len(spec.payload.get("tickers") or []),
            }
            for spec in plan.jobs
        ],
    }
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(args.output.resolve())
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
