from __future__ import annotations

from copy import deepcopy
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from app.database_safety import run_guarded_alembic_upgrade
from app.models.ceri_tables import (
    CeriCompany,
    CeriDerivedFeature,
    CeriEarningsActual,
    CeriFeatureSourceManifest,
    CeriSourceRecord,
)
from app.models.tables import (
    BackgroundJob,
    ExecutionConfigurationAnchor,
    MarketCalculationContext,
    PipelineRun,
    SetupSignalSnapshot,
    UploadRun,
    WinnerPredictionSnapshot,
)
from app.services.background_job_service import JobStatus
from app.services.ceri.source_manifest_service import (
    freeze_or_verify_feature_source_manifest,
)
from app.services.ceri.surprise_feature_service import CeriSurpriseFeatureService
from app.services.source_mutation_authority import (
    PrefetchedSourceBodies,
    prefetched_source_scope,
)


def test_multi_ticker_checkpoints_keep_earnings_source_pure(
    disposable_postgres_database: str,
) -> None:
    """Incident regression A/G: ticker 1 commit cannot poison ticker 2."""

    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    source_updates: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def capture_source_updates(_conn, _cursor, statement, _params, _context, _many):
        normalized = " ".join(statement.lower().split())
        if normalized.startswith("update ceri_earnings_actuals"):
            source_updates.append(normalized)

    with Session(engine, expire_on_commit=False) as db:
        earnings = _seed_earnings(db, ("AAA", "BBB"))
        bundle = PrefetchedSourceBodies(db)
        loaded = bundle.load(
            CeriEarningsActual,
            select(CeriEarningsActual).order_by(CeriEarningsActual.id),
        )
        bundle.seal()
        sealed_bodies = deepcopy(dict(bundle.bodies))

        service = CeriSurpriseFeatureService()
        for row in loaded:
            with prefetched_source_scope(db, bundle):
                calculated = service.attach_consensus_snapshot(row, [])
                db.add(
                    CeriDerivedFeature(
                        company_id=row.company_id,
                        feature_family="earnings_surprise",
                        feature_key=f"actual:{row.id}",
                        as_of_session=date(2026, 9, 25),
                        value_json={
                            "features": [
                                {
                                    "earnings_actual_id": calculated.earnings_actual_id,
                                    "earnings_source_record_id": (
                                        calculated.earnings_source_record_id
                                    ),
                                    "consensus_selection_reason": (
                                        calculated.consensus_selection_reason
                                    ),
                                    "surprise_absolute": str(calculated.surprise_absolute),
                                    "surprise_pct": str(calculated.surprise_pct),
                                }
                            ]
                        },
                        source_ids_json=[row.source_record_id],
                        evidence_hash=f"earnings-surprise:{row.id}",
                        config_version="test-v1",
                        config_hash="test-config",
                        calculation_version="test-v1",
                        ownership_mode="STANDALONE",
                    )
                )
            db.commit()

        persisted = list(db.scalars(select(CeriEarningsActual).order_by(CeriEarningsActual.id)))
        outputs = list(db.scalars(select(CeriDerivedFeature).order_by(CeriDerivedFeature.id)))

        assert len(earnings) == len(persisted) == len(outputs) == 2
        assert source_updates == []
        assert deepcopy(dict(bundle.bodies)) == sealed_bodies
        assert all(row.consensus_selection_reason is None for row in persisted)
        assert all(row.surprise_absolute is None for row in persisted)
        assert all(row.surprise_pct is None for row in persisted)
        assert [Decimal(row.value_json["features"][0]["surprise_pct"]) for row in outputs] == [
            Decimal("20"),
            Decimal("20"),
        ]

    engine.dispose()


def test_retry_rejects_changed_source_before_new_output(
    disposable_postgres_database: str,
) -> None:
    """Incident regression B: retry cannot silently adopt a changed row body."""

    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine, expire_on_commit=False) as db:
        job = _seed_manifest_authority(db)
        first = _company_bundle(db, descending=False)
        manifest, created = freeze_or_verify_feature_source_manifest(
            db, job=job, source_bodies=first
        )
        first_fingerprint = manifest.bundle_fingerprint
        db.commit()
        assert created is True

    with Session(engine) as db:
        company = db.scalar(select(CeriCompany))
        company.exchange = "CHANGED"
        db.commit()

    with Session(engine, expire_on_commit=False) as db:
        job = db.scalar(select(BackgroundJob).where(BackgroundJob.request_key == "manifest-job"))
        retained = db.scalar(select(CeriFeatureSourceManifest))
        changed = _company_bundle(
            db,
            descending=True,
            expected_manifest=retained.manifest_json,
        )
        assert changed.body_fingerprint != first_fingerprint
        with pytest.raises(ValueError, match="CERI_SOURCE_AUTHORITY_CHANGED_BEFORE_RETRY"):
            freeze_or_verify_feature_source_manifest(db, job=job, source_bodies=changed)
        db.rollback()
        assert db.scalar(select(func.count()).select_from(CeriDerivedFeature)) == 0

    engine.dispose()


