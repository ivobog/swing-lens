from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.database_safety import run_guarded_alembic_upgrade
from app.models.ceri_tables import (
    CeriCompany,
    CeriEstimateSnapshot,
    CeriEvidenceDisposition,
    CeriIngestionRunSourceRecord,
    CeriProcessingRun,
    CeriScoreSnapshot,
    CeriSourceRecord,
)
from app.models.tables import (
    CoreCalculationCurrentProjection,
    CoreCalculationEvidence,
    MarketCalculationContext,
    PipelineRun,
    RawCompanyRow,
    UploadRun,
)
from app.services.ceri.capture_service import CeriRunCaptureService
from app.services.ceri.dtos import RawProviderRecord
from app.services.ceri.enums import CeriDataset
from app.services.ceri.evidence_eligibility import eligible_snapshot_select
from app.services.ceri.normalization_service import CeriNormalizationService
from app.services.ceri.pipeline_containment_service import (
    reconcile_unsuccessful_pipeline_ceri,
)
from app.services.ceri.run_local_contract import provider_no_data_is_explicit
from app.services.ceri.snapshot_service import CeriSnapshotService
from app.services.ceri.source_record_service import CeriSourceRecordService
from app.services.contextual_effective_configuration import resolve_ceri_configuration
from app.services.market_clock_service import MarketClockService


def test_cancelled_source_owner_does_not_own_later_deduplicated_normalization(
    disposable_postgres_database: str,
) -> None:
    """Scenarios A/B: B closes its own membership, reusing only valid rows."""

    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine, expire_on_commit=False) as db:
        db.add(CeriCompany(ticker="MSFT", exchange="NASDAQ"))
        service = CeriSourceRecordService()
        run_a = service.create_ingestion_run(
            db,
            provider="manual",
            provider_terms_version="fixture-v1",
            dataset="estimates",
            scope={"ticker": "MSFT", "run_id": 1001},
            request_key="isolation:A",
            config_version="test-v1",
            config_hash="test-hash",
        )
        first = service.store_source_record(
            db,
            ingestion_run_id=run_a.id,
            record=_estimate("MSFT:FY1", "2026-12-31", "14.25"),
            raw_payload_allowed=True,
        ).source_record
        second = service.store_source_record(
            db,
            ingestion_run_id=run_a.id,
            record=_estimate("MSFT:FY2", "2027-12-31", "15.25"),
            raw_payload_allowed=True,
        ).source_record
        service.finish_ingestion_run(
            db,
            run_a,
            status="CANCELLED",
            requested_count=2,
            fetched_count=2,
            inserted_count=2,
            deduplicated_count=0,
            corrected_count=0,
            quarantined_count=0,
            failed_count=0,
            warning_count=0,
        )
        partial = CeriProcessingRun(
            job_type="CERI_NORMALIZE_BATCH",
            status="RUNNING",
            deterministic_request_key="isolation:A:partial",
            started_at=datetime.now(UTC),
        )
        db.add(partial)
        db.flush()
        CeriNormalizationService().normalize(
            db, processing_run=partial, source_records=[first]
        )
        db.commit()

        run_b = service.create_ingestion_run(
            db,
            provider="manual",
            provider_terms_version="fixture-v1",
            dataset="estimates",
            scope={"ticker": "MSFT", "run_id": 1002},
            request_key="isolation:B",
            config_version="test-v1",
            config_hash="test-hash",
        )
        for record in (
            _estimate("MSFT:FY1", "2026-12-31", "14.25"),
            _estimate("MSFT:FY2", "2027-12-31", "15.25"),
        ):
            assert service.store_source_record(
                db,
                ingestion_run_id=run_b.id,
                record=record,
                raw_payload_allowed=True,
            ).deduplicated
        service.finish_ingestion_run(
            db,
            run_b,
            status="COMPLETED",
            requested_count=2,
            fetched_count=2,
            inserted_count=0,
            deduplicated_count=2,
            corrected_count=0,
            quarantined_count=0,
            failed_count=0,
            warning_count=0,
        )
        processing_b = CeriProcessingRun(
            job_type="CERI_NORMALIZE_BATCH",
            status="RUNNING",
            deterministic_request_key="isolation:B:normalize",
            started_at=datetime.now(UTC),
        )
        db.add(processing_b)
        db.flush()
        result = CeriNormalizationService().normalize(
            db, processing_run=processing_b, ingestion_run_id=run_b.id
        )
        db.commit()

        memberships = list(
            db.scalars(
                select(CeriIngestionRunSourceRecord)
                .where(CeriIngestionRunSourceRecord.ingestion_run_id == run_b.id)
                .order_by(CeriIngestionRunSourceRecord.source_record_id)
            )
        )
        assert result.status == "COMPLETED"
        assert {row.ingestion_outcome for row in memberships} == {"DEDUPLICATED"}
        assert {row.normalization_state for row in memberships} == {"REUSED", "NORMALIZED"}
        assert processing_b.counts_json["closure"]["referenced"] == 2
        assert processing_b.counts_json["closure"]["terminal"] == 2
        assert db.scalar(select(func.count()).select_from(CeriSourceRecord)) == 2
        assert db.scalar(select(func.count()).select_from(CeriEstimateSnapshot)) == 2
        assert second.ingestion_run_id == run_a.id
    engine.dispose()


