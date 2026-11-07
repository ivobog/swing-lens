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


def test_sector_residency_rechecks_seals_and_expires_with_its_transaction(contextual_engine):
    from native_mutation_support import seed_native_core
    from sqlalchemy import event
    from sqlalchemy.orm import object_session

    from app.services.core_mutation_authority import (
        _sector_evidence_rows,
        _validate_evidence,
        sector_evidence_read_scope,
    )

    with Session(contextual_engine) as seed:
        _, _, artifacts = seed_native_core(seed)
        evidence_id = artifacts["fundamental"].evidence_id
        seed.commit()
    full_reads = []

    def observe(_conn, _cursor, statement, _parameters, _context, _many):
        if (statement.lstrip().lower().startswith("select")
                and "core_calculation_evidence.payload_json" in statement):
            full_reads.append(statement)

    event.listen(contextual_engine, "before_cursor_execute", observe)
    try:
        with Session(contextual_engine) as db, sector_evidence_read_scope(db):
            original = _sector_evidence_rows(db, (evidence_id,))[evidence_id]
            retained = _sector_evidence_rows(db, (evidence_id,))[evidence_id]
            assert retained is original and len(full_reads) == 1
            _validate_evidence(retained)
            original.payload_json["growth_score"] = "9999"
            with pytest.raises(ValueError, match="MUTATION_SOURCE_PAYLOAD_FINGERPRINT_MISMATCH"):
                _validate_evidence(retained)  # residency must never cache successful validation
            db.rollback()
            fresh = _sector_evidence_rows(db, (evidence_id,))[evidence_id]
            _validate_evidence(fresh)
            assert len(full_reads) == 2  # old transaction cannot supply a read witness
            with Session(contextual_engine) as other:
                foreign = _sector_evidence_rows(other, (evidence_id,))[evidence_id]
                assert object_session(foreign) is other and foreign is not fresh
                _validate_evidence(foreign)
                assert len(full_reads) == 3
        with Session(contextual_engine) as outside:
            _validate_evidence(_sector_evidence_rows(outside, (evidence_id,))[evidence_id])
            assert len(full_reads) == 4
    finally:
        event.remove(contextual_engine, "before_cursor_execute", observe)


