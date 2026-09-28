from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.database_safety import run_guarded_alembic_upgrade
from app.models.ceri_tables import (
    CeriCompany,
    CeriDerivedFeature,
    CeriEarningsActual,
    CeriEstimateSnapshot,
    CeriFeatureBuildState,
    CeriFeatureSourceManifest,
    CeriScoreSnapshot,
    CeriSourceRecord,
)
from app.models.tables import BackgroundJob, PipelineRun, RawCompanyRow, UploadRun
from app.observability.correlation import worker_job_scope
from app.services.background_job_service import JobStatus, claim_next_job, mark_job_completed
from app.services.background_worker import JobDeferred
from app.services.ceri.artifact_lineage import CeriArtifactOwnership
from app.services.ceri.batched_job_handlers import execute_feature_batch_job
from app.services.ceri.batched_workflow import (
    CERI_FEATURE_BATCH,
    CERI_NORMALIZE_BATCH,
    CERI_PROVIDER_INGEST_BATCH,
    CERI_RUN_FINALIZE,
)
from app.services.ceri.feature_certification_workflow import (
    CeriFeatureCertificationRequest,
    admit_ceri_feature_certification,
    execute_ceri_feature_certification_job,
)
from app.services.ceri.feature_rebuild_service import (
    CERI_CERTIFICATION_SCOPE_KIND,
    CERI_FULL_PIPELINE_SCOPE_KIND,
    CeriFeatureRebuildRequest,
    CeriFeatureRebuildService,
)
from app.services.ceri.job_handlers import (
    CERI_ALERT_REBUILD,
    CERI_CAPTURE_RUN,
    CERI_CHANGE_DETECTION,
)
from app.services.ceri.parent_pipeline_fence import require_parent_pipeline_active
from app.services.pipeline_prerequisites import CeriParentPipelineTerminalError
from app.services.pipeline_service import PipelineStatus, roll_up_ceri_pipeline_job_failure
from app.services.scope_refresh_adoption import SemanticWorkAuthority, admit_frozen_operation
from app.services.work_scope_identity import AcquisitionRequirement, ScopeMember
from app.services.worker_registry import register_worker
from app.settings import get_settings

ROOT = Path(__file__).resolve().parents[2]


def _upgrade(database_url: str) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    run_guarded_alembic_upgrade(config, database_url, "head")


