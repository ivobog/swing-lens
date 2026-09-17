from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from alembic import command
from app.models.tables import CoreCalculationEvidence, UploadRun, WinnerPredictionSnapshot
from app.services.calculation_identity import (
    CalculationIdentity,
    CalendarIdentity,
    IdentityDimension,
)
from app.services.core_calculation_evidence import (
    CoreEvidenceKind,
    get_current_readiness,
    get_readiness_for_row,
    persist_core_evidence,
)
from app.services.producer_readiness import (
    ReadinessStatus,
    normalize_producer_readiness,
    readiness_from_evidence,
    readiness_from_winner_prediction,
)

pytestmark = [pytest.mark.integration, pytest.mark.destructive]


def test_readiness_is_frozen_identity_bound_and_projection_derived(disposable_postgres_database):
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", disposable_postgres_database)
    command.upgrade(config, "head")
    engine = create_engine(disposable_postgres_database)
    legacy = CalculationIdentity.legacy_unknown(run_id=1, ticker="ACME")
    identity = replace(
        legacy,
        temporal=replace(
            legacy.temporal,
            as_of_session=IdentityDimension.known(date(2026, 9, 15)),
            calculation_cutoff=IdentityDimension.known(datetime(2026, 9, 15, 20, tzinfo=UTC)),
            calendar=IdentityDimension.known(
                CalendarIdentity(
                    "SWINGLENS_US_EQUITIES",
                    "swinglens-us-equities-v1",
                    "America/New_York",
                    IdentityDimension.not_applicable(),
                )
            ),
        ),
    )
    supported = {"dual_score": 7.5, "insufficient_data": False, "technical_confidence": "normal"}
    unsupported = {**supported, "insufficient_data": True}
    with Session(engine) as db:
        db.add(UploadRun(id=1, filename="readiness.csv", status="COMPLETED"))
        db.flush()
        row = SimpleNamespace(run_id=1, ticker="ACME", evidence_id=None)
        first = persist_core_evidence(
            db,
            kind=CoreEvidenceKind.TECHNICAL,
            current_row=row,
            payload=supported,
            calculation_identity=identity,
        )
        first_payload = deepcopy(first.payload_json)
        second = persist_core_evidence(
            db,
            kind=CoreEvidenceKind.TECHNICAL,
            current_row=row,
            payload=unsupported,
            calculation_identity=identity,
        )
        assert first.id != second.id
        assert first.payload_json["dual_score"] == second.payload_json["dual_score"] == 7.5
        assert readiness_from_evidence(first).status is ReadinessStatus.READY
        assert readiness_from_evidence(second).status is ReadinessStatus.INSUFFICIENT_EVIDENCE
        assert first.payload_json == first_payload
        assert get_readiness_for_row(db, kind=CoreEvidenceKind.TECHNICAL, current_row=row) == (
            readiness_from_evidence(second)
        )
        assert (
            get_current_readiness(
                db, kind=CoreEvidenceKind.TECHNICAL, run_id=1, ticker="ACME"
            ).evidence_id
            == second.id
        )
        retry = persist_core_evidence(
            db,
            kind=CoreEvidenceKind.TECHNICAL,
            current_row=row,
            payload=unsupported,
            calculation_identity=identity,
        )
        assert retry.id == second.id
        other_identity = replace(
            identity,
            ownership=replace(
                identity.ownership,
                pipeline_id=IdentityDimension.known(99),
            ),
        )
        third = persist_core_evidence(
            db,
            kind=CoreEvidenceKind.TECHNICAL,
            current_row=row,
            payload=supported,
            calculation_identity=other_identity,
        )
        assert readiness_from_evidence(third).status is ReadinessStatus.READY
        assert (
            readiness_from_evidence(third).fingerprint()
            != readiness_from_evidence(first).fingerprint()
        )
        ids = first.id, second.id, third.id
        db.commit()
    with Session(engine) as db:
        first, second, third = (db.get(CoreCalculationEvidence, evidence_id) for evidence_id in ids)
        assert first.payload_json == first_payload
        assert readiness_from_evidence(first).status is ReadinessStatus.READY
        assert readiness_from_evidence(second).status is ReadinessStatus.INSUFFICIENT_EVIDENCE
        assert (
            get_current_readiness(
                db, kind=CoreEvidenceKind.TECHNICAL, run_id=1, ticker="ACME"
            ).evidence_id
            == third.id
        )
        # An ORM mutation of frozen readiness is covered by the Phase-2 guard.
        altered = deepcopy(first.payload_json)
        altered["producer_readiness"]["status"] = "ERROR"
        first.payload_json = altered
        with pytest.raises(ValueError, match="IMMUTABLE_EVIDENCE_MUTATION_REJECTED"):
            db.flush()
        db.rollback()
    from app.services.combined_ranking_identity import embed_calculation_identity

    with Session(engine) as db:
        readiness = normalize_producer_readiness(
            "WINNER",
            {"technical_data_quality": "high"},
            identity_fingerprint=str(identity.fingerprint()),
        )
        prediction = WinnerPredictionSnapshot(
            run_id=1,
            ticker="ACME",
            prediction_as_of_date=date(2026, 9, 15),
            source_data_cutoff_at=datetime(2026, 9, 15, 20, tzinfo=UTC),
            entry_schedule_status="RESOLVED",
            entry_data_status="PENDING",
            eligibility_status="ELIGIBLE",
            feature_schema_version="t12a-v1",
            feature_vector_hash="f" * 64,
            config_hash="c" * 64,
            calculation_version="t12a-v1",
            feature_json={"score": 7.5},
            source_ids_json={},
            warning_flags_json=[],
            lineage_json=embed_calculation_identity(
                {"producer_readiness": readiness.canonical_payload()},
                identity,
                policy="T12A_TEST",
            ),
        )
        db.add(prediction)
        db.commit()
        assert readiness_from_winner_prediction(prediction).status is ReadinessStatus.UNKNOWN
        prediction.lineage_json = {**prediction.lineage_json, "dependent_episode": True}
        db.commit()
        prediction_id = prediction.id
    with Session(engine) as db:
        prediction = db.get(WinnerPredictionSnapshot, prediction_id)
        assert readiness_from_winner_prediction(prediction).fingerprint() == readiness.fingerprint()
        altered = deepcopy(prediction.lineage_json)
        altered["producer_readiness"]["status"] = "READY"
        prediction.lineage_json = altered
        with pytest.raises(ValueError, match="IMMUTABLE_EVIDENCE_MUTATION_REJECTED"):
            db.flush()
        db.rollback()
        db.delete(db.get(WinnerPredictionSnapshot, prediction_id))
        with pytest.raises(ValueError, match="IMMUTABLE_EVIDENCE_MUTATION_REJECTED"):
            db.flush()
        db.rollback()
        prediction = db.get(WinnerPredictionSnapshot, prediction_id)
        prediction.lineage_json = {}
        db.delete(prediction)
        with pytest.raises(ValueError, match="IMMUTABLE_EVIDENCE_MUTATION_REJECTED"):
            db.flush()
        db.rollback()
    engine.dispose()