@pytest.mark.parametrize(
    "boundary", [
        "UNIVERSE_RANKING_INPUTS_LOADED", "UNIVERSE_SOURCE_VALIDATION",
        "PERSISTENCE_VALIDATION_SOURCE_LOCKS", "PERSISTENCE_VALIDATION_SOURCE_ROLES",
    ],
)
@pytest.mark.parametrize("fault", ["cancel", "failure"])
def test_sector_source_role_checkpoints_preserve_locks_and_rollback(
    contextual_engine, fault, boundary,
):
    import psycopg
    from native_mutation_support import seed_native_core
    from sqlalchemy import event, func

    from app.models.tables import (
        BackgroundJob,
        CoreCalculationEvidenceSource,
        ExecutionConfigurationBinding,
        MarketCalculationContext,
        RankingResult,
        SectorRotationRow,
        SectorRotationSnapshot,
    )
    from app.services.configuration_delivery import ANCHOR_KEY, binding_reference
    from app.services.contextual_effective_configuration import (
        resolve_regime_configuration,
        resolve_sector_configuration,
    )
    from app.services.domain_write_fence import (
        deferred_execution_ownership_lock,
        fence_domain_commits,
    )
    from app.services.market_regime_command_center import MarketRegimeCommandCenterService
    from app.services.pipeline_executor import PipelineCancelled, _sector_checkpoint_callback
    from app.services.sector_rotation_service import SectorRotationService

    configuration = resolve_sector_configuration()
    regime = resolve_regime_configuration()
    with Session(contextual_engine) as db:
        cutoff, _, artifacts = seed_native_core(db, extra_configurations=(configuration, regime))
        ranking_id = artifacts["ranking"].id
        pipeline_id = db.get(MarketCalculationContext, cutoff.context_id).pipeline_run_id
        reference = binding_reference(db, pipeline_run_id=pipeline_id)
        job = BackgroundJob(
            job_type="FULL_PIPELINE", status="RUNNING", related_run_id=7,
            payload_json={"pipeline_run_id": pipeline_id, ANCHOR_KEY: reference},
            execution_token="sector-checkpoint-test-attempt",
        )
        db.add(job)
        db.flush()
        job_id, token = job.id, job.execution_token
        db.add(ExecutionConfigurationBinding(
            binding_key=f"job:{job_id}", job_id=job_id, anchor_id=reference["anchor_id"],
        ))
        MarketRegimeCommandCenterService().build_snapshot(
            db, run_id=7, market_cutoff=cutoff, effective_configuration=regime,
        )
        db.commit()

    models = (SectorRotationSnapshot, SectorRotationRow, CoreCalculationEvidence,
              CoreCalculationCurrentProjection, CoreCalculationEvidenceSource)

    def counts(session):
        return tuple(session.scalar(select(func.count()).select_from(model)) for model in models)

    with Session(contextual_engine) as db:
        before = counts(db)
        observed = []
        cpu_phase_queries = []
        writer_evidence_bulk_reads = []
        state = {}

        def observe_sql(_conn, _cursor, statement, _parameters, _context, _many):
            if state.get("phase") == "SOURCE_ROWS_LOADED":
                cpu_phase_queries.append(statement)
            if (state.get("phase", "").startswith(("PERSISTENCE", "DECLARATION"))
                    and "core_calculation_evidence.payload_json" in statement
                    and "core_calculation_evidence.id IN (" in statement):
                writer_evidence_bulk_reads.extend(_parameters.values())

        def progress(_db, **values):
            phase = values["operational_metadata_patch"]["sector_progress"]["phase"]
            if phase == "SOURCE_ROWS_LOADED":
                assert not db.in_transaction()
            if phase == boundary:
                atomic = boundary.startswith("PERSISTENCE_VALIDATION")
                assert db.in_nested_transaction() == atomic
                with Session(contextual_engine) as contender:
                    assert counts(contender) == before  # staged rows remain invisible
                    statement = select(RankingResult.id).where(
                        RankingResult.id == ranking_id,
                    ).with_for_update(nowait=True)
                    if boundary == "UNIVERSE_RANKING_INPUTS_LOADED":
                        # The clean input reader has acquired no mutation locks.
                        assert contender.scalar(statement) == ranking_id
                    else:
                        with pytest.raises(OperationalError) as error:
                            contender.execute(statement)
                        assert isinstance(error.value.orig, psycopg.errors.LockNotAvailable)
                if fault == "failure" and values["processed"] > 0:
                    observed.append((phase, values["processed"], values["total"]))
                    raise RuntimeError("sector-source-role-test-failure")
            state.update(phase=phase, processed=values["processed"])
            observed.append((phase, values["processed"], values["total"]))

        checkpoint = _sector_checkpoint_callback(
            db,
            should_cancel=lambda: fault == "cancel"
            and state.get("phase") == boundary,
            progress_callback=progress,
        )
        event.listen(contextual_engine, "before_cursor_execute", observe_sql)
        try:
            expected = PipelineCancelled if fault == "cancel" else RuntimeError
            with pytest.raises(
                expected, match="sector-source-role-test-failure" if fault == "failure" else None,
            ), deferred_execution_ownership_lock(), fence_domain_commits(
                db, job_id=job_id, execution_token=token,
            ):
                SectorRotationService().build_sector_rotation_snapshot(
                    db, 7, market_cutoff=cutoff, effective_configuration=configuration,
                    checkpoint_callback=checkpoint,
                )
        finally:
            event.remove(contextual_engine, "before_cursor_execute", observe_sql)
        if boundary.startswith("PERSISTENCE_VALIDATION"):
            assert ("SOURCE_ROWS_LOADED", 2, 2) in observed
        assert any(phase == boundary for phase, _, _ in observed)
        assert not cpu_phase_queries  # no lazy ORM query reopened the clean CPU phase
        assert len(writer_evidence_bulk_reads) == len(set(writer_evidence_bulk_reads))
        assert bool(writer_evidence_bulk_reads) == (
            boundary.startswith("PERSISTENCE_VALIDATION")
        )  # each producer JSON loads once; every validation pass still executes
        assert counts(db) == before
        db.commit()  # a caller cannot accidentally publish rejected staged state
    with Session(contextual_engine) as db:
        assert counts(db) == before


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
