"""Compare the failed clone's handoff envelope; no committed mutations."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def main():
    from sqlalchemy import create_engine, select, text
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import Session

    from app.database_safety import assert_disposable_database
    from app.models.ceri_tables import CeriScoreSnapshot
    from app.models.tables import (
        PipelineRun,
        SetupSignalSnapshot,
        TransitionDecisionHandoffManifest,
        TransitionPreflightPlan,
    )
    from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
    from app.services.configuration_delivery import (
        binding_reference,
        configuration_delivery_scope,
        load_configuration_delivery,
    )
    from app.services.decision_handoff_contract import handoff_artifact_identity
    from app.services.market_calculation_context_service import market_context_for_pipeline
    from app.services.setup_lifecycle.source_loader import SetupLifecycleSourceLoader
    from app.services.transition_preflight_plan_service import (
        _build_decision_handoff_payload,
        _canonical_structural_differences,
        _decision_ceri_context,
        _market_context_payload,
    )
    from app.settings import get_settings

    out = Path(__file__).resolve().parent / "evidence"
    metadata = json.loads((out / "production-clone.json").read_text())
    original = make_url(get_settings().database_url)
    target = original.set(database=metadata["database"])
    assert_disposable_database(target, active_database_url=original)
    engine = create_engine(target)
    with Session(engine) as db:
        pipeline = db.get(PipelineRun, 41)
        cutoff = market_context_for_pipeline(db, pipeline)
        plan = db.scalar(
            select(TransitionPreflightPlan).where(TransitionPreflightPlan.pipeline_run_id == 41)
        )
        retained = db.scalar(
            select(TransitionDecisionHandoffManifest).where(
                TransitionDecisionHandoffManifest.pipeline_run_id == 41
            )
        )
        expected = retained.manifest_json
        ceri_cutoff = _decision_ceri_context(db, plan, cutoff)
        coarse = {
            "market_context_matches": expected["market_context"]
            == Canonical.canonicalize(_market_context_payload(cutoff)),
            "ceri_context_matches": expected["ceri_context"]
            == Canonical.canonicalize(_market_context_payload(ceri_cutoff)),
            "ceri_rows_match_without_owner_filter": expected["ceri_score_snapshots"]
            == [
                handoff_artifact_identity(row)
                for row in db.scalars(
                    select(CeriScoreSnapshot)
                    .where(CeriScoreSnapshot.run_id == 41)
                    .order_by(CeriScoreSnapshot.ticker, CeriScoreSnapshot.id)
                )
            ],
        }
        print(json.dumps(coarse), flush=True)
        # Recreate the worker's active owner state ONLY in this disposable,
        # rollback-only transaction, so canonical CERI eligibility is identical.
        db.execute(text("update pipeline_runs set status='RUNNING' where id=41"))
        db.expire_all()
        delivery = load_configuration_delivery(db, binding_reference(db, pipeline_run_id=41))
        hashes = dict(
            db.execute(
                select(SetupSignalSnapshot.ticker, SetupSignalSnapshot.source_data_hash).where(
                    SetupSignalSnapshot.run_id == 41
                )
            ).all()
        )
        with configuration_delivery_scope(delivery):
            context = SetupLifecycleSourceLoader().load_run_context(db, 41, market_cutoff=cutoff)
            observed = _build_decision_handoff_payload(
                db,
                plan=plan,
                market_cutoff=cutoff,
                ceri_market_cutoff=ceri_cutoff,
                built_rows=tuple(
                    (row, SimpleNamespace(source_data_hash=hashes[row.ticker]))
                    for row in context.tickers
                ),
                decision_manifests=expected["decision_manifests"],
            )
        differences = _canonical_structural_differences(expected, observed)
        result = {
            **coarse,
            "differences": differences,
            "note": (
                "Stored decision manifests and captured Setup input hashes are controls; "
                "runtime reported zero differing decision tickers. "
                "No financial authority was granted by this diagnostic."
            ),
            "committed_mutations": False,
        }
        (out / "clone-handoff-envelope-diff.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        print(json.dumps(result), flush=True)
        db.rollback()
    engine.dispose()


if __name__ == "__main__":
    main()
