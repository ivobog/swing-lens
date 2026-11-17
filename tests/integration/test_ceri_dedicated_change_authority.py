"""A real sealed historical comparator must support the dedicated CERI owner."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
import test_contextual_configuration_adoption_postgresql as contextual
from sqlalchemy import select
from sqlalchemy.orm import Session

contextual_engine = contextual.contextual_engine
pytestmark = [pytest.mark.integration, pytest.mark.destructive]


def test_native_dedicated_capture_persists_score_change_with_strict_owner(
    contextual_engine, monkeypatch
):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "e2e"))
    from single_run_certification.fixtures import _seed_ceri_manual_evidence

    from app.models.ceri_tables import CeriChangeEvent, CeriCompany, CeriScoreSnapshot
    from app.models.tables import MarketCalculationContext, PipelineRun, RawCompanyRow, UploadRun
    from app.services.background_job_service import enqueue_job
    from app.services.ceri.capture_service import CeriRunCaptureService
    from app.services.ceri.feature_rebuild_service import (
        CeriFeatureRebuildRequest,
        CeriFeatureRebuildService,
    )
    from app.services.ceri.snapshot_service import CeriSnapshotService
    from app.services.configuration_delivery import (
        binding_reference,
        configuration_delivery_scope,
        load_configuration_delivery,
    )
    from app.services.contextual_effective_configuration import ContextualEffectiveConfiguration
    from app.services.market_calculation_context_service import (
        PipelineCalculationContextError,
        assert_pipeline_calculation_context,
        create_or_get_pipeline_ceri_context,
        create_pipeline_market_context,
    )
    from app.services.market_clock_service import MarketClockService
    from app.services.scope_refresh_adoption import admit_frozen_operation, bind_semantic_authority
    from app.services.work_scope_identity import AcquisitionRequirement, ScopeMember
    from app.settings import Settings

    enabled = Settings(
        _env_file=None,
        ceri_enabled=True,
        ceri_run_capture_enabled=True,
        ceri_provider_ingest_enabled=True,
        ib_market_intelligence_enabled=False,
        ib_volatility_intelligence_enabled=False,
        ib_short_pressure_enabled=False,
    )
    monkeypatch.setattr("app.settings.get_settings", lambda: enabled)
    monkeypatch.setattr("app.services.ceri.feature_flags.get_settings", lambda: enabled)
    with Session(contextual_engine) as db:
        session = MarketClockService().cutoff_for(datetime.now(UTC), reason="COMPARISON_FIXTURE")
        _seed_ceri_manual_evidence(
            db, as_of_session=session.latest_completed_session, tickers=("ALFA",)
        )
        db.commit()
        prior = db.scalar(select(CeriScoreSnapshot))
        assert prior.evidence_id is not None
        db.add(UploadRun(id=777, filename="dedicated-native-change.csv", status="COMPLETED"))
        db.flush()
        db.add(RawCompanyRow(run_id=777, row_number=1, ticker="ALFA", raw_json={}))
        authority = admit_frozen_operation(
            db,
            operation_kind="full-pipeline-run",
            subject_kind="ticker",
            members=(ScopeMember("TICKER", "ALFA"),),
            cycle_key="dedicated-score-change",
            business_cutoff=session.latest_completed_session,
            provider_source_class="CERI",
            request_type="FEATURE_REBUILD",
            requirements=(AcquisitionRequirement("CERI_SOURCE_RECORDS"),),
            policy_identity="native-dedicated-change-regression",
            scope_definition={"tickers": ["ALFA"]},
        )
        owner = PipelineRun(upload_run_id=777, status="RUNNING", result_json={})
        bind_semantic_authority(owner, authority)
        db.add(owner)
        db.flush()
        main = create_pipeline_market_context(db, owner, cutoff_at=datetime.now(UTC))
        enqueue_job(db, "FULL_PIPELINE", {"pipeline_run_id": owner.id}, related_run_id=777)
        cutoff = create_or_get_pipeline_ceri_context(db, pipeline_run_id=owner.id)
        delivery = load_configuration_delivery(db, binding_reference(db, pipeline_run_id=owner.id))
        frozen = ContextualEffectiveConfiguration(
            delivery.configurations["contextual.ceri"].snapshot
        )
        company = db.scalar(select(CeriCompany).where(CeriCompany.ticker == "ALFA"))
        with configuration_delivery_scope(delivery):
            rebuilt = CeriFeatureRebuildService(config=frozen.ceri_config()).rebuild(
                db,
                CeriFeatureRebuildRequest(
                    company_ids=(company.id,),
                    run_id=777,
                    as_of_session=cutoff.latest_completed_session,
                    cutoff_at=cutoff.cutoff_at,
                    calculation_context_id=cutoff.context_id,
                    calendar_version=cutoff.calendar_version,
                    ownership_mode="PIPELINE",
                    semantic_authority=authority,
                ),
            )
            assert rebuilt.failed == 0 and rebuilt.features > 0
            result = CeriRunCaptureService(
                snapshot_service=CeriSnapshotService(effective_configuration=frozen)
            ).capture_run(
                db,
                777,
                force=True,
                market_cutoff=cutoff,
                effective_configuration=frozen,
                expected_pipeline_id=owner.id,
            )
        assert result.score_snapshots == 1 and result.change_events > 0 and result.failed == 0
        db.commit()
        current = db.scalar(select(CeriScoreSnapshot).where(CeriScoreSnapshot.run_id == 777))
        changes = list(
            db.scalars(select(CeriChangeEvent).where(CeriChangeEvent.to_snapshot_id == current.id))
        )
        assert changes and all(row.from_snapshot_id == prior.id for row in changes)
        assert (
            current.calculation_context_id == cutoff.context_id and current.evidence_id is not None
        )
        assert db.get(MarketCalculationContext, cutoff.context_id).pipeline_run_id is None
        assert db.get(MarketCalculationContext, main.context_id).pipeline_run_id == owner.id
        assert (
            assert_pipeline_calculation_context(
                db,
                pipeline_run_id=owner.id,
                upload_run_id=777,
                supplied_context=cutoff,
                context_role="CERI",
            ).context_id
            == cutoff.context_id
        )
        with pytest.raises(PipelineCalculationContextError, match="not owned by a pipeline"):
            assert_pipeline_calculation_context(
                db, pipeline_run_id=owner.id, upload_run_id=777, supplied_context=cutoff
            )
        sibling = PipelineRun(
            upload_run_id=777,
            status="RUNNING",
            result_json={"ceri_calculation_context_id": cutoff.context_id},
        )
        db.add(sibling)
        db.flush()
        with pytest.raises(PipelineCalculationContextError, match="owner binding mismatch"):
            assert_pipeline_calculation_context(
                db,
                pipeline_run_id=owner.id,
                upload_run_id=777,
                supplied_context=cutoff,
                context_role="CERI",
            )
        db.rollback()