def test_feature_certification_admission_graph_and_terminal_boundary_are_durable(
    disposable_postgres_database: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _upgrade(disposable_postgres_database)
    monkeypatch.setenv("RUNTIME_MODE", "CERTIFICATION")
    monkeypatch.setenv("RUNTIME_INSTANCE_ID", "feature-certification-test-session")
    monkeypatch.setenv("MARKET_DATA_PREWARM_ENABLED", "false")
    monkeypatch.setenv("WINNER_PROBABILITY_AUTO_COHORT_REFRESH_ENABLED", "false")
    monkeypatch.setenv("WINNER_PROBABILITY_AUTO_MATURATION_ENABLED", "false")
    get_settings.cache_clear()
    engine = create_engine(disposable_postgres_database)
    try:
        with Session(engine, expire_on_commit=False) as db:
            admitted = admit_ceri_feature_certification(
                db,
                CeriFeatureCertificationRequest(
                    tickers=("SYN2", "SYN1"),
                    provider_datasets=("eodhd:earnings", "eodhd:estimates"),
                    checkpoint_interval=1,
                    cutoff_at=datetime(2026, 9, 25, 22, tzinfo=UTC),
                    stop_boundary="FEATURE_ONLY",
                    request_key="synthetic-two-ticker-feature-certification",
                    requested_by="pytest",
                ),
            )
            root = db.get(BackgroundJob, admitted.root_job_id)
            assert root is not None
            assert root.payload_json["tickers"] == ["SYN1", "SYN2"]
            assert root.payload_json["checkpoint_interval"] == 1
            assert root.payload_json["allow_downstream_continuation"] is False
            root.status = JobStatus.RUNNING
            db.flush()
            with worker_job_scope(root), pytest.raises(JobDeferred):
                execute_ceri_feature_certification_job(db, root)

            children = list(
                db.scalars(
                    select(BackgroundJob)
                    .where(BackgroundJob.workflow_key == admitted.workflow_key)
                    .where(BackgroundJob.id != root.id)
                    .order_by(BackgroundJob.priority, BackgroundJob.id)
                )
            )
            assert [child.job_type for child in children] == [
                CERI_PROVIDER_INGEST_BATCH,
                CERI_NORMALIZE_BATCH,
                CERI_PROVIDER_INGEST_BATCH,
                CERI_NORMALIZE_BATCH,
                CERI_FEATURE_BATCH,
            ]
            assert all(child.payload_json["tickers"] == ["SYN1", "SYN2"] for child in children)
            assert all(child.payload_json["checkpoint_interval"] == 1 for child in children)
            assert all(child.parent_job_id == root.id for child in children)

            for child in children:
                child.status = JobStatus.COMPLETED
            db.flush()
            with worker_job_scope(root):
                result = execute_ceri_feature_certification_job(db, root)
            mark_job_completed(db, root, result)
            db.commit()

            pipeline = db.get(PipelineRun, admitted.pipeline_run_id)
            assert pipeline is not None
            assert pipeline.status == PipelineStatus.FEATURE_CERTIFIED
            assert root.status == JobStatus.COMPLETED
            all_jobs = list(db.scalars(select(BackgroundJob)))
            prohibited = {
                CERI_RUN_FINALIZE,
                CERI_CAPTURE_RUN,
                CERI_CHANGE_DETECTION,
                CERI_ALERT_REBUILD,
                "FULL_PIPELINE",
                "SETUP_SIGNAL_CAPTURE",
                "WINNER_PREDICTION_CAPTURE",
                "SETUP_LIFECYCLE_EVALUATION",
            }
            assert prohibited.isdisjoint({job.job_type for job in all_jobs})
            assert db.scalar(select(CeriScoreSnapshot.id).limit(1)) is None

            failed_admission = admit_ceri_feature_certification(
                db,
                CeriFeatureCertificationRequest(
                    tickers=("FAIL1", "FAIL2"),
                    cutoff_at=datetime(2026, 9, 25, 22, tzinfo=UTC),
                    request_key="synthetic-feature-certification-failure",
                    requested_by="pytest",
                ),
            )
            failed_root = db.get(BackgroundJob, failed_admission.root_job_id)
            assert failed_root is not None
            failed_root.status = JobStatus.RUNNING
            db.flush()
            with worker_job_scope(failed_root), pytest.raises(JobDeferred):
                execute_ceri_feature_certification_job(db, failed_root)
            failed_root.status = JobStatus.QUEUED
            failed_children = list(
                db.scalars(
                    select(BackgroundJob)
                    .where(BackgroundJob.workflow_key == failed_admission.workflow_key)
                    .where(BackgroundJob.id != failed_root.id)
                    .order_by(BackgroundJob.id)
                )
            )
            failed_child = failed_children[0]
            failed_child.status = JobStatus.FAILED
            failed_child.completed_at = datetime.now(UTC)
            roll_up_ceri_pipeline_job_failure(db, failed_child)
            db.flush()
            failed_pipeline = db.get(PipelineRun, failed_admission.pipeline_run_id)
            assert failed_pipeline is not None
            assert failed_pipeline.status == PipelineStatus.FAILED
            assert failed_root.status == JobStatus.FAILED
            assert all(
                child.status in {JobStatus.FAILED, JobStatus.BLOCKED}
                for child in failed_children
            )
            fenced_child = next(child for child in failed_children if child is not failed_child)
            with pytest.raises(CeriParentPipelineTerminalError):
                require_parent_pipeline_active(db, fenced_child)
            assert prohibited.isdisjoint(
                set(
                    db.scalars(
                        select(BackgroundJob.job_type).where(
                            BackgroundJob.workflow_key == failed_admission.workflow_key
                        )
                    )
                )
            )
            db.rollback()
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_two_ticker_feature_certification_uses_scope_without_raw_rows_and_stops(
    disposable_postgres_database: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _upgrade(disposable_postgres_database)
    monkeypatch.setenv("RUNTIME_MODE", "CERTIFICATION")
    monkeypatch.setenv("RUNTIME_INSTANCE_ID", "feature-certification-membership-test")
    monkeypatch.setenv("MARKET_DATA_PREWARM_ENABLED", "false")
    monkeypatch.setenv("WINNER_PROBABILITY_AUTO_COHORT_REFRESH_ENABLED", "false")
    monkeypatch.setenv("WINNER_PROBABILITY_AUTO_MATURATION_ENABLED", "false")
    get_settings.cache_clear()
    engine = create_engine(disposable_postgres_database)
    try:
        with Session(engine, expire_on_commit=False) as db:
            admitted = admit_ceri_feature_certification(
                db,
                CeriFeatureCertificationRequest(
                    tickers=("NVDA", "CRM"),
                    provider_datasets=("eodhd:earnings", "eodhd:estimates"),
                    checkpoint_interval=1,
                    cutoff_at=datetime(2026, 9, 25, 22, tzinfo=UTC),
                    stop_boundary="FEATURE_ONLY",
                    request_key="disposable-nvda-crm-membership-regression",
                    requested_by="pytest",
                ),
            )
            root = db.get(BackgroundJob, admitted.root_job_id)
            assert root is not None
            root.status = JobStatus.RUNNING
            db.flush()
            with worker_job_scope(root), pytest.raises(JobDeferred):
                execute_ceri_feature_certification_job(db, root)

            assert (
                db.scalar(
                    select(func.count())
                    .select_from(RawCompanyRow)
                    .where(RawCompanyRow.run_id == admitted.upload_run_id)
                )
                == 0
            )
            authority = SemanticWorkAuthority(
                scope_id=str(root.scope_id),
                refresh_cycle_id=str(root.refresh_cycle_id),
                acquisition_plan_id=str(root.acquisition_plan_id),
            )
            service = CeriFeatureRebuildService()
            certification_request = CeriFeatureRebuildRequest(
                tickers=("CRM", "NVDA"),
                run_id=admitted.upload_run_id,
                cutoff_at=datetime(2026, 9, 25, 22, tzinfo=UTC),
                calculation_context_id=int(root.payload_json["calculation_context_id"]),
                calendar_version=str(root.payload_json["calendar_version"]),
                ownership_mode=CeriArtifactOwnership.PIPELINE.value,
                semantic_authority=authority,
            )
            membership_authority = service._pipeline_membership_authority(
                db, certification_request
            )
            assert membership_authority == CERI_CERTIFICATION_SCOPE_KIND
            selected = service._companies(
                db,
                certification_request,
                membership_authority=membership_authority,
            )
            assert [company.ticker for company in selected] == ["CRM", "NVDA"]
            with pytest.raises(
                ValueError, match="CERI_CERTIFICATION_SOURCE_EVIDENCE_INSUFFICIENT:CRM,NVDA"
            ):
                service.prepare_batch(db, certification_request)

            original_hashes = _seed_two_ticker_feature_sources(db)
            children = list(
                db.scalars(
                    select(BackgroundJob)
                    .where(BackgroundJob.workflow_key == admitted.workflow_key)
                    .where(BackgroundJob.id != root.id)
                    .order_by(BackgroundJob.priority, BackgroundJob.id)
                )
            )
            feature = next(child for child in children if child.job_type == CERI_FEATURE_BATCH)
            for child in children:
                if child is not feature:
                    child.status = JobStatus.COMPLETED
            db.commit()
            _claim_feature_job(db, feature)

            result = execute_feature_batch_job(db, feature)
            assert result["status"] == JobStatus.COMPLETED
            assert result["processed_tickers"] == 2
            assert set(result["results"]) == {"CRM", "NVDA"}
            assert all(
                values["processed_companies"] == 1 for values in result["results"].values()
            )
            mark_job_completed(db, feature, result)
            db.commit()

            manifest = db.scalar(
                select(CeriFeatureSourceManifest).where(
                    CeriFeatureSourceManifest.background_job_id == feature.id
                )
            )
            assert manifest is not None
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(CeriFeatureSourceManifest)
                    .where(
                        CeriFeatureSourceManifest.calculation_context_id
                        == manifest.calculation_context_id
                    )
                )
                == 1
            )
            assert manifest.run_id == admitted.upload_run_id
            assert manifest.pipeline_run_id == admitted.pipeline_run_id
            assert manifest.calculation_context_id == root.payload_json["calculation_context_id"]
            assert manifest.scope_id == root.scope_id
            assert manifest.refresh_cycle_id == root.refresh_cycle_id
            assert manifest.acquisition_plan_id == root.acquisition_plan_id
            manifest_tables = {
                entry["table"] for entry in manifest.manifest_json["entries"]
            }
            assert {
                "ceri_companies",
                "ceri_earnings_actuals",
                "ceri_estimate_snapshots",
                "ceri_source_records",
            }.issubset(manifest_tables)
            checkpoint = feature.operational_metadata_json["ceri_batch"]
            assert checkpoint["completed_tickers"] == ["CRM", "NVDA"]
            assert checkpoint["processed"] == 2

            build_states = list(
                db.scalars(
                    select(CeriFeatureBuildState).where(
                        CeriFeatureBuildState.calculation_context_id
                        == manifest.calculation_context_id
                    )
                )
            )
            assert len(build_states) == 2
            assert {row.source_manifest_id for row in build_states} == {manifest.id}
            company_ids = {row.company_id for row in build_states}
            assert len(company_ids) == 2
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(CeriDerivedFeature)
                    .where(
                        CeriDerivedFeature.calculation_context_id
                        == manifest.calculation_context_id,
                        CeriDerivedFeature.feature_family == "earnings_surprise",
                    )
                )
                == 2
            )
            retained_hashes = dict(
                db.execute(
                    select(CeriSourceRecord.provider_record_id, CeriSourceRecord.content_hash)
                    .where(CeriSourceRecord.provider_record_id.in_(original_hashes))
                ).all()
            )
            assert retained_hashes == original_hashes

            with worker_job_scope(root):
                root_result = execute_ceri_feature_certification_job(db, root)
            mark_job_completed(db, root, root_result)
            db.commit()
            pipeline = db.get(PipelineRun, admitted.pipeline_run_id)
            assert pipeline is not None
            assert pipeline.status == PipelineStatus.FEATURE_CERTIFIED
            prohibited = {
                CERI_RUN_FINALIZE,
                CERI_CAPTURE_RUN,
                CERI_CHANGE_DETECTION,
                CERI_ALERT_REBUILD,
                "FULL_PIPELINE",
                "SETUP_SIGNAL_CAPTURE",
                "WINNER_PREDICTION_CAPTURE",
                "SETUP_LIFECYCLE_EVALUATION",
            }
            assert prohibited.isdisjoint(
                set(
                    db.scalars(
                        select(BackgroundJob.job_type).where(
                            BackgroundJob.workflow_key == admitted.workflow_key
                        )
                    )
                )
            )
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(CeriScoreSnapshot)
                    .where(CeriScoreSnapshot.run_id == admitted.upload_run_id)
                )
                == 0
            )

            certification_request = CeriFeatureRebuildRequest(
                tickers=("CRM", "NVDA", "AAPL"),
                run_id=admitted.upload_run_id,
                cutoff_at=datetime(2026, 9, 25, 22, tzinfo=UTC),
                calculation_context_id=manifest.calculation_context_id,
                calendar_version=manifest.calendar_version,
                ownership_mode=CeriArtifactOwnership.PIPELINE.value,
                semantic_authority=authority,
            )
            with pytest.raises(
                ValueError, match="CERI_CERTIFICATION_SCOPE_MEMBERSHIP_INVALID:BATCH_SCOPE_MISMATCH"
            ):
                service.prepare_batch(db, certification_request)

            _assert_full_pipeline_membership_still_uses_raw_rows(
                db,
                service=service,
                certification_run_id=admitted.upload_run_id,
            )
            _assert_invalid_certification_memberships(db, service=service)
            db.rollback()
    finally:
        engine.dispose()
        get_settings.cache_clear()


