from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from alembic import command
from app.models.tables import CoreCalculationEvidence, UploadRun
from app.services.calculation_identity import (
    CalculationIdentity,
    CalendarIdentity,
    IdentityDimension,
)
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.core_calculation_evidence import (
    CoreEvidenceKind,
    get_current_evidence,
    persist_core_evidence,
)
from app.services.effective_configuration import (
    CONFIGURATION_PAYLOAD_KEY,
    ConfigurationClassification,
    ConfigurationDrift,
    ConfigurationEntry,
    ConfigurationFamily,
    ConfigurationResolution,
    EffectiveConfigurationSnapshot,
    bind_configuration,
    configuration_from_evidence,
)
from app.services.effective_configuration_families import resolve_ranking_profile

pytestmark = [pytest.mark.integration, pytest.mark.destructive]


def test_configuration_embedded_evidence_round_trip_retry_drift_and_immutability(
    disposable_postgres_database,
):
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", disposable_postgres_database)
    assert ScriptDirectory.from_config(config).get_heads() == ["0079_setup_lifecycle_alert_ev"]
    command.upgrade(config, "head")
    command.check(config)
    engine = create_engine(disposable_postgres_database)
    try:
        _, first = resolve_ranking_profile("momentum_swing")
        # Use the same profile name but different behavior-affecting values in C2.
        from app.services.effective_configuration_families import snapshot_ranking_profile
        from app.services.ranking_profile_config import get_ranking_profile

        profile = get_ranking_profile("momentum_swing")
        second = snapshot_ranking_profile(
            replace(
                profile,
                technical_weight=profile.technical_weight + 0.05,
                fundamental_weight=profile.fundamental_weight - 0.05,
            )
        )
        base = CalculationIdentity.legacy_unknown(run_id=1, ticker="ACME")
        base = replace(
            base,
            temporal=replace(
                base.temporal,
                as_of_session=IdentityDimension.known(date(2026, 9, 16)),
                calculation_cutoff=IdentityDimension.known(datetime(2026, 9, 16, 20, tzinfo=UTC)),
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
        c1, c2 = bind_configuration(base, first), bind_configuration(base, second)
        with Session(engine) as db:
            db.add(UploadRun(id=1, filename="configuration.csv", status="COMPLETED"))
            db.flush()
            row = SimpleNamespace(
                run_id=1, ticker="ACME", ranking_profile=profile.name, evidence_id=None
            )
            args = dict(
                kind=CoreEvidenceKind.RANKING,
                current_row=row,
                payload={"profile_score": 7.5, "is_complete": True},
            )
            e1 = persist_core_evidence(
                db, **args, calculation_identity=c1, effective_configuration=first
            )
            e1_id, e1_payload = e1.id, deepcopy(e1.payload_json)
            retry = persist_core_evidence(
                db, **args, calculation_identity=c1, effective_configuration=first
            )
            assert retry.id == e1_id
            e2 = persist_core_evidence(
                db, **args, calculation_identity=c2, effective_configuration=second
            )
            assert e2.id != e1_id
            assert e1.payload_json == e1_payload
            assert configuration_from_evidence(e1).value == first.identity
            assert configuration_from_evidence(e2).value == second.identity
            assert (
                ConfigurationDrift(
                    configuration_from_evidence(e1), configuration_from_evidence(e2)
                ).detected
                is True
            )
            assert (
                get_current_evidence(
                    db,
                    kind=CoreEvidenceKind.RANKING,
                    run_id=1,
                    ticker="ACME",
                    ranking_profile=profile.name,
                ).id
                == e2.id
            )
            with pytest.raises(ValueError, match="must match"):
                persist_core_evidence(
                    db, **args, calculation_identity=c1, effective_configuration=second
                )
            with pytest.raises(ValueError, match="owned by"):
                persist_core_evidence(
                    db,
                    kind=CoreEvidenceKind.RANKING,
                    current_row=row,
                    payload={CONFIGURATION_PAYLOAD_KEY: first.as_dict()},
                    calculation_identity=c1,
                )
            legacy = persist_core_evidence(db, **args, calculation_identity=base)
            assert configuration_from_evidence(legacy).state.value == "LEGACY_UNKNOWN"
            db.commit()

        with Session(engine) as db:
            historical = db.get(CoreCalculationEvidence, e1_id)
            assert historical.payload_json == e1_payload
            assert configuration_from_evidence(historical).value == first.identity
            historical.payload_json = {"changed": True}
            with pytest.raises(ValueError, match="IMMUTABLE_EVIDENCE_MUTATION_REJECTED"):
                db.flush()
            db.rollback()
            historical = db.get(CoreCalculationEvidence, e1_id)
            db.delete(historical)
            with pytest.raises(ValueError, match="IMMUTABLE_EVIDENCE_MUTATION_REJECTED"):
                db.flush()
            db.rollback()
            assert db.get(CoreCalculationEvidence, e1_id).payload_json == e1_payload
    finally:
        engine.dispose()


def test_secret_safe_storage_and_binding_integrity(disposable_postgres_database):
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", disposable_postgres_database)
    command.upgrade(config, "head")
    engine = create_engine(disposable_postgres_database)
    try:
        family = ConfigurationFamily(
            "test.secret-safe",
            "v1",
            ConfigurationResolution("test.resolver", "v1"),
            (
                ("weight", ConfigurationClassification.BEHAVIORAL),
                ("api_key", ConfigurationClassification.SECURITY_SECRET),
            ),
        )
        frozen = EffectiveConfigurationSnapshot(
            family,
            (
                ConfigurationEntry("weight", 0.5, ConfigurationClassification.BEHAVIORAL),
                ConfigurationEntry(
                    "api_key",
                    "synthetic-sensitive-value",
                    ConfigurationClassification.SECURITY_SECRET,
                ),
            ),
        )
        base = CalculationIdentity.legacy_unknown(run_id=1, ticker="ACME")
        base = replace(
            base,
            temporal=replace(
                base.temporal,
                calculation_cutoff=IdentityDimension.known(datetime(2026, 9, 16, 20, tzinfo=UTC)),
            ),
        )
        identity = bind_configuration(base, frozen)
        with Session(engine) as db:
            db.add(UploadRun(id=1, filename="safe.csv", status="COMPLETED"))
            db.flush()
            evidence = persist_core_evidence(
                db,
                kind=CoreEvidenceKind.TECHNICAL,
                current_row=SimpleNamespace(run_id=1, ticker="ACME", evidence_id=None),
                payload={"dual_score": 7.5},
                calculation_identity=identity,
                effective_configuration=frozen,
            )
            assert "synthetic-sensitive-value" not in Canonical.dumps(evidence.payload_json)
            assert "api_key" not in Canonical.dumps(evidence.payload_json)
            evidence_id = evidence.id
            db.commit()
        with Session(engine) as db:
            evidence = db.scalar(
                select(CoreCalculationEvidence).where(CoreCalculationEvidence.id == evidence_id)
            )
            assert configuration_from_evidence(evidence).value == frozen.identity
            corrupted = SimpleNamespace(
                payload_json=deepcopy(evidence.payload_json),
                payload_fingerprint=evidence.payload_fingerprint,
                calculation_identity_json=evidence.calculation_identity_json,
                calculation_identity_fingerprint=evidence.calculation_identity_fingerprint,
            )
            corrupted.payload_json[CONFIGURATION_PAYLOAD_KEY]["semantic_hash"] = "f" * 64
            with pytest.raises(ValueError, match="integrity"):
                configuration_from_evidence(corrupted)
    finally:
        engine.dispose()
