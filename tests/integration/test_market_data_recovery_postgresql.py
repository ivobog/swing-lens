"""New gap-policy admissions preserve a blocked source's entire frozen population."""

from datetime import UTC, datetime

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.database_safety import run_guarded_alembic_upgrade
from app.models.tables import (
    MarketCalculationContext,
    PipelineRun,
    RawCompanyRow,
    UploadRun,
    WorkScopeRecord,
)
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.market_data_availability_policy import MarketDataAvailabilityMode
from app.services.pipeline_market_data_recovery import start_market_data_recovery
from app.services.pipeline_service import start_pipeline
from app.settings import Settings


def _body(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def test_recovery_isolated_full_population_idempotent_atomic_and_source_immutable(
    disposable_postgres_database, monkeypatch,
):
    run_guarded_alembic_upgrade(Config("alembic.ini"), disposable_postgres_database)
    settings = Settings(
        _env_file=None, database_url=disposable_postgres_database,
        ceri_enabled=False, ceri_run_capture_enabled=False, ceri_provider_ingest_enabled=False,
        setup_lifecycle_pipeline_step_enabled=False, winner_probability_capture_in_pipeline=False,
        market_data_prewarm_enabled=False,
    )
    monkeypatch.setattr("app.services.pipeline_service.get_settings", lambda: settings)
    engine = create_engine(disposable_postgres_database)
    try:
        with Session(engine, expire_on_commit=False) as db:
            upload = UploadRun(filename="full-universe.csv", status="COMPLETED", row_count=3)
            db.add(upload)
            db.flush()
            db.add_all([
                RawCompanyRow(
                    run_id=upload.id, row_number=index, ticker=ticker,
                    company_name=f"Company {ticker}", sector="Industrials",
                    raw_json={"Symbol": ticker, "Revenue": str(index * 1234)},
                ) for index, ticker in enumerate(("CLBK", "HLT", "MSFT"), start=1)
            ])
            db.flush()
            source = start_pipeline(
                db, upload.id, market_data_availability_policy=MarketDataAvailabilityMode.STRICT_V1,
                ceri_run_capture_enabled=False, ceri_provider_ingest_enabled=False,
                setup_lifecycle_pipeline_step_enabled=False,
            )
            source.status = "BLOCKED"
            source.current_step = "FETCHING_MARKET_DATA"
            source.error_message = "MARKET_DATA_SESSION_READINESS_BLOCKED: CLBK,HLT"
            source.completed_at = datetime.now(UTC)
            db.commit()
            source_id, upload_id = source.id, upload.id

            def source_snapshot():
                db.expire_all()
                return Canonical.fingerprint({
                    "pipeline": _body(db.get(PipelineRun, source_id)),
                    "upload": _body(db.get(UploadRun, upload_id)),
                    "rows": [_body(row) for row in db.scalars(select(RawCompanyRow).where(
                        RawCompanyRow.run_id == upload_id,
                    ).order_by(RawCompanyRow.id))],
                    "scope": _body(db.get(WorkScopeRecord, source.scope_id)),
                    "context": _body(db.scalar(select(MarketCalculationContext).where(
                        MarketCalculationContext.pipeline_run_id == source_id,
                    ))),
                })

            before = source_snapshot()
            with monkeypatch.context() as fault:
                def reject_new_admission(*_args, **_kwargs):
                    raise RuntimeError("Injected enqueue failure after copying raw rows")
                fault.setattr("app.services.pipeline_service.start_pipeline", reject_new_admission)
                with pytest.raises(RuntimeError, match="Injected enqueue failure"):
                    start_market_data_recovery(
                        db, source_pipeline_id=source_id, upload_run_id=upload_id,
                    )
                db.rollback()
            assert db.scalar(select(func.count(UploadRun.id))) == 1
            assert db.scalar(select(func.count(RawCompanyRow.id))) == 3
            assert source_snapshot() == before

            recovery = start_market_data_recovery(
                db, source_pipeline_id=source_id, upload_run_id=upload_id,
            )
            db.commit()
            assert recovery.id != source_id
            assert recovery.upload_run_id != upload_id
            assert recovery.scope_id != source.scope_id
            assert recovery.result_json["market_data_availability_policy"]["mode"] == (
                MarketDataAvailabilityMode.CONTINUE_KNOWN_DATA_GAPS_V1.value
            )
            copied = list(db.scalars(select(RawCompanyRow).where(
                RawCompanyRow.run_id == recovery.upload_run_id,
            ).order_by(RawCompanyRow.row_number)))
            assert [row.ticker for row in copied] == ["CLBK", "HLT", "MSFT"]
            lineage = recovery.result_json["market_data_recovery_source"]
            assert lineage["requested_tickers"] == ["CLBK", "HLT", "MSFT"]
            assert lineage["source_pipeline_id"] == source_id
            assert lineage["source_scope_id"] == source.scope_id
            assert len(lineage["source_raw_row_ids"]) == 3
            scope = db.get(WorkScopeRecord, recovery.scope_id)
            assert scope.payload_json["scope_definition"]["market_data_recovery_source"] == lineage
            assert source_snapshot() == before
            again = start_market_data_recovery(
                db, source_pipeline_id=source_id, upload_run_id=upload_id,
            )
            db.commit()
            assert again.id == recovery.id
            assert db.scalar(select(func.count(UploadRun.id))) == 2
            assert db.scalar(select(func.count(RawCompanyRow.id))) == 6
            assert source_snapshot() == before
    finally:
        engine.dispose()
