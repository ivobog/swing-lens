"""Native score comparisons batch exact locked score and evidence sources."""

import traceback
from datetime import UTC, datetime
from pathlib import Path

import pytest
import test_contextual_configuration_adoption_postgresql as contextual
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriScoreSnapshot
from app.models.tables import CoreCalculationEvidence, RawCompanyRow, UploadRun
from app.services.ceri.capture_service import CeriRunCaptureService
from app.services.ceri.change_rebuild_service import (
    CeriChangeRebuildRequest,
    CeriChangeRebuildService,
)
from app.services.ceri.snapshot_service import CeriSnapshotService
from app.services.contextual_effective_configuration import resolve_ceri_configuration
from app.services.market_clock_service import MarketClockService

contextual_engine = contextual.contextual_engine
pytestmark = [pytest.mark.integration, pytest.mark.destructive]


@pytest.mark.parametrize("population", [1, 50])
def test_score_change_rebuild_authority_queries_are_bounded(
    contextual_engine, population, record_property, monkeypatch
):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "e2e"))
    from single_run_certification.fixtures import _seed_ceri_manual_evidence

    with Session(contextual_engine) as db:
        clock = MarketClockService()
        initial = clock.cutoff_for(datetime.now(UTC), reason="T14D_SCORE_SOURCE_SEED")
        _seed_ceri_manual_evidence(db, as_of_session=initial.latest_completed_session)
        db.commit()
        snapshots = CeriSnapshotService()
        frozen = resolve_ceri_configuration(
            snapshots.config,
            consumer={
                "run_capture": True,
                "revision_feature_config_hash": snapshots.config.config_hash,
                "ibmi_enabled": False,
                "volatility_enabled": False,
                "short_pressure_enabled": False,
                "volatility": {"ceri_risk_max_contribution": 1.5},
            },
        )
        capture = CeriRunCaptureService(snapshot_service=snapshots)
        for index in range(population):
            run = UploadRun(
                filename=f"native-score-scaling-{index}.csv", status="COMPLETED", row_count=1
            )
            db.add(run)
            db.flush()
            db.add(RawCompanyRow(run_id=run.id, row_number=1, ticker="ALFA", raw_json={}))
            db.commit()
            cutoff = clock.cutoff_for(datetime.now(UTC), reason="T14D_SCORE_SCALING_CAPTURE")
            result = capture.capture_run(
                db, run.id, force=True, market_cutoff=cutoff, effective_configuration=frozen
            )
            assert result.failed == 0 and result.score_snapshots == 1, result
            db.commit()
        scores = list(db.scalars(select(CeriScoreSnapshot)))
        assert len(scores) == population + 1 and all(s.evidence_id is not None for s in scores)
        statements = []

        def observe(conn, cursor, sql, parameters, context, executemany):
            stack = traceback.extract_stack()
            if sql.lstrip().upper().startswith("SELECT") and (
                any(frame.name == "validate_score_source" for frame in stack)
                or (
                    any("change_rebuild_service.py" in frame.filename for frame in stack)
                    and "FOR UPDATE" in sql.upper()
                    and (
                        "ceri_score_snapshots" in sql.lower()
                        or "core_calculation_evidence" in sql.lower()
                    )
                )
            ):
                statements.append(sql)

        boundary = clock.cutoff_for(datetime.now(UTC), reason="T14D_SCORE_CHANGE_REBUILD")
        request = CeriChangeRebuildRequest(
            ticker="ALFA",
            as_of_session=boundary.latest_completed_session,
            cutoff_at=boundary.cutoff_at,
        )
        event.listen(contextual_engine, "before_cursor_execute", observe)
        try:
            result = CeriChangeRebuildService().rebuild(db, request)
            assert result.failed == 0, result.as_dict()
        finally:
            event.remove(contextual_engine, "before_cursor_execute", observe)
        record_property("population", population)
        record_property("native_score_sources", len(scores))
        record_property("score_authority_selects", len(statements))
        record_property("query_fingerprints", "\n".join(sorted(set(statements))))
        assert len(statements) <= 12, f"{len(statements)} score-authority SELECTs; budget=12"
        db.commit()
        if population == 1:
            score = scores[-1]
            evidence = db.get(CoreCalculationEvidence, score.evidence_id)
            old_score, old_key = score.opportunity_score, evidence.evidence_key
            for target, field, altered in (
                (score, "opportunity_score", 999),
                (evidence, "evidence_key", "forged"),
            ):
                setattr(target, field, altered)
                rejected = CeriChangeRebuildService().rebuild(db, request)
                assert rejected.failed > 0, rejected.as_dict()
                assert any(
                    "BODY_MISMATCH" in error["error"]
                    or "BUNDLE_CHANGED" in error["error"]
                    or (
                        target is evidence
                        and "IMMUTABLE_EVIDENCE_MUTATION_REJECTED" in error["error"]
                    )
                    for error in rejected.errors
                ), rejected.errors
                db.rollback()
            assert db.get(CeriScoreSnapshot, score.id).opportunity_score == old_score
            assert db.get(CoreCalculationEvidence, evidence.id).evidence_key == old_key