def _seed_two_ticker_feature_sources(db: Session) -> dict[str, str]:
    companies = {
        company.ticker.upper(): company
        for company in db.scalars(
            select(CeriCompany).where(func.upper(CeriCompany.ticker).in_(("CRM", "NVDA")))
        )
    }
    assert set(companies) == {"CRM", "NVDA"}
    known_at = datetime(2026, 9, 24, 18, tzinfo=UTC)
    hashes: dict[str, str] = {}
    for ticker, company in sorted(companies.items()):
        estimate_key = f"{ticker}-estimate"
        earnings_key = f"{ticker}-earnings"
        estimate_hash = f"hash-{estimate_key}"
        earnings_hash = f"hash-{earnings_key}"
        hashes[estimate_key] = estimate_hash
        hashes[earnings_key] = earnings_hash
        estimate_source = CeriSourceRecord(
            provider="eodhd",
            provider_terms_version="disposable-v1",
            dataset="estimates",
            provider_record_id=estimate_key,
            company_hint_json={"ticker": ticker, "provider_company_id": f"{ticker}.US"},
            published_at=known_at,
            observed_at=known_at,
            source_timestamp=known_at,
            retrieved_at=known_at,
            ingested_at=known_at,
            restricted_normalized_json={"ticker": ticker, "consensus": "10"},
            content_hash=estimate_hash,
            normalized_hash=f"normalized-{estimate_key}",
            idempotency_key=f"idempotency-{estimate_key}",
            export_policy="exportable",
        )
        earnings_source = CeriSourceRecord(
            provider="eodhd",
            provider_terms_version="disposable-v1",
            dataset="earnings",
            provider_record_id=earnings_key,
            company_hint_json={"ticker": ticker, "provider_company_id": f"{ticker}.US"},
            published_at=known_at,
            observed_at=known_at,
            source_timestamp=known_at,
            retrieved_at=known_at,
            ingested_at=known_at,
            restricted_normalized_json={"ticker": ticker, "actual": "11"},
            content_hash=earnings_hash,
            normalized_hash=f"normalized-{earnings_key}",
            idempotency_key=f"idempotency-{earnings_key}",
            export_policy="exportable",
        )
        db.add_all([estimate_source, earnings_source])
        db.flush()
        db.add_all(
            [
                CeriEstimateSnapshot(
                    source_record_id=estimate_source.id,
                    company_id=company.id,
                    metric="EPS_DILUTED",
                    fiscal_period_end=datetime(2026, 9, 30).date(),
                    period_type="CURRENT_QUARTER",
                    canonical_period_slot="CURRENT_QUARTER",
                    consensus=Decimal("10"),
                    effective_at=known_at,
                    reference_at=known_at,
                    known_at=known_at,
                    retrieved_at=known_at,
                    effective_session=known_at.date(),
                    provider_observed_at=known_at,
                    source_timestamp=known_at,
                    source_provider="eodhd",
                    canonical_observation_key=f"{ticker}:EPS_DILUTED:CURRENT_QUARTER",
                ),
                CeriEarningsActual(
                    source_record_id=earnings_source.id,
                    company_id=company.id,
                    metric="EPS_DILUTED",
                    period_type="CURRENT_QUARTER",
                    fiscal_period_end=datetime(2026, 9, 30).date(),
                    report_at=known_at,
                    report_session=known_at.date(),
                    actual_value=Decimal("11"),
                    provider_consensus_value=Decimal("10"),
                    provider_surprise_pct=Decimal("10"),
                    event_kind="REPORTED",
                    provider_consensus_semantics="REPORT_TIME_CONSENSUS",
                ),
            ]
        )
    db.flush()
    return hashes


