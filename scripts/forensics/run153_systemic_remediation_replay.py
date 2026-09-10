from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.database_safety import assert_disposable_database
from app.models.tables import (
    MarketCalculationContext,
    PriceBar,
    SetupSignalSnapshot,
    SetupSignalSnapshotCurrentSelection,
    SetupSignalSnapshotSelectionEvent,
)
from app.services.market_calculation_context_service import cutoff_from_row
from app.services.pipeline_service import MarketDataPolicy, start_pipeline
from app.services.setup_lifecycle.repository import SetupLifecycleRepository
from app.services.setup_lifecycle.snapshot_builder import (
    SetupLifecycleSnapshotBuilder,
    SetupLifecycleSnapshotCaptureService,
    build_run_context_snapshots,
)
from app.services.setup_lifecycle.source_loader import SetupLifecycleSourceLoader
from app.services.technical_score_service import score_run_technicals
from app.services.transition_preflight_plan_service import (
    TransitionPreflightError,
    create_transition_preflight_plan,
    verify_transition_decision_manifests_before_mutation,
)
from app.settings import get_settings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-name", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--cutoff-at", default="2026-09-10T08:13:04.357207+00:00")
    args = parser.parse_args()
    database_url = make_url(get_settings().database_url).set(database=args.database_name)
    rendered_url = database_url.render_as_string(hide_password=False)
    assert_disposable_database(rendered_url)
    engine = create_engine(database_url, pool_pre_ping=True)
    report: dict[str, object] = {"run_id": args.run_id}
    try:
        with Session(engine, expire_on_commit=False) as db:
            plan = create_transition_preflight_plan(
                db,
                upload_run_id=args.run_id,
                idempotency_key=f"run153-systemic-replay:{args.run_id}:{uuid4().hex}",
                cutoff_at=datetime.fromisoformat(args.cutoff_at).astimezone(UTC),
                expires_in=timedelta(days=1),
            )
            db.commit()
            report["preflight_plan_id"] = plan.id
            report["cases"] = len(plan.tickers_json)
            report["confidence"] = dict(
                Counter(row["confidence"] for row in plan.candidate_results_json)
            )
            report["transition_types"] = dict(
                Counter(
                    "NEW_SESSION_CANONICAL_INITIALIZATION"
                    if row["predicted_current_state_advance"]
                    else "SAME_SESSION_REPLACEMENT"
                    if row["predicted_pointer_advance"]
                    else "NON_TRANSITION"
                    for row in plan.candidate_results_json
                )
            )
            pipeline = start_pipeline(
                db,
                args.run_id,
                requested_by="run153-offline-certification",
                ceri_run_capture_enabled=False,
                ceri_provider_ingest_enabled=False,
                setup_lifecycle_pipeline_step_enabled=True,
                market_data_policy=MarketDataPolicy.ALLOW_CACHE_FALLBACK,
                transition_preflight_plan_id=plan.id,
            )
            db.commit()
            report["pipeline_id"] = pipeline.id
            market_cutoff = cutoff_from_row(
                db.get(MarketCalculationContext, plan.market_calculation_context_id)
            )
            report["technical_scores"] = len(
                score_run_technicals(db, args.run_id, market_cutoff=market_cutoff)
            )
            db.flush()
            loader = SetupLifecycleSourceLoader()
            builder = SetupLifecycleSnapshotBuilder()
            repository = SetupLifecycleRepository()
            run_context = loader.load_run_context(db, args.run_id, market_cutoff=market_cutoff)
            built_rows = build_run_context_snapshots(
                db, run_context, builder=builder, repository=repository
            )
            verify_transition_decision_manifests_before_mutation(
                db,
                upload_run_id=args.run_id,
                market_cutoff=market_cutoff,
                built_rows=built_rows,
                repository=repository,
            )
            report["equivalent_cases"] = len(built_rows)
            report["manifest_equivalence_failures"] = 0

            before = _lifecycle_counts(db)
            target = next(
                bar
                for ticker_context in run_context.tickers
                for bar in ticker_context.price_bars
                if bar.id is not None
            )
            persistent = db.get(PriceBar, target.id)
            persistent.volume = (persistent.volume or 0) + 1
            persistent.data_hash = "run153-injected-material-change"
            db.flush()
            try:
                SetupLifecycleSnapshotCaptureService().capture_snapshots_for_run(
                    db, args.run_id, market_cutoff=market_cutoff
                )
                report["injected_mismatch_detected"] = False
            except TransitionPreflightError as exc:
                report["injected_mismatch_detected"] = exc.code == "DECISION_MANIFEST_MISMATCH"
                report["no_lifecycle_mutation_after_mismatch"] = _lifecycle_counts(db) == before
            db.rollback()
    finally:
        engine.dispose()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("injected_mismatch_detected") else 1


def _lifecycle_counts(db: Session) -> tuple[int, int, int]:
    return (
        int(db.scalar(select(func.count()).select_from(SetupSignalSnapshot)) or 0),
        int(db.scalar(select(func.count()).select_from(SetupSignalSnapshotCurrentSelection)) or 0),
        int(db.scalar(select(func.count()).select_from(SetupSignalSnapshotSelectionEvent)) or 0),
    )


if __name__ == "__main__":
    raise SystemExit(main())
