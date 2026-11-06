"""Completed-company progress follows native work and retains atomic rollback."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from test_run40_lifecycle_remediation_postgresql import _capture, _counts, _migrate, _seed_universe
from test_run41_capture_handoff_postgresql import _artifact_pins

from app.models.tables import SetupLifecycleEvaluationEvidence, SetupLifecycleEvaluationRun
from app.services.setup_lifecycle.alert_service import SetupLifecycleAlertService
from app.services.setup_lifecycle.evaluation_service import (
    SetupLifecycleEvaluationCancelled,
    SetupLifecycleEvaluationService,
)


@pytest.mark.parametrize("interruption", ["failure", "cancel"])
def test_completed_company_is_uncommitted_and_interruption_rolls_back(
    disposable_postgres_database, monkeypatch, interruption
):
    _migrate(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    alerts_completed = []
    native_alerts = SetupLifecycleAlertService.evaluate_episode_result

    def observe_alerts(self, *args, **kwargs):
        result = native_alerts(self, *args, **kwargs)
        alerts_completed.append(result)
        return result

    monkeypatch.setattr(SetupLifecycleAlertService, "evaluate_episode_result", observe_alerts)
    try:
        with Session(engine) as db:
            cutoff, pipeline_id = _seed_universe(
                db,
                run_id=702,
                tickers=("R041", "R042"),
                cutoff_at=datetime(2026, 9, 18, 21, tzinfo=UTC),
            )
            capture = _capture(db, 702, cutoff)
            before = _counts(db)
            pins = _artifact_pins(db)
            evidence_before = db.scalar(
                select(func.count()).select_from(SetupLifecycleEvaluationEvidence)
            )
            db.commit()
            cancelled = False
            completed = []

            def progress(**checkpoint):
                nonlocal cancelled
                if checkpoint["phase"] != "LIFECYCLE_CANDIDATE_COMPLETED":
                    return
                completed.append(checkpoint)
                assert checkpoint["processed"] == len(completed) == 1
                assert checkpoint["total"] == 2
                assert checkpoint["current_item"] == checkpoint["last_completed_item"]
                assert len(alerts_completed) == 1
                assert db.in_nested_transaction()
                assert db.scalar(
                    select(func.count()).select_from(SetupLifecycleEvaluationEvidence)
                ) > evidence_before
                with Session(engine) as observer:
                    assert _counts(observer) == before
                    assert _artifact_pins(observer) == pins
                    assert observer.scalar(
                        select(func.count()).select_from(SetupLifecycleEvaluationEvidence)
                    ) == evidence_before
                if interruption == "failure":
                    raise RuntimeError("completed company interrupted")
                cancelled = True

            expected = (
                RuntimeError if interruption == "failure" else SetupLifecycleEvaluationCancelled
            )
            with pytest.raises(expected):
                SetupLifecycleEvaluationService().evaluate_run(
                    db,
                    702,
                    capture_result=capture,
                    market_cutoff=cutoff,
                    pipeline_run_id=pipeline_id,
                    progress_callback=progress,
                    should_cancel=lambda: cancelled,
                )
            assert len(completed) == len(alerts_completed) == 1
            assert _counts(db) == before
            assert _artifact_pins(db) == pins
            assert db.scalar(
                select(func.count()).select_from(SetupLifecycleEvaluationEvidence)
            ) == evidence_before
            failed = db.scalar(
                select(SetupLifecycleEvaluationRun)
                .where(SetupLifecycleEvaluationRun.source_run_id == 702)
                .order_by(SetupLifecycleEvaluationRun.id.desc())
            )
            assert failed.status == ("FAILED" if interruption == "failure" else "CANCELLED")
    finally:
        engine.dispose()
