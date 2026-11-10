"""Bound decoded residency while retaining native evidence seal checks."""

import gc

import pytest
import test_contextual_configuration_adoption_postgresql as contextual
from native_mutation_support import bound_pipeline
from sqlalchemy import event, select
from sqlalchemy.orm import Session
from test_fundamental_ranker_v2 import _quality_values

from app.models.tables import CoreCalculationEvidence, RawCompanyRow, UploadRun
from app.services import core_mutation_authority as authority
from app.services.core_effective_configuration import resolve_fundamental_configuration
from app.services.fundamental_score_service import recalculate_run_fundamentals

pytestmark = [pytest.mark.integration, pytest.mark.destructive]
contextual_engine = contextual.contextual_engine


def test_native_sector_evidence_windows_bound_decoded_residency_and_recheck_seals(
    contextual_engine,
):
    config = resolve_fundamental_configuration()
    count = authority.SECTOR_EVIDENCE_RESIDENT_LIMIT * 2 + 5
    with Session(contextual_engine) as seed:
        upload = UploadRun(filename="sector-residency.csv", status="COMPLETED")
        seed.add(upload)
        seed.flush()
        seed.add_all([
            RawCompanyRow(
                run_id=upload.id, row_number=index+1, ticker=f"MEM{index:04d}",
                raw_json={"Symbol": f"MEM{index:04d}", **_quality_values()},
            ) for index in range(count)
        ])
        seed.flush()
        cutoff, pipeline_id = bound_pipeline(seed, upload.id, [config])
        recalculate_run_fundamentals(
            seed, upload.id, market_cutoff=cutoff, pipeline_run_id=pipeline_id,
            effective_configuration=config,
        )
        seed.commit()
        ids = tuple(seed.scalars(select(CoreCalculationEvidence.id).where(
            CoreCalculationEvidence.run_id == upload.id,
        ).order_by(CoreCalculationEvidence.id)))
        assert len(ids) == count
    full_queries = []

    def observe(_connection, _cursor, statement, parameters, _context, _many):
        if (statement.lstrip().lower().startswith("select")
                and "core_calculation_evidence.payload_json" in statement):
            full_queries.append((statement, parameters))

    event.listen(contextual_engine, "before_cursor_execute", observe)
    try:
        with Session(contextual_engine) as db, authority.sector_evidence_read_scope(db):
            evidence = authority.sector_evidence_rows(db, ids)
            assert len(evidence) == count and evidence.resident_count == 0
            # Five profile/ticker groups deliberately disagree with stored ID
            # order. Exercise the production visitation plan, not reader.__iter__.
            role_ids = [ids[index] for index in sorted(
                range(count), key=lambda index: (index % 5, (index * 47) % count),
            )]
            original_roles = tuple(role_ids)
            for index in authority.sector_evidence_visit_order(role_ids):
                address = role_ids[index]
                authority._validate_evidence(evidence[address])
                assert evidence.resident_count <= authority.SECTOR_EVIDENCE_RESIDENT_LIMIT
                assert evidence.resident_bytes <= authority.SECTOR_EVIDENCE_RESIDENT_BYTES
            gc.collect()
            resident = [row for row in db.identity_map.values()
                        if isinstance(row, CoreCalculationEvidence)]
            assert len(resident) <= authority.SECTOR_EVIDENCE_RESIDENT_LIMIT
            del resident
            assert len(full_queries) <= (
                count + authority.SECTOR_EVIDENCE_FETCH_BATCH_SIZE - 1
            ) // authority.SECTOR_EVIDENCE_FETCH_BATCH_SIZE
            metrics = evidence.load_metrics()
            assert tuple(role_ids) == original_roles
            assert metrics["evidence_body_rows_loaded"] == count
            assert metrics["evidence_bulk_load_count"] == len(full_queries)
            assert metrics["evidence_address_count"] == count
            assert metrics["evidence_cache_misses"] <= (count + 31) // 32
            first = evidence[ids[0]]  # Evicted bodies reload their exact address.
            authority._validate_evidence(first)
            first.payload_json["growth_score"] = "9999"
            with pytest.raises(ValueError, match="MUTATION_SOURCE_PAYLOAD_FINGERPRINT_MISMATCH"):
                authority._validate_evidence(evidence[ids[0]])
            db.rollback()
            authority._validate_evidence(authority.sector_evidence_rows(db, ids)[ids[0]])
    finally:
        event.remove(contextual_engine, "before_cursor_execute", observe)


def test_oversized_evidence_is_not_retained_or_given_cached_validation(
    contextual_engine, monkeypatch,
):
    from native_mutation_support import seed_native_core

    with Session(contextual_engine) as db:
        _, _, rows = seed_native_core(db)
        address = rows["fundamental"].evidence_id
    monkeypatch.setattr(authority, "SECTOR_EVIDENCE_RESIDENT_BYTES", 1)
    with Session(contextual_engine) as db, authority.sector_evidence_read_scope(db):
        evidence = authority.sector_evidence_rows(db, (address,))
        row = evidence[address]
        authority._validate_evidence(row)
        assert evidence.resident_count == evidence.resident_bytes == 0
        row.payload_json["growth_score"] = "9999"
        with pytest.raises(ValueError, match="MUTATION_SOURCE_PAYLOAD_FINGERPRINT_MISMATCH"):
            authority._validate_evidence(evidence[address])


def test_native_published_sector_evidence_does_not_alias_returned_dto(
    contextual_engine, monkeypatch,
):
    from native_mutation_support import seed_native_core

    from app.models.tables import SectorRotationSnapshot
    from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
    from app.services.sector_rotation_service import SectorRotationService

    native_permission_check = authority.validate_sector_permission

    def indexed_permission(evidence, universe_rows, permission_index=None):
        assert permission_index is not None
        return native_permission_check(evidence, universe_rows, permission_index)

    monkeypatch.setattr(authority, "validate_sector_permission", indexed_permission)

    with Session(contextual_engine) as db:
        cutoff, configs, _ = seed_native_core(db, contextual_snapshots=True)
        dto = SectorRotationService().build_sector_rotation_snapshot(
            db, 7, market_cutoff=cutoff, effective_configuration=configs[-1],
        )
        snapshot = db.scalar(select(SectorRotationSnapshot).where(
            SectorRotationSnapshot.run_id == 7,
            SectorRotationSnapshot.is_current_revision.is_(True),
        ))
        evidence = db.get(CoreCalculationEvidence, snapshot.evidence_id)
        before = Canonical.fingerprint(evidence.payload_json)
        original = dto.debug["native_input_envelope"]["universe_rows"][0]["debug"]
        original["detached_copy_probe"] = "caller-mutated-after-publication"
        assert Canonical.fingerprint(evidence.payload_json) == before
        authority.validate_evidence(evidence)
        db.rollback()
