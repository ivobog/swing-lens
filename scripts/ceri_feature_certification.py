"""Admit the bounded, feature-only CERI certification workflow.

This command creates durable certification authority and a coordinator root. It
never calls a provider directly and it cannot request scoring or continuation.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.certification_session_discovery import (  # noqa: E402
    bind_certification_session_environment,
    discover_active_certification_session,
)
from app.settings import Settings, get_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Admit an isolated two-ticker CERI feature-only certification root."
    )
    parser.add_argument("--tickers", nargs="+", required=True)
    parser.add_argument("--provider-datasets", nargs="+", required=True)
    parser.add_argument("--checkpoint-interval", required=True, type=int)
    parser.add_argument("--stop-boundary", required=True)
    parser.add_argument("--request-key", required=True)
    parser.add_argument("--cutoff-at", help="Optional timezone-aware ISO-8601 cutoff.")
    parser.add_argument("--requested-by")
    args = parser.parse_args()
    cutoff_at = (
        datetime.fromisoformat(args.cutoff_at.replace("Z", "+00:00"))
        if args.cutoff_at
        else None
    )
    if cutoff_at is not None and cutoff_at.utcoffset() is None:
        parser.error("--cutoff-at must include a timezone offset")

    discovery_settings = Settings()
    discovery_engine = create_engine(discovery_settings.database_url, pool_pre_ping=True)
    try:
        with Session(discovery_engine) as discovery_db:
            binding = discover_active_certification_session(
                discovery_db,
                repo_root=ROOT,
                settings=discovery_settings,
                environment=os.environ,
            )
    finally:
        discovery_engine.dispose()
    bind_certification_session_environment(binding, environment=os.environ)
    get_settings.cache_clear()

    from app.db import SessionLocal
    from app.services.ceri.feature_certification_workflow import (
        CeriFeatureCertificationRequest,
        admit_ceri_feature_certification,
    )

    with SessionLocal() as db:
        try:
            admitted = admit_ceri_feature_certification(
                db,
                CeriFeatureCertificationRequest(
                    tickers=tuple(args.tickers),
                    provider_datasets=tuple(args.provider_datasets),
                    checkpoint_interval=args.checkpoint_interval,
                    cutoff_at=cutoff_at,
                    stop_boundary=args.stop_boundary,
                    request_key=args.request_key,
                    requested_by=args.requested_by,
                ),
                settings=get_settings(),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
    print(
        json.dumps(
            {
                "status": "ADMITTED",
                "upload_run_id": admitted.upload_run_id,
                "pipeline_run_id": admitted.pipeline_run_id,
                "root_job_id": admitted.root_job_id,
                "workflow_key": admitted.workflow_key,
                "stop_boundary": "FEATURE_ONLY",
                "runtime_instance_id": binding.runtime_instance_id,
                "runtime_git_sha": binding.git_sha,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
