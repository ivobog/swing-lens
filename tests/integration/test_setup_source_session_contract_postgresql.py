from __future__ import annotations

from datetime import UTC, datetime

import pytest
from native_mutation_support import seed_native_core
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_slse_performance_certification import _upgrade

from app.models.tables import PipelineRun, UploadRun
from app.services import pipeline_executor
from app.services.decision_effective_configuration import resolve_setup_configuration
from app.services.domain_write_fence import bounded_domain_session
from app.services.setup_lifecycle.config import load_setup_lifecycle_config
from app.services.setup_lifecycle.snapshot_builder import SetupLifecycleSnapshotBuilder
from app.services.setup_lifecycle.source_loader import SetupLifecycleSourceLoader

pytestmark = [pytest.mark.integration, pytest.mark.destructive]


def test_expired_pipeline_progress_does_not_open_setup_parent_transaction(
    disposable_postgres_database: str,
) -> None:
    """Stage progress must not begin Setup's authoritative transaction early."""

    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    progress: list[dict[str, object]] = []
    try:
        with Session(engine, expire_on_commit=True) as db:
            upload = UploadRun(
                filename="setup-parent-transaction.csv",
                status="COMPLETED",
                row_count=100,
            )
            pipeline = PipelineRun(
                upload_run=upload,
                status="CAPTURING_SETUP_SIGNALS",
                current_step="CAPTURING_SETUP_SIGNALS",
            )
            db.add(pipeline)
            db.commit()
            setup_upload_run_id = int(upload.id)
            pipeline_executor._bind_job_progress_callback(
                db,
                pipeline,
                lambda _db, **values: progress.append(values),
            )
            db.commit()
            assert not db.in_transaction()

            pipeline_executor._report_job_stage_progress(
                db,
                pipeline,
                "CAPTURING_SETUP_SIGNALS",
            )

            assert setup_upload_run_id > 0
            assert not db.in_transaction()
            assert progress == [
                {"stage": "CAPTURING_SETUP_SIGNALS", "current_item": None}
            ]
    finally:
        engine.dispose()


def test_setup_source_context_survives_bounded_read_session_close(
    disposable_postgres_database: str,
) -> None:
    """Every relationship consumed by the CPU build is part of the loaded contract."""

    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    native_config = load_setup_lifecycle_config()
    frozen_config = resolve_setup_configuration(native_config)

    try:
        with Session(engine) as db:
            cutoff, _, _ = seed_native_core(
                db,
                ticker="MSFT",
                cutoff_at=datetime(2026, 9, 16, 21, tzinfo=UTC),
                extra_configurations=(frozen_config,),
            )

            with bounded_domain_session(db) as read_db:
                context = SetupLifecycleSourceLoader().load_run_context(
                    read_db,
                    7,
                    market_cutoff=cutoff,
                )
                read_db.commit()

            source = context.tickers[0]
            assert source.raw_row.run.status == "COMPLETED"
            built = SetupLifecycleSnapshotBuilder(native_config).build(source)
            assert built.dto.ticker == "MSFT"
            assert built.dto.source_lineage["source_run_status"] == "COMPLETED"
            assert built.dto.source_lineage["source_run_successful"] is True
    finally:
        engine.dispose()
