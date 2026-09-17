"""Legacy cleanup cannot remove retained history or unchecked concurrent rows."""

from datetime import date

import pytest
import test_contextual_configuration_adoption_postgresql as contextual
from historical_evidence_support import seed_pre_phase5_evidence
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.models.tables import CoreCalculationEvidence, MarketRegimeSnapshot, UploadRun
from app.services.calculation_identity import CalculationIdentity
from app.services.combined_ranking_identity import embed_calculation_identity
from app.services.core_calculation_evidence import CoreEvidenceKind
from app.services.market_regime_repository import MarketRegimeRepository

pytestmark = [pytest.mark.integration, pytest.mark.destructive]
contextual_engine = contextual.contextual_engine


def legacy_snapshot(digest):
    return MarketRegimeSnapshot(
        run_id=7,
        as_of_date=date(2026, 9, 14),
        calculation_version="historical-regime",
        regime="Bull trend",
        risk_state="Green",
        score=8,
        action_summary="Retained legacy fixture",
        evidence_hash=digest * 64,
        debug_json=embed_calculation_identity(
            {}, CalculationIdentity.legacy_unknown(run_id=7), policy="PRE_PHASE5_HISTORY_FIXTURE"
        ),
    )


def test_legacy_delete_is_explicit_exact_and_protects_hidden_retained_links(contextual_engine):
    repository = MarketRegimeRepository()
    with Session(contextual_engine) as db:
        db.add(UploadRun(id=7, filename="legacy-regime.csv", status="COMPLETED"))
        db.flush()
        row = legacy_snapshot("a")
        db.add(row)
        db.commit()
        row_id = row.id
        with pytest.raises(ValueError, match="REGIME_LEGACY_MAINTENANCE_MODE_REQUIRED"):
            repository.delete_for_run(db, 7)
        db.commit()
        assert db.get(MarketRegimeSnapshot, row_id) is not None

        inserted = []

        def append_retained_snapshot(_connection, _cursor, statement, *_args):
            if inserted or not statement.startswith("DELETE FROM market_regime_snapshots"):
                return
            with Session(contextual_engine) as competitor:
                newer = legacy_snapshot("b")
                competitor.add(newer)
                competitor.flush()
                evidence = seed_pre_phase5_evidence(
                    competitor, kind=CoreEvidenceKind.REGIME, current_row=newer
                )
                inserted.append((newer.id, evidence.id))
                competitor.commit()

        event.listen(contextual_engine, "before_cursor_execute", append_retained_snapshot)
        try:
            repository.delete_for_run(db, 7, legacy_only=True)
        finally:
            if event.contains(contextual_engine, "before_cursor_execute", append_retained_snapshot):
                event.remove(contextual_engine, "before_cursor_execute", append_retained_snapshot)
            db.commit()
        assert db.get(MarketRegimeSnapshot, row_id) is None
        snapshot_id, evidence_id = inserted[0]
        assert db.get(MarketRegimeSnapshot, snapshot_id).evidence_id == evidence_id

        # Clearing a mutable serving link cannot hide the immutable run ledger.
        db.get(MarketRegimeSnapshot, snapshot_id).evidence_id = None
        with pytest.raises(ValueError, match="REGIME_CERTIFIED_ARTIFACT_DELETE_FORBIDDEN"):
            repository.delete_for_run(db, 7, legacy_only=True)
        db.commit()
        assert db.get(MarketRegimeSnapshot, snapshot_id).evidence_id == evidence_id
        assert db.scalar(select(CoreCalculationEvidence.id)) == evidence_id
