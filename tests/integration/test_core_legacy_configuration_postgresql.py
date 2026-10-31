"""Legacy configuration cannot acquire current authority at any Core writer boundary."""

import csv
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
import test_contextual_configuration_adoption_postgresql as contextual
from native_mutation_support import seed_native_core
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import (
    CombinedResult,
    CoreCalculationCurrentProjection,
    CoreCalculationEvidence,
    CoreCalculationEvidenceSource,
    FundamentalScore,
    RankingResult,
    TechnicalScore,
)
from app.services import core_effective_configuration
from app.services.calculation_identity import CalculationIdentity, IdentityDimension
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.core_calculation_evidence import (
    CoreEvidenceKind,
    declare_core_evidence_mutation,
    persist_core_evidence,
)
from app.services.domain_mutation import (
    MUTATION_AUTHORITY_POLICIES,
    MutationDomain,
    validate_mutation_context,
)
from app.services.effective_configuration import CONFIGURATION_PAYLOAD_KEY
from app.services.producer_readiness import READINESS_PAYLOAD_KEY

pytestmark = [pytest.mark.integration, pytest.mark.destructive]
contextual_engine = contextual.contextual_engine


def test_core_legacy_config_noninheritance_after_native_authority(
    contextual_engine,
    monkeypatch,
    record_property,
):
    source = (
        Path(__file__).resolve().parents[1] / "e2e/single_run_certification/golden/v3/universe.csv"
    )
    with source.open(encoding="utf-8", newline="") as stream:
        raw = {**next(csv.DictReader(stream)), "Symbol": "ACME"}
    with Session(contextual_engine) as db:
        _, configurations, _ = seed_native_core(db, raw_values=raw)

    resolver_calls = []

    def forbidden(*_args, **_kwargs):
        resolver_calls.append("live")
        raise AssertionError("Legacy writer attempted to resolve current configuration")

    for name in (
        "resolve_fundamental_configuration",
        "resolve_technical_configuration",
        "resolve_combined_configuration",
        "resolve_ranking_configuration",
    ):
        monkeypatch.setattr(core_effective_configuration, name, forbidden)

    models = (FundamentalScore, TechnicalScore, CombinedResult, RankingResult)

    def committed_state():
        with Session(contextual_engine) as db:
            return {
                model.__tablename__: [
                    dict(row)
                    for row in db.execute(select(model.__table__).order_by(model.id)).mappings()
                ]
                for model in (
                    *models,
                    CoreCalculationEvidence,
                    CoreCalculationEvidenceSource,
                    CoreCalculationCurrentProjection,
                )
            }

    observations = []
    for kind, model, configuration in zip(
        (
            CoreEvidenceKind.FUNDAMENTAL,
            CoreEvidenceKind.TECHNICAL,
            CoreEvidenceKind.COMBINED,
            CoreEvidenceKind.RANKING,
        ),
        models,
        configurations,
        strict=True,
    ):
        with Session(contextual_engine) as db:
            row = db.scalar(select(model).where(model.run_id == 7))
            evidence = db.get(CoreCalculationEvidence, row.evidence_id)
            evidence_id = evidence.id
            identity = CalculationIdentity.from_canonical_payload(
                evidence.calculation_identity_json
            )
            business = deepcopy(evidence.payload_json)
            business.pop(CONFIGURATION_PAYLOAD_KEY)
            business.pop(READINESS_PAYLOAD_KEY)
            sources = {}
            if kind in (CoreEvidenceKind.COMBINED, CoreEvidenceKind.RANKING):
                sources = {
                    "fundamental": db.scalar(
                        select(FundamentalScore).where(FundamentalScore.run_id == 7)
                    ),
                    "technical": db.scalar(
                        select(TechnicalScore).where(TechnicalScore.run_id == 7)
                    ),
                }
            args = dict(kind=kind, current_row=row, sources=sources, payload=business)
            authority = declare_core_evidence_mutation(
                db,
                **args,
                calculation_identity=identity,
                effective_configuration=configuration.snapshot,
            )
            assert validate_mutation_context(authority).valid
            assert (
                MUTATION_AUTHORITY_POLICIES[MutationDomain(kind.value)].configuration_namespace
                == f"core.{kind.value.lower()}"
            )
            accepted = persist_core_evidence(
                db,
                **args,
                calculation_identity=identity,
                effective_configuration=configuration.snapshot,
                mutation_context=authority,
            )
            assert accepted.id == evidence_id
            db.commit()
            before = committed_state()
            # All other identity, temporal, owner, source and native declaration
            # prerequisites are real and valid. Only configuration becomes legacy.
            legacy = replace(
                identity,
                configuration=replace(
                    identity.configuration,
                    effective_configuration=IdentityDimension.legacy_unknown(),
                ),
            )
            assert legacy.configuration.effective_configuration.state.value == "LEGACY_UNKNOWN"
            with pytest.raises(ValueError, match="MUTATION_IDENTITY_AND_CONFIGURATION_REQUIRED"):
                declare_core_evidence_mutation(
                    db,
                    **args,
                    calculation_identity=legacy,
                    effective_configuration=None,
                )
            legacy_declaration = declare_core_evidence_mutation(
                db,
                **args,
                calculation_identity=legacy,
                effective_configuration=configuration.snapshot,
            )
            rejected = validate_mutation_context(legacy_declaration)
            assert not rejected.valid
            assert "configuration: identity mismatch" in rejected.issues
            with pytest.raises(ValueError, match="MUTATION_IDENTITY_AND_CONFIGURATION_REQUIRED"):
                persist_core_evidence(
                    db,
                    **args,
                    calculation_identity=legacy,
                    effective_configuration=None,
                    mutation_context=authority,
                )
            db.commit()
            assert committed_state() == before
            with pytest.raises(
                ValueError,
                match="effective configuration must match the bound Calculation Identity",
            ):
                persist_core_evidence(
                    db,
                    **args,
                    calculation_identity=legacy,
                    effective_configuration=configuration.snapshot,
                    mutation_context=authority,
                )
            db.commit()
            assert committed_state() == before
            observations.append(
                {
                    "kind": kind.value,
                    "native_evidence_id": evidence_id,
                    "stored_configuration": configuration.snapshot.semantic_hash,
                    "unchanged_state_sha256": Canonical.fingerprint(before),
                    "native_legacy_declaration_issues": rejected.issues,
                    "missing_configuration_refused": True,
                    "current_configuration_graft_refused": True,
                }
            )
    assert resolver_calls == []
    record_property("four_core_legacy_configuration_boundaries", observations)
