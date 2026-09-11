from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.tables import MarketCalculationContext
from app.services.market_calculation_context_service import cutoff_from_row
from app.services.setup_lifecycle.repository import SetupLifecycleRepository
from app.services.setup_lifecycle.snapshot_builder import (
    SetupLifecycleSnapshotBuilder,
    build_run_context_snapshots,
)
from app.services.setup_lifecycle.source_loader import SetupLifecycleSourceLoader
from app.services.setup_lifecycle.transition_candidate_service import (
    TransitionCandidateDiscoveryService,
)
from app.services.transition_preflight_plan_service import (
    reconstruct_transition_decision_manifests,
)
from app.settings import get_settings

RUN_ID = 153
CONTEXT_ID = 6
TICKER = "TBLA"


def main() -> int:
    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        with Session(engine) as db:
            db.connection().exec_driver_sql(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
            )
            context_row = db.get(MarketCalculationContext, CONTEXT_ID)
            if context_row is None:
                raise RuntimeError(f"MarketCalculationContext {CONTEXT_ID} was not found")
            market_cutoff = cutoff_from_row(context_row)
            preflight = TransitionCandidateDiscoveryService().discover_for_run(
                db,
                RUN_ID,
                market_cutoff=market_cutoff,
                tickers={TICKER},
            )[0]
            repository = SetupLifecycleRepository()
            run_context = SetupLifecycleSourceLoader().load_run_context(
                db,
                RUN_ID,
                market_cutoff=market_cutoff,
                tickers={TICKER},
            )
            built_rows = build_run_context_snapshots(
                db,
                run_context,
                builder=SetupLifecycleSnapshotBuilder(),
                repository=repository,
            )
            production = reconstruct_transition_decision_manifests(
                db,
                market_cutoff=market_cutoff,
                built_rows=built_rows,
                repository=repository,
                tickers={TICKER},
            )[TICKER]
            preflight_manifest = preflight.decision_manifest or {}
            production_manifest = production["decision_manifest"]
            manifest_equal = preflight_manifest == production_manifest
            preflight_key = preflight_manifest.get("candidate", {}).get("selection_key")
            production_key = production_manifest.get("candidate", {}).get("selection_key")
            report = {
                "run_id": RUN_ID,
                "context_id": CONTEXT_ID,
                "cutoff_at": market_cutoff.cutoff_at,
                "latest_completed_session": market_cutoff.latest_completed_session,
                "ticker": TICKER,
                "preflight_selection_key": preflight_key,
                "fixed_production_loader_selection_key": production_key,
                "preflight_technical_fingerprint": preflight_manifest.get("technical", {}).get(
                    "fingerprint"
                ),
                "production_technical_fingerprint": production_manifest.get("technical", {}).get(
                    "fingerprint"
                ),
                "preflight_manifest_fingerprint": preflight.decision_manifest_fingerprint,
                "production_manifest_fingerprint": production["decision_manifest_fingerprint"],
                "historical_persisted_manifest_equal": manifest_equal,
                "historical_divergence_reproduced": (
                    not manifest_equal
                    and preflight_key == "TBLA/1d/2026-08-03"
                    and production_key == "TBLA/1d/2026-08-03"
                    and preflight_manifest.get("technical", {}).get("fingerprint")
                    != production_manifest.get("technical", {}).get("fingerprint")
                ),
            }
            db.rollback()
    finally:
        engine.dispose()
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0 if report["historical_divergence_reproduced"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
