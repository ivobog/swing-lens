"""Serving values and historical producer configuration cannot impersonate C1."""

import pytest
import test_contextual_configuration_adoption_postgresql as contextual
from historical_evidence_support import seed_pre_phase5_evidence
from native_mutation_support import seed_native_core
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_core_effective_configuration import changed_configuration

from app.models.tables import (
    CombinedResult,
    CoreCalculationEvidence,
    FundamentalScore,
    TechnicalScore,
)
from app.services.combined_decision import refresh_combined_results
from app.services.combined_ranking_identity import (
    calculation_identity_from_debug,
    embed_calculation_identity,
)
from app.services.core_calculation_evidence import CoreEvidenceKind
from app.services.core_mutation_authority import validate_source_values
from app.services.effective_configuration import bind_configuration

pytestmark = [pytest.mark.integration, pytest.mark.destructive]
contextual_engine = contextual.contextual_engine


def test_frozen_source_values_remain_independent_of_serving_diagnostics(contextual_engine):
    from sqlalchemy import inspect

    with Session(contextual_engine) as db:
        _, _, sources = seed_native_core(db)
        technical = sources["technical"]
        evidence = db.get(CoreCalculationEvidence, technical.evidence_id)
        fields = set(inspect(TechnicalScore).column_attrs.keys())
        values = {key: value for key, value in evidence.payload_json.items() if key in fields}
        # Native eligibility projections intentionally omit unused fields.
        values.pop("missing_data_json", None)
        frozen = TechnicalScore(**values, id=technical.id, evidence_id=evidence.id)
        technical.data_quality_score = 6
        technical.missing_data_json = {"market_data": {"mode": "CACHE_FALLBACK"}}
        validate_source_values(frozen, evidence, db=db)
        # Explicitly supplied, consumed values cannot be altered in a frozen DTO.
        frozen.dual_score = 99
        with pytest.raises(ValueError, match="MUTATION_SOURCE_FINANCIAL_VALUE_MISMATCH"):
            validate_source_values(frozen, evidence, db=db)
        db.rollback()
        assert (
            db.get(TechnicalScore, technical.id).missing_data_json
            == evidence.payload_json["missing_data_json"]
        )


def test_combined_rejects_unsealed_source_values_and_c2_history_under_c1(contextual_engine):
    with Session(contextual_engine) as db:
        cutoff, configurations, sources = seed_native_core(db)
        fundamental = sources["fundamental"]
        technical = sources["technical"]
        identity = calculation_identity_from_debug(fundamental.debug_json)
        pipeline_id = identity.ownership.pipeline_id.value
        old_combined = sources["combined"].evidence_id
        old_fundamental = fundamental.fundamental_score
        args = dict(
            market_cutoff=cutoff,
            pipeline_run_id=pipeline_id,
            effective_configuration=configurations[2],
            source_evidence={
                "ACME": {"fundamental": fundamental.evidence_id, "technical": technical.evidence_id}
            },
        )
        fundamental.fundamental_score = 99
        with pytest.raises(ValueError, match="MUTATION_SOURCE_FINANCIAL_VALUE_MISMATCH"):
            refresh_combined_results(db, 7, **args)
        db.commit()
        assert db.scalar(select(FundamentalScore.fundamental_score)) == old_fundamental
        assert db.scalar(select(CombinedResult.evidence_id)) == old_combined

        # Seed retained pre-adoption history, not a live producer loophole. Its
        # own identity/config is complete, but the consumer's parent retains C1.
        c2 = changed_configuration(configurations[0])
        fundamental.debug_json = embed_calculation_identity(
            fundamental.debug_json,
            bind_configuration(identity, c2.snapshot),
            policy="PRE_PHASE5_HISTORY_FIXTURE_C2",
        )
        historical = seed_pre_phase5_evidence(
            db,
            kind=CoreEvidenceKind.FUNDAMENTAL,
            current_row=fundamental,
            effective_configuration=c2.snapshot,
        )
        db.commit()
        args["source_evidence"]["ACME"]["fundamental"] = historical.id
        before = len(list(db.scalars(select(CoreCalculationEvidence))))
        with pytest.raises(ValueError, match="MUTATION_SOURCE_RETAINED_CONFIGURATION_MISMATCH"):
            refresh_combined_results(db, 7, **args)
        db.commit()
        assert db.scalar(select(CombinedResult.evidence_id)) == old_combined
        assert len(list(db.scalars(select(CoreCalculationEvidence)))) == before
