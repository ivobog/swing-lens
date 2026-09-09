from __future__ import annotations

import argparse
import json
from datetime import timedelta
from pathlib import Path

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.database_safety import assert_disposable_database, safe_database_url
from app.models.ceri_tables import CeriScoreSnapshot, CeriSourceRecord
from app.models.tables import (
    MarketCalculationContext,
    PriceBar,
    SetupSignalSnapshot,
    TransitionPreflightPlan,
    UploadRun,
)
from app.services.canonical_evidence import CanonicalEvidenceSerializer
from app.services.ceri.pit_eligibility import (
    price_bar_is_eligible,
    source_record_is_eligible,
    source_record_known_at,
)
from app.services.ceri.snapshot_service import CeriSnapshotService
from app.services.market_calculation_context_service import cutoff_from_row
from app.services.pipeline_service import start_pipeline
from app.services.setup_lifecycle.forensic_hash import immutable_evidence_hash
from app.services.setup_lifecycle.transition_candidate_service import (
    TransitionCandidateDiscoveryService,
)
from app.services.transition_preflight_plan_service import create_transition_preflight_plan

RUN_IDS = (148, 149, 150, 151, 152)
TICKERS = {"TBLA", "NNI", "TPL", "DRS"}
CERI_SOURCE_IDS = tuple(range(867459, 867466))
PRICE_BAR_IDS = (2263479, 2263504)


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay temporal contracts on a disposable clone.")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    identity = assert_disposable_database(args.database_url)
    engine = create_engine(args.database_url, pool_pre_ping=True)
    report: dict[str, object] = {
        "database": identity.database_name,
        "database_url": safe_database_url(args.database_url),
        "production_business_writes": False,
        "production_pipeline_run": False,
        "live_canary_run": False,
        "historical_repair": False,
    }

    with Session(engine) as db:
        baseline = _counts(db)
        context_row = db.get(MarketCalculationContext, 5)
        if context_row is None:
            raise RuntimeError("certified context 5 is missing from clone")
        frozen = cutoff_from_row(context_row)
        report["frozen_context"] = {
            "id": frozen.context_id,
            "cutoff_at": CanonicalEvidenceSerializer.canonicalize(frozen.cutoff_at),
            "latest_completed_session": frozen.latest_completed_session.isoformat(),
            "calendar_version": frozen.calendar_version,
        }
        report["run_replays"] = _run_replays(db)
        report["ceri_10929"] = _ceri_replay(db)
        report["ceri_sources"] = _source_replay(db, frozen.cutoff_at)
        report["price_bars"] = _bar_replay(db, frozen)

        discovery = TransitionCandidateDiscoveryService(full_universe_technical_preview=False)
        targeted = discovery.discover_for_run(
            db,
            76,
            market_cutoff=frozen,
            tickers=TICKERS,
        )
        run_152 = discovery.discover_for_run(
            db,
            152,
            market_cutoff=frozen,
            tickers={"TBLA", "DRS"},
        )
        report["targeted_candidates"] = [row.as_dict() for row in targeted]
        report["run_152_candidates"] = [row.as_dict() for row in run_152]
        report["read_only_counts_unchanged"] = _counts(db) == baseline
        db.rollback()

    with Session(engine, expire_on_commit=False) as db:
        plan = create_transition_preflight_plan(
            db,
            upload_run_id=152,
            idempotency_key="adversarial-clone-run-152",
            cutoff_at=frozen.cutoff_at,
            tickers={"TBLA", "DRS"},
            expires_in=timedelta(days=1),
            discovery=TransitionCandidateDiscoveryService(full_universe_technical_preview=False),
        )
        db.commit()
        plan_id = plan.id
        plan_context_id = plan.market_calculation_context_id

    with Session(engine) as db:
        db.execute(text("SET LOCAL TIME ZONE 'Europe/Zurich'"))
        pipeline = start_pipeline(
            db,
            152,
            transition_preflight_plan_id=plan_id,
            transition_candidate_discovery=TransitionCandidateDiscoveryService(
                full_universe_technical_preview=False
            ),
        )
        db.commit()
        pipeline_id = pipeline.id

    with Session(engine) as db:
        duplicate = start_pipeline(
            db,
            152,
            transition_preflight_plan_id=plan_id,
            transition_candidate_discovery=TransitionCandidateDiscoveryService(
                full_universe_technical_preview=False
            ),
        )
        plan = db.get(TransitionPreflightPlan, plan_id)
        context = db.get(MarketCalculationContext, plan_context_id)
        report["preflight_execution"] = {
            "plan_id": plan_id,
            "plan_status": plan.status,
            "context_id": plan_context_id,
            "context_owner_pipeline_id": context.pipeline_run_id,
            "pipeline_id": pipeline_id,
            "duplicate_pipeline_id": duplicate.id,
            "one_pipeline": duplicate.id == pipeline_id == plan.pipeline_run_id,
        }
        report["clone_final_counts"] = _counts(db)
        db.rollback()

    report["passed"] = bool(
        report["read_only_counts_unchanged"]
        and report["ceri_10929"]["matches"]
        and report["preflight_execution"]["one_pipeline"]
        and len(report["run_replays"]) == len(RUN_IDS)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


def _counts(db: Session) -> dict[str, int]:
    names = (
        "upload_runs",
        "pipeline_runs",
        "background_jobs",
        "market_calculation_contexts",
        "transition_preflight_plans",
        "setup_signal_snapshots",
        "setup_signal_snapshot_current_selections",
        "setup_signal_snapshot_selection_events",
        "ceri_score_snapshots",
        "ceri_source_records",
        "price_bars",
    )
    return {
        name: int(db.execute(text(f'SELECT count(*) FROM "{name}"')).scalar_one()) for name in names
    }


def _run_replays(db: Session) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for run_id in RUN_IDS:
        run = db.get(UploadRun, run_id)
        if run is None:
            raise RuntimeError(f"run {run_id} is missing from clone")
        snapshots = list(
            db.scalars(
                select(SetupSignalSnapshot)
                .where(SetupSignalSnapshot.run_id == run_id)
                .order_by(SetupSignalSnapshot.id)
            )
        )
        rows.append(
            {
                "run_id": run_id,
                "status": run.status,
                "snapshot_count": len(snapshots),
                "immutable_snapshot_digest": CanonicalEvidenceSerializer.fingerprint(
                    [(row.id, immutable_evidence_hash(row)) for row in snapshots]
                ),
            }
        )
    return rows


def _ceri_replay(db: Session) -> dict[str, object]:
    snapshot = db.get(CeriScoreSnapshot, 10929)
    if snapshot is None:
        raise RuntimeError("CERI snapshot 10929 is missing from clone")
    result = CeriSnapshotService().reproduce_snapshot(snapshot)
    return {
        "matches": result.matches,
        "stored_hash": result.stored_hash,
        "reproduced_hash": result.reproduced_hash,
        "source_count": len((snapshot.component_json or {}).get("source_ids") or []),
    }


def _source_replay(db: Session, cutoff_at) -> list[dict[str, object]]:
    sources = {
        row.id: row
        for row in db.scalars(
            select(CeriSourceRecord).where(CeriSourceRecord.id.in_(CERI_SOURCE_IDS))
        )
    }
    return [
        {
            "id": source_id,
            "present": source_id in sources,
            "known_at": (
                CanonicalEvidenceSerializer.canonicalize(source_record_known_at(sources[source_id]))
                if source_id in sources and source_record_known_at(sources[source_id]) is not None
                else None
            ),
            "eligible": (
                source_record_is_eligible(sources[source_id], cutoff_at)
                if source_id in sources
                else False
            ),
        }
        for source_id in CERI_SOURCE_IDS
    ]


def _bar_replay(db: Session, frozen) -> list[dict[str, object]]:
    bars = {
        row.id: row for row in db.scalars(select(PriceBar).where(PriceBar.id.in_(PRICE_BAR_IDS)))
    }
    return [
        {
            "id": bar_id,
            "present": bar_id in bars,
            "eligible": (
                price_bar_is_eligible(
                    bars[bar_id],
                    latest_completed_session=frozen.latest_completed_session,
                    cutoff_at=frozen.cutoff_at,
                )
                if bar_id in bars
                else False
            ),
        }
        for bar_id in PRICE_BAR_IDS
    ]


if __name__ == "__main__":
    raise SystemExit(main())
