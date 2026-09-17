"""Native authority attacks, rollback, and immutable projection boundaries."""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

import pytest
import test_contextual_configuration_adoption_postgresql as contextual
from native_mutation_support import bound_pipeline
from sqlalchemy import delete, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from test_fundamental_ranker_v2 import _quality_values

from app.models.tables import (
    CoreCalculationCurrentProjection,
    CoreCalculationEvidence,
    FundamentalScore,
    RawCompanyRow,
    UploadRun,
)
from app.services.calculation_identity import IdentityDimension
from app.services.combined_ranking_identity import calculation_identity_from_debug
from app.services.core_calculation_evidence import (
    CoreEvidenceKind,
    _advance_current_projection,
    declare_core_evidence_mutation,
    persist_core_evidence,
)
from app.services.core_effective_configuration import resolve_fundamental_configuration
from app.services.core_mutation_authority import projection_mutation_context
from app.services.domain_mutation import MutationDomain, MutationSemanticMode
from app.services.fundamental_score_service import recalculate_run_fundamentals

pytestmark = [pytest.mark.integration, pytest.mark.destructive]
contextual_engine = contextual.contextual_engine


def test_native_writer_rejects_forged_authority_and_rolls_back_staged_serving(contextual_engine):
    config = resolve_fundamental_configuration()
    with Session(contextual_engine) as db:
        db.add(UploadRun(id=7, filename="authority.csv", status="COMPLETED"))
        db.flush()
        db.add(
            RawCompanyRow(
                run_id=7,
                row_number=1,
                ticker="ACME",
                raw_json={"Symbol": "ACME", **_quality_values()},
            )
        )
        db.flush()
        cutoff, pipeline_id = bound_pipeline(db, 7, [config])
        row = recalculate_run_fundamentals(
            db, 7, market_cutoff=cutoff, pipeline_run_id=pipeline_id, effective_configuration=config
        )[0]
        db.commit()
        row_id, evidence_id = row.id, row.evidence_id
        evidence = db.get(CoreCalculationEvidence, evidence_id)
        payload = deepcopy(evidence.payload_json)
        payload.pop("producer_readiness")
        payload.pop("effective_configuration_at_creation")
        identity = calculation_identity_from_debug(row.debug_json)
        context = declare_core_evidence_mutation(
            db,
            kind=CoreEvidenceKind.FUNDAMENTAL,
            current_row=row,
            payload=payload,
            effective_configuration=config.snapshot,
        )
        baseline_score = row.fundamental_score
        retry = persist_core_evidence(
            db,
            kind=CoreEvidenceKind.FUNDAMENTAL,
            current_row=row,
            payload=payload,
            calculation_identity=identity,
            effective_configuration=config.snapshot,
            mutation_context=context,
        )
        assert retry.id == evidence_id
        db.commit()
        attacks = {
            "missing": None,
            "domain": replace(context, domain=MutationDomain.TECHNICAL),
            "writer_version": replace(context, writer=replace(context.writer, version="forged")),
            "legacy_mode": replace(context, semantic_mode=MutationSemanticMode.LEGACY_UNCERTIFIED),
            "missing_identity": replace(context, calculation_identity=None),
            "missing_time": replace(context, temporal=None),
            "different_time": replace(
                context, temporal=replace(cutoff, cutoff_at=cutoff.cutoff_at + timedelta(hours=1))
            ),
            "missing_config": replace(context, configuration=None),
            "missing_manifest": replace(context, evidence=()),
            "wrong_run": replace(context, run_id=8),
            "wrong_pipeline": replace(context, pipeline_run_id=pipeline_id + 1),
            "unbound_identity": replace(
                context,
                calculation_identity=replace(
                    identity,
                    ownership=replace(identity.ownership, pipeline_id=IdentityDimension.unknown()),
                ),
            ),
        }
        for name, attack in attacks.items():
            row = db.get(FundamentalScore, row_id)
            row.fundamental_score = 99
            with pytest.raises(ValueError):
                persist_core_evidence(
                    db,
                    kind=CoreEvidenceKind.FUNDAMENTAL,
                    current_row=row,
                    payload=payload,
                    calculation_identity=identity,
                    effective_configuration=config.snapshot,
                    mutation_context=attack,
                )
            db.commit()  # A caller catching rejection cannot commit staged serving changes.
            assert db.get(FundamentalScore, row_id).fundamental_score == baseline_score, name
            assert len(list(db.scalars(select(CoreCalculationEvidence)))) == 1, name
            assert db.scalar(select(CoreCalculationCurrentProjection.evidence_id)) == evidence_id

        evidence = db.get(CoreCalculationEvidence, evidence_id)
        projection = projection_mutation_context(evidence)
        with pytest.raises(ValueError):
            _advance_current_projection(
                db, evidence, mutation_context=replace(projection, evidence=())
            )
        db.commit()
        assert db.scalar(select(CoreCalculationCurrentProjection.evidence_id)) == evidence_id
        _advance_current_projection(db, evidence, mutation_context=projection)
        db.commit()
        assert db.scalar(select(CoreCalculationCurrentProjection.evidence_id)) == evidence_id

        # The first creation of a pointer is serialized even without a row to lock.
        db.execute(delete(CoreCalculationCurrentProjection))
        db.commit()
        _advance_current_projection(db, evidence, mutation_context=projection)
        with Session(contextual_engine) as contender:
            contender.execute(text("SET LOCAL lock_timeout = '100ms'"))
            contender_evidence = contender.get(CoreCalculationEvidence, evidence_id)
            with pytest.raises(OperationalError):
                _advance_current_projection(
                    contender,
                    contender_evidence,
                    mutation_context=projection_mutation_context(contender_evidence),
                )
            contender.rollback()
        db.commit()
        assert len(list(db.scalars(select(CoreCalculationCurrentProjection)))) == 1

        # A certified older artifact cannot replace a newer pointer.
        later_cutoff, later_pipeline = bound_pipeline(
            db, 7, [config], cutoff_at=cutoff.cutoff_at + timedelta(hours=1)
        )
        newer = recalculate_run_fundamentals(
            db,
            7,
            market_cutoff=later_cutoff,
            pipeline_run_id=later_pipeline,
            effective_configuration=config,
        )[0]
        new_id = newer.evidence_id
        db.commit()
        evidence = db.get(CoreCalculationEvidence, evidence_id)
        _advance_current_projection(
            db, evidence, mutation_context=projection_mutation_context(evidence)
        )
        db.commit()
        assert db.scalar(select(CoreCalculationCurrentProjection.evidence_id)) == new_id

        with pytest.raises(ValueError, match="MUTATION_SERVING_PROJECTION_REGRESSION_REJECTED"):
            recalculate_run_fundamentals(
                db,
                7,
                market_cutoff=cutoff,
                pipeline_run_id=pipeline_id,
                effective_configuration=config,
            )
        db.commit()
        assert db.scalar(select(FundamentalScore.evidence_id)) == new_id
        assert db.scalar(select(CoreCalculationCurrentProjection.evidence_id)) == new_id

        identity = calculation_identity_from_debug(newer.debug_json)
        payload = deepcopy(db.get(CoreCalculationEvidence, new_id).payload_json)
        payload.pop("producer_readiness")
        payload.pop("effective_configuration_at_creation")
        context = declare_core_evidence_mutation(
            db,
            kind=CoreEvidenceKind.FUNDAMENTAL,
            current_row=newer,
            payload=payload,
            effective_configuration=config.snapshot,
        )
        raw = db.scalar(select(RawCompanyRow))
        raw.raw_json = {**raw.raw_json, "Symbol": "WRONG"}
        db.commit()
        row = newer
        with pytest.raises(ValueError, match="SOURCE"):
            persist_core_evidence(
                db,
                kind=CoreEvidenceKind.FUNDAMENTAL,
                current_row=row,
                payload=payload,
                calculation_identity=identity,
                effective_configuration=config.snapshot,
                mutation_context=context,
            )
        db.commit()
        assert db.scalar(select(CoreCalculationCurrentProjection.evidence_id)) == new_id


@pytest.mark.parametrize(
    "domain",
    ["FUNDAMENTAL", "TECHNICAL", "COMBINED", "RANKING", "REGIME", "SECTOR", "CERI", "IBMI"],
)
def test_direct_financial_sealer_requires_explicit_context(contextual_engine, domain):
    from types import SimpleNamespace

    with Session(contextual_engine) as db:
        with pytest.raises(ValueError, match="DOMAIN_MUTATION_CONTEXT_REQUIRED"):
            persist_core_evidence(
                db,
                kind=CoreEvidenceKind(domain),
                current_row=SimpleNamespace(evidence_id=None),
                payload={},
            )
        db.commit()
        assert db.scalar(select(CoreCalculationEvidence.id)) is None
