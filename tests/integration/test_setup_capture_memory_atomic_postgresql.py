"""Fresh publication failure/cancellation must roll back before native recovery."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from test_run40_lifecycle_remediation_postgresql import _migrate, _seed_universe

from app.models.tables import CoreCalculationEvidence, SetupSignalSnapshot
from app.services.setup_lifecycle.snapshot_builder import (
    SetupLifecycleCaptureCancelled,
    SetupLifecycleSnapshotCaptureService,
)


@pytest.mark.parametrize("interruption", ["failure", "cancel"])
def test_fresh_publication_is_atomic_and_recovery_preserves_outputs(
    disposable_postgres_database, interruption
):
    _migrate(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        cutoff, _ = _seed_universe(
            db,
            run_id=704,
            tickers=("M431", "M432"),
            cutoff_at=datetime(2026, 9, 18, 21, tzinfo=UTC),
        )
        db.commit()
        cancelled = False
        checkpoints = []

        def count_setup(session):
            return (
                session.scalar(select(func.count()).select_from(SetupSignalSnapshot)),
                session.scalar(
                    select(func.count())
                    .select_from(CoreCalculationEvidence)
                    .where(CoreCalculationEvidence.artifact_kind == "SETUP")
                ),
            )

        def checkpoint(_ticker, processed, _total, *, phase):
            nonlocal cancelled
            if phase == "EVIDENCE" and processed == 1:
                # The writer has flushed both serving rows and sealed its first
                # output, but another transaction must still see no publication.
                assert db.scalar(select(func.count()).select_from(SetupSignalSnapshot)) == 2
                with Session(engine) as observer:
                    assert count_setup(observer) == (0, 0)
                checkpoints.append(phase)
                if interruption == "failure":
                    raise RuntimeError("interrupt fresh Setup publication")
                cancelled = True

        expected = SetupLifecycleCaptureCancelled if interruption == "cancel" else RuntimeError
        with pytest.raises(expected):
            SetupLifecycleSnapshotCaptureService().capture_snapshots_for_run(
                db,
                704,
                market_cutoff=cutoff,
                progress_callback=checkpoint,
                should_cancel=lambda: cancelled,
            )
        db.rollback()
        assert checkpoints == ["EVIDENCE"]
        assert count_setup(db) == (0, 0)
        first = SetupLifecycleSnapshotCaptureService().capture_snapshots_for_run(
            db,
            704,
            market_cutoff=cutoff,
        )
        db.commit()
        pins = list(
            db.execute(
                select(
                    SetupSignalSnapshot.id,
                    SetupSignalSnapshot.evaluation_run_id,
                    CoreCalculationEvidence.payload_fingerprint,
                )
                .join(
                    CoreCalculationEvidence,
                    CoreCalculationEvidence.id == SetupSignalSnapshot.evidence_id,
                )
                .order_by(SetupSignalSnapshot.id)
            )
        )
        replay = SetupLifecycleSnapshotCaptureService().capture_snapshots_for_run(
            db,
            704,
            market_cutoff=cutoff,
        )
        db.commit()
        assert first.failed == replay.failed == 0
        assert first.captured == replay.captured == 2
        assert first.snapshot_ids == replay.snapshot_ids
        assert count_setup(db) == (2, 2)
        assert pins == list(
            db.execute(
                select(
                    SetupSignalSnapshot.id,
                    SetupSignalSnapshot.evaluation_run_id,
                    CoreCalculationEvidence.payload_fingerprint,
                )
                .join(
                    CoreCalculationEvidence,
                    CoreCalculationEvidence.id == SetupSignalSnapshot.evidence_id,
                )
                .order_by(SetupSignalSnapshot.id)
            )
        )
    engine.dispose()