def _claim_feature_job(db: Session, job: BackgroundJob) -> None:
    worker_id = f"feature-certification-fixture-{job.id}"
    worker = register_worker(
        db,
        worker_id=worker_id,
        queues=("interactive", "broker", "background"),
        heartbeat_timeout_seconds=30,
        hostname="feature-certification-disposable",
        process_id=job.id,
    )
    db.commit()
    excluded = tuple(
        job_type
        for job_type in db.scalars(select(BackgroundJob.job_type).distinct())
        if job_type != job.job_type
    )
    claimed = claim_next_job(
        db,
        worker_id=worker_id,
        worker_instance_id=worker.instance_id,
        excluded_job_types=excluded,
    )
    assert claimed is not None and claimed.id == job.id
    db.commit()


def _assert_full_pipeline_membership_still_uses_raw_rows(
    db: Session,
    *,
    service: CeriFeatureRebuildService,
    certification_run_id: int,
) -> None:
    run = UploadRun(filename="normal-membership.csv", row_count=1, status="PROCESSING")
    db.add(run)
    db.flush()
    db.add(
        RawCompanyRow(
            run_id=run.id,
            row_number=1,
            ticker="CRM",
            raw_json={"ticker": "CRM"},
        )
    )
    authority = admit_frozen_operation(
        db,
        operation_kind=CERI_FULL_PIPELINE_SCOPE_KIND,
        subject_kind="ticker",
        members=(ScopeMember("TICKER", "CRM"), ScopeMember("TICKER", "NVDA")),
        cycle_key=f"normal-membership:{run.id}",
        business_cutoff=datetime(2026, 9, 25, 22, tzinfo=UTC),
        provider_source_class="CERI",
        request_type="FULL_PIPELINE",
        requirements=(AcquisitionRequirement("CERI_SOURCE_RECORDS"),),
        policy_identity="normal-membership-regression-v1",
        scope_definition={"run_id": run.id, "tickers": ["CRM", "NVDA"]},
    )
    request = CeriFeatureRebuildRequest(
        tickers=("CRM", "NVDA"),
        run_id=run.id,
        ownership_mode=CeriArtifactOwnership.PIPELINE.value,
        semantic_authority=authority,
    )
    membership_authority = service._pipeline_membership_authority(db, request)
    assert membership_authority == CERI_FULL_PIPELINE_SCOPE_KIND
    selected = service._companies(
        db,
        request,
        membership_authority=membership_authority,
    )
    assert [company.ticker for company in selected] == ["CRM"]
    assert run.id != certification_run_id
    certification_authority = service._pipeline_membership_authority(
        db,
        CeriFeatureRebuildRequest(
            tickers=("CRM", "NVDA"),
            run_id=certification_run_id,
            ownership_mode=CeriArtifactOwnership.PIPELINE.value,
            semantic_authority=SemanticWorkAuthority(
                scope_id=next(
                    str(job.scope_id)
                    for job in db.scalars(select(BackgroundJob))
                    if job.related_run_id == certification_run_id and job.scope_id
                ),
                refresh_cycle_id=next(
                    str(job.refresh_cycle_id)
                    for job in db.scalars(select(BackgroundJob))
                    if job.related_run_id == certification_run_id and job.refresh_cycle_id
                ),
                acquisition_plan_id=next(
                    str(job.acquisition_plan_id)
                    for job in db.scalars(select(BackgroundJob))
                    if job.related_run_id == certification_run_id and job.acquisition_plan_id
                ),
            ),
        ),
    )
    assert certification_authority == CERI_CERTIFICATION_SCOPE_KIND