def test_provider_empty_is_an_explicit_run_local_result(
    disposable_postgres_database: str,
) -> None:
    """Scenario C: no-data is positive workflow evidence, not missing work."""

    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        db.add(UploadRun(id=2001, filename="provider-empty.csv", row_count=1, status="COMPLETED"))
        db.add(CeriCompany(ticker="JOE", exchange="US"))
        db.add(RawCompanyRow(run_id=2001, row_number=1, ticker="JOE", raw_json={}))
        db.add(
            _ingestion(
                request_key="provider-empty",
                run_id=2001,
                ticker="JOE",
                fetched_count=0,
            )
        )
        db.commit()
        assert provider_no_data_is_explicit(db, run_id=2001, ticker="JOE") is True
        assert provider_no_data_is_explicit(db, run_id=2001, ticker="NVDA") is False
        snapshot_service = CeriSnapshotService()
        frozen = resolve_ceri_configuration(
            snapshot_service.config,
            consumer={
                "run_capture": True,
                "revision_feature_config_hash": snapshot_service.config.config_hash,
                "ibmi_enabled": False,
                "volatility_enabled": False,
                "short_pressure_enabled": False,
                "volatility": {"ceri_risk_max_contribution": 1.5},
            },
        )
        result = CeriRunCaptureService(snapshot_service=snapshot_service).capture_run(
            db,
            2001,
            force=True,
            market_cutoff=MarketClockService().cutoff_for(
                datetime(2026, 9, 30, 14, tzinfo=UTC),
                reason="PROVIDER_EMPTY_CONTRACT_TEST",
            ),
            effective_configuration=frozen,
        )
        db.commit()
        snapshots = list(
            db.scalars(select(CeriScoreSnapshot).where(CeriScoreSnapshot.run_id == 2001))
        )
        assert result.score_snapshots == result.unrated == 1
        assert len(snapshots) == 1
        assert snapshots[0].opportunity_unrated_reason == "NO_ELIGIBLE_ESTIMATE_INPUT"
        assert snapshots[0].opportunity_coverage_pct == 0.0
        assert snapshots[0].evidence_id is not None
    engine.dispose()


