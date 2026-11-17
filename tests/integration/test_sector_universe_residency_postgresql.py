"""Compact aggregation retains sealed values and releases decoded producer trees."""

import gc
import weakref
from dataclasses import asdict

import pytest
import test_contextual_configuration_adoption_postgresql as contextual
from native_mutation_support import seed_native_core
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.models.tables import CoreCalculationEvidence
from app.services import sector_universe_service as universe
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.sector_rotation_config import load_sector_rotation_config

pytestmark = [pytest.mark.integration, pytest.mark.destructive]
contextual_engine = contextual.contextual_engine


def test_compact_universe_matches_full_frozen_projections_and_releases_evidence(
    contextual_engine, monkeypatch,
):
    with Session(contextual_engine) as seed:
        cutoff, _, _ = seed_native_core(seed)

    references = []
    full_json_queries = []

    def loaded(_session, instance):
        if isinstance(instance, CoreCalculationEvidence):
            references.append(weakref.ref(instance))

    def query(_conn, _cursor, statement, _parameters, _context, _many):
        if "core_calculation_evidence.payload_json" in statement:
            full_json_queries.append(statement)

    event.listen(contextual_engine, "before_cursor_execute", query)
    try:
        with Session(contextual_engine) as db:
            event.listen(db, "loaded_as_persistent", loaded)
            compact = universe.build_universe_sector_metrics(
                db, 7, load_sector_rotation_config(), market_cutoff=cutoff,
            )
            assert references and full_json_queries
            gc.collect()
            # Even while the read transaction remains open, completed source
            # projection relationships and the local LRU have no live owners.
            assert all(reference() is None for reference in references)
            assert not db.new and not db.dirty and not db.deleted
            compact_bytes = Canonical.dumps([asdict(row) for row in compact])
            db.rollback()
    finally:
        event.remove(contextual_engine, "before_cursor_execute", query)

    # Retain complete native frozen projections as the control. The production
    # selectors, permissions and financial-value checks run in both executions.
    monkeypatch.setattr(universe, "_sector_source_view", lambda row: row)
    with Session(contextual_engine) as db:
        complete = universe.build_universe_sector_metrics(
            db, 7, load_sector_rotation_config(), market_cutoff=cutoff,
        )
        assert Canonical.dumps([asdict(row) for row in complete]) == compact_bytes
        db.rollback()