def _assert_invalid_certification_memberships(
    db: Session, *, service: CeriFeatureRebuildService
) -> None:
    unresolved = admit_frozen_operation(
        db,
        operation_kind=CERI_CERTIFICATION_SCOPE_KIND,
        subject_kind="ticker",
        members=(ScopeMember("TICKER", "MISS1"), ScopeMember("TICKER", "MISS2")),
        cycle_key="certification-membership-unresolved",
        business_cutoff=datetime(2026, 9, 25, 22, tzinfo=UTC),
        provider_source_class="CERI_EODHD",
        request_type="CERI_FEATURE_CERTIFICATION",
        requirements=(AcquisitionRequirement("CERI_SOURCE_RECORDS"),),
        policy_identity="certification-membership-test-v1",
        scope_definition={"tickers": ["MISS1", "MISS2"]},
    )
    with pytest.raises(
        ValueError,
        match="CERI_CERTIFICATION_SCOPE_MEMBERSHIP_INVALID:UNRESOLVED_OR_OUTSIDE_SCOPE",
    ):
        service._companies(
            db,
            CeriFeatureRebuildRequest(
                tickers=("MISS1", "MISS2"), semantic_authority=unresolved
            ),
            membership_authority=CERI_CERTIFICATION_SCOPE_KIND,
        )

    ambiguous = admit_frozen_operation(
        db,
        operation_kind=CERI_CERTIFICATION_SCOPE_KIND,
        subject_kind="ticker",
        members=(ScopeMember("TICKER", "AMB"), ScopeMember("TICKER", "OK1")),
        cycle_key="certification-membership-ambiguous",
        business_cutoff=datetime(2026, 9, 25, 22, tzinfo=UTC),
        provider_source_class="CERI_EODHD",
        request_type="CERI_FEATURE_CERTIFICATION",
        requirements=(AcquisitionRequirement("CERI_SOURCE_RECORDS"),),
        policy_identity="certification-membership-test-v1",
        scope_definition={"tickers": ["AMB", "OK1"]},
    )
    db.add_all(
        [
            CeriCompany(
                ticker="AMB",
                exchange="US",
                current_provider_ids_json={"eodhd": "AMB.US"},
            ),
            CeriCompany(
                ticker="AMB",
                exchange="XNAS",
                current_provider_ids_json={"eodhd": "AMB.US"},
            ),
            CeriCompany(
                ticker="OK1",
                exchange="US",
                current_provider_ids_json={"eodhd": "OK1.US"},
            ),
        ]
    )
    db.flush()
    with pytest.raises(
        ValueError,
        match="CERI_CERTIFICATION_SCOPE_MEMBERSHIP_INVALID:AMBIGUOUS_CANONICAL_COMPANY",
    ):
        service._companies(
            db,
            CeriFeatureRebuildRequest(tickers=("AMB", "OK1"), semantic_authority=ambiguous),
            membership_authority=CERI_CERTIFICATION_SCOPE_KIND,
        )

    conflict = admit_frozen_operation(
        db,
        operation_kind=CERI_CERTIFICATION_SCOPE_KIND,
        subject_kind="ticker",
        members=(ScopeMember("TICKER", "BAD1"), ScopeMember("TICKER", "OK2")),
        cycle_key="certification-membership-provider-conflict",
        business_cutoff=datetime(2026, 9, 25, 22, tzinfo=UTC),
        provider_source_class="CERI_EODHD",
        request_type="CERI_FEATURE_CERTIFICATION",
        requirements=(AcquisitionRequirement("CERI_SOURCE_RECORDS"),),
        policy_identity="certification-membership-test-v1",
        scope_definition={"tickers": ["BAD1", "OK2"]},
    )
    db.add_all(
        [
            CeriCompany(
                ticker="BAD1",
                exchange="US",
                current_provider_ids_json={"eodhd": "OTHER.US"},
            ),
            CeriCompany(
                ticker="OK2",
                exchange="US",
                current_provider_ids_json={"eodhd": "OK2.US"},
            ),
        ]
    )
    db.flush()
    with pytest.raises(
        ValueError,
        match="CERI_CERTIFICATION_SCOPE_MEMBERSHIP_INVALID:PROVIDER_IDENTITY_CONFLICT:BAD1",
    ):
        service._companies(
            db,
            CeriFeatureRebuildRequest(tickers=("BAD1", "OK2"), semantic_authority=conflict),
            membership_authority=CERI_CERTIFICATION_SCOPE_KIND,
        )