def test_crash_retry_reuses_unchanged_manifest_and_order_is_deterministic(
    disposable_postgres_database: str,
) -> None:
    """Incident regression C/F: unchanged retry survives interruption and reordering."""

    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine, expire_on_commit=False) as db:
        job = _seed_manifest_authority(db, tickers=("AAA", "BBB"))
        first = _company_bundle(db, descending=False)
        manifest, created = freeze_or_verify_feature_source_manifest(
            db, job=job, source_bodies=first
        )
        manifest_id = manifest.id
        first_fingerprint = manifest.bundle_fingerprint
        db.commit()
        assert created is True

        # Simulate an interruption after authority publication and before any
        # calculation-owned output is written.
        try:
            raise MemoryError("forced retry interruption")
        except MemoryError:
            db.rollback()

    with Session(engine, expire_on_commit=False) as db:
        job = db.scalar(select(BackgroundJob).where(BackgroundJob.request_key == "manifest-job"))
        retained = db.scalar(select(CeriFeatureSourceManifest))
        retry = _company_bundle(
            db,
            descending=True,
            expected_manifest=retained.manifest_json,
        )
        manifest, created = freeze_or_verify_feature_source_manifest(
            db, job=job, source_bodies=retry
        )

        assert created is False
        assert manifest.id == manifest_id
        assert manifest.bundle_fingerprint == first_fingerprint == retry.body_fingerprint
        assert manifest.source_count == 2
        assert len(manifest.manifest_json["entries"]) == 2
        assert db.scalar(select(func.count()).select_from(CeriFeatureSourceManifest)) == 1
        assert db.scalar(select(func.count()).select_from(CeriDerivedFeature)) == 0
        assert db.scalar(select(func.count()).select_from(SetupSignalSnapshot)) == 0
        assert db.scalar(select(func.count()).select_from(WinnerPredictionSnapshot)) == 0

    engine.dispose()


def _company_bundle(
    db: Session,
    *,
    descending: bool,
    expected_manifest: dict | None = None,
) -> PrefetchedSourceBodies:
    order = CeriCompany.id.desc() if descending else CeriCompany.id.asc()
    bundle = PrefetchedSourceBodies(db, expected_manifest=expected_manifest)
    bundle.load(CeriCompany, select(CeriCompany).order_by(order))
    bundle.seal()
    return bundle


def _seed_manifest_authority(db: Session, *, tickers: tuple[str, ...] = ("AAA",)) -> BackgroundJob:
    run = UploadRun(filename="manifest.csv", row_count=len(tickers), status="COMPLETED")
    db.add(run)
    db.flush()
    pipeline = PipelineRun(upload_run_id=run.id, status="RUNNING")
    db.add(pipeline)
    db.flush()
    context = MarketCalculationContext(
        # Feature manifests use the post-acquisition CERI authority, which is
        # owned through PipelineRun.result_json rather than the run-start FK.
        pipeline_run_id=None,
        upload_run_id=run.id,
        cutoff_at=datetime(2026, 9, 25, 18, tzinfo=UTC),
        exchange_timezone="America/New_York",
        latest_completed_session=date(2026, 9, 25),
        calendar_version="test-calendar-v1",
        bar_readiness_version="test-bars-v1",
        cutoff_reason="TEST",
    )
    db.add(context)
    db.add_all(CeriCompany(ticker=ticker, exchange="US") for ticker in tickers)
    db.add(
        ExecutionConfigurationAnchor(
            anchor_id="test-anchor",
            fingerprint="test-fingerprint",
            payload_json={"test": True},
        )
    )
    db.flush()
    pipeline.result_json = {
        "ceri_calculation_context_id": context.id,
        "ceri_calculation_cutoff_at": context.cutoff_at.isoformat(),
        "ceri_calculation_as_of_session": context.latest_completed_session.isoformat(),
        "ceri_calculation_calendar_version": context.calendar_version,
        "ceri_calculation_bar_readiness_version": context.bar_readiness_version,
        "ceri_calculation_context_reason": context.cutoff_reason,
    }
    job = BackgroundJob(
        job_type="CERI_FEATURE_BATCH",
        status=JobStatus.RUNNING,
        request_key="manifest-job",
        workflow_key=f"ceri:pipeline:{pipeline.id}:test",
        related_run_id=run.id,
        payload_json={
            "run_id": run.id,
            "pipeline_run_id": pipeline.id,
            "calculation_context_id": context.id,
            "batch_index": 1,
            "cutoff_at": context.cutoff_at.isoformat(),
            "as_of_session": context.latest_completed_session.isoformat(),
            "calendar_version": context.calendar_version,
            "effective_configuration_anchor": {
                "anchor_id": "test-anchor",
                "fingerprint": "test-fingerprint",
            },
        },
    )
    db.add(job)
    db.commit()
    return job


def _seed_earnings(db: Session, tickers: tuple[str, ...]) -> list[CeriEarningsActual]:
    companies = [CeriCompany(ticker=ticker, exchange="US") for ticker in tickers]
    db.add_all(companies)
    db.flush()
    records = [
        CeriSourceRecord(
            provider="fixture",
            dataset="earnings",
            provider_record_id=f"{company.ticker}-earnings",
            content_hash=f"hash-{company.ticker}",
            idempotency_key=f"earnings-{company.ticker}",
            export_policy="exportable",
            redistribution_allowed=False,
            purge_eligible=False,
        )
        for company in companies
    ]
    db.add_all(records)
    db.flush()
    earnings = [
        CeriEarningsActual(
            source_record_id=record.id,
            company_id=company.id,
            metric="EPS_DILUTED",
            period_type="QUARTERLY",
            fiscal_period_end=date(2026, 6, 30),
            report_at=datetime(2026, 8, 1, 20, tzinfo=UTC),
            report_session=date(2026, 8, 1),
            actual_value=Decimal("1.20"),
            provider_consensus_value=Decimal("1.00"),
            provider_consensus_semantics="REPORT_TIME_CONSENSUS",
            event_kind="REPORTED",
        )
        for company, record in zip(companies, records, strict=True)
    ]
    db.add_all(earnings)
    db.commit()
    return earnings


def _upgrade(database_url: str) -> None:
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    run_guarded_alembic_upgrade(config, database_url, "head")