def test_failed_pipeline_excludes_evidence_repairs_projection_and_processing(
    disposable_postgres_database: str,
) -> None:
    """Scenarios D/E: failure is fail-closed and terminalizes child state."""

    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine, expire_on_commit=False) as db:
        upload = UploadRun(filename="failed.csv", row_count=1, status="FAILED")
        db.add(upload)
        db.flush()
        pipeline = PipelineRun(upload_run_id=upload.id, status="FAILED")
        db.add(pipeline)
        db.flush()
        context = MarketCalculationContext(
            # Reproduce the incident's missing context FK; ownership must still
            # fail closed through the upload/run scope.
            pipeline_run_id=None,
            upload_run_id=upload.id,
            cutoff_at=datetime(2026, 9, 30, 14, tzinfo=UTC),
            exchange_timezone="America/New_York",
            latest_completed_session=date(2026, 9, 29),
            calendar_version="test-v1",
            bar_readiness_version="test-v1",
            cutoff_reason="TEST",
        )
        company = CeriCompany(ticker="AAA", exchange="US")
        db.add_all([context, company])
        db.flush()
        evidence = CoreCalculationEvidence(
            artifact_kind="CERI",
            run_id=upload.id,
            ticker="AAA",
            calculation_identity_fingerprint="identity",
            calculation_identity_json={"test": True},
            payload_fingerprint="payload",
            payload_json={"test": True},
            source_evidence_ids_json={},
            evidence_key="failed-evidence",
            calculated_at=context.cutoff_at,
        )
        db.add(evidence)
        db.flush()
        snapshot = _snapshot(
            run_id=upload.id,
            company_id=company.id,
            context_id=context.id,
            evidence_id=evidence.id,
        )
        processing = CeriProcessingRun(
            job_type="CERI_NORMALIZE_BATCH",
            status="RUNNING",
            deterministic_request_key=f"ceri:pipeline:{pipeline.id}:normalize:AAA",
            scope_json={"run_id": upload.id, "ticker": "AAA"},
            started_at=datetime.now(UTC),
        )
        db.add_all(
            [
                snapshot,
                processing,
                CoreCalculationCurrentProjection(
                    artifact_kind="CERI",
                    run_id=upload.id,
                    ticker="AAA",
                    ranking_profile_key="",
                    evidence_id=evidence.id,
                ),
            ]
        )
        db.commit()

        result = reconcile_unsuccessful_pipeline_ceri(db, pipeline)
        db.commit()

        assert result == {
            "excluded": 1,
            "projections_repaired": 1,
            "processing_terminalized": 1,
        }
        assert db.scalar(select(func.count()).select_from(CeriEvidenceDisposition)) == 1
        assert list(db.scalars(eligible_snapshot_select())) == []
        assert db.scalar(select(func.count()).select_from(CoreCalculationCurrentProjection)) == 0
        assert db.get(CeriProcessingRun, processing.id).status == "FAILED"

        # Reconciliation is idempotent.
        again = reconcile_unsuccessful_pipeline_ceri(db, pipeline)
        db.commit()
        assert again["excluded"] == 0
        assert db.scalar(select(func.count()).select_from(CeriEvidenceDisposition)) == 1
    engine.dispose()


def _estimate(provider_record_id: str, period_end: str, consensus: str) -> RawProviderRecord:
    observed = datetime(2026, 9, 30, 10, tzinfo=UTC)
    return RawProviderRecord(
        provider="manual",
        dataset=CeriDataset.ESTIMATES,
        provider_record_id=provider_record_id,
        payload={
            "ticker": "MSFT",
            "exchange": "NASDAQ",
            "metric": "EPS_DILUTED",
            "period_type": "ANNUAL",
            "fiscal_period_end": period_end,
            "consensus": consensus,
            "currency": "USD",
            "published_at": observed.isoformat(),
        },
        published_at=observed,
        observed_at=observed,
    )


def _ingestion(*, request_key: str, run_id: int, ticker: str, fetched_count: int):
    from app.models.ceri_tables import CeriIngestionRun

    return CeriIngestionRun(
        provider="eodhd",
        dataset="estimates",
        scope_json={"run_id": run_id, "ticker": ticker},
        status="COMPLETED",
        request_key=request_key,
        requested_count=1,
        fetched_count=fetched_count,
        completed_at=datetime.now(UTC),
    )


def _snapshot(
    *, run_id: int, company_id: int, context_id: int, evidence_id: int
) -> CeriScoreSnapshot:
    return CeriScoreSnapshot(
        evidence_id=evidence_id,
        run_id=run_id,
        source_run_id_text=str(run_id),
        company_id=company_id,
        ticker="AAA",
        as_of_session=date(2026, 9, 29),
        cutoff_at=datetime(2026, 9, 30, 14, tzinfo=UTC),
        calculation_context_id=context_id,
        calendar_version="test-v1",
        opportunity_score=None,
        opportunity_coverage_pct=0.0,
        opportunity_unrated_reason="NO_ELIGIBLE_ESTIMATE_INPUT",
        event_risk_score=0.0,
        data_confidence="Low",
        coverage_pct=0.0,
        posture="NEUTRAL",
        config_version="test-v1",
        config_hash="test-hash",
        calculation_version="test-v1",
        evidence_contract_version="test-v1",
        comparison_state="NO_PRIOR_COMPARABLE_SNAPSHOT",
        evidence_hash="snapshot-hash",
    )


def _upgrade(database_url: str) -> None:
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    run_guarded_alembic_upgrade(config, database_url, "head")
