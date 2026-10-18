"""Native Run 41 ownership witness; writes only a guarded disposable database.

Run from repository root: .venv/Scripts/python.exe docs/forensics/run41-lifecycle-failure/reproduce_recapture.py
Uses existing synthetic financial fixtures, native capture/upsert/evaluation, no providers.
"""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for folder in (ROOT, ROOT / "tests", ROOT / "tests/integration"):
    sys.path.insert(0, str(folder))

from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from conftest import disposable_postgres_database_factory
from test_run40_lifecycle_remediation_postgresql import _capture, _migrate, _seed_universe

from app.database_safety import assert_disposable_database
from app.models.tables import SetupLifecycleEvaluationRun, SetupSignalSnapshot
from app.services.pipeline_executor import _invoke_setup_evaluation
from app.services.setup_lifecycle.evaluation_service import SetupLifecycleEvaluationService
from app.services.setup_lifecycle.snapshot_builder import SetupLifecycleSnapshotCaptureService


def main():
    result = {"production_database_written": False, "native_financial_validators_patched": False}
    database_factory = disposable_postgres_database_factory.__wrapped__()
    with database_factory() as url:
        assert_disposable_database(url)
        result["database"] = make_url(url).database
        _migrate(url)
        engine = create_engine(url)
        try:
            with Session(engine) as db:
                cutoff, pipeline_id = _seed_universe(
                    db,
                    run_id=702,
                    tickers=("R041",),
                    cutoff_at=datetime(2026, 9, 18, 21, tzinfo=UTC),
                )
                original = _capture(db, 702, cutoff)
                service = SetupLifecycleEvaluationService()
                service._validate_capture_handoff(
                    db, run_id=702, capture_result=original,
                    snapshot_ids=original.snapshot_ids, market_cutoff=cutoff,
                )
                result["original_capture"] = {
                    "evaluation_run_id": original.evaluation_run_id,
                    "snapshot_ids": original.snapshot_ids,
                    "validation": "PASS",
                }

                class ObservedCapture(SetupLifecycleSnapshotCaptureService):
                    def capture_snapshots_for_run(self, *args, **kwargs):
                        captured = super().capture_snapshots_for_run(*args, **kwargs)
                        result["recapture"] = {
                            "evaluation_run_id": captured.evaluation_run_id,
                            "snapshot_ids": captured.snapshot_ids,
                            "status": captured.status,
                        }
                        return captured

                service.capture_service = ObservedCapture()
                try:
                    _invoke_setup_evaluation(
                        service.evaluate_run, db, 702, capture_result=None,
                        market_cutoff=cutoff, pipeline_run_id=pipeline_id,
                    )
                except ValueError as exc:
                    result["handoff_disabled"] = {"type": type(exc).__name__, "error": str(exc)}
                    assert str(exc) == "Setup capture handoff snapshot ownership is inconsistent."
                else:
                    raise AssertionError("Native default-off path did not reproduce the failure")
                db.rollback()
                owners = dict(db.execute(select(
                    SetupSignalSnapshot.id, SetupSignalSnapshot.evaluation_run_id
                )).all())
                result["persisted_snapshot_owners_after_failure"] = owners
                assert tuple(result["recapture"]["snapshot_ids"]) == original.snapshot_ids
                assert result["recapture"]["evaluation_run_id"] != original.evaluation_run_id
                assert all(value == original.evaluation_run_id for value in owners.values())
                result["failed_audits"] = [
                    {"id": row.id, "status": row.status, "error": row.error_summary_json}
                    for row in db.scalars(select(SetupLifecycleEvaluationRun).where(
                        SetupLifecycleEvaluationRun.status == "FAILED"
                    ))
                ]
                service._validate_capture_handoff(
                    db, run_id=702, capture_result=original,
                    snapshot_ids=original.snapshot_ids, market_cutoff=cutoff,
                )
                result["original_handoff_after_failure"] = "PASS"
                # Isolate the failing predicate using the same native validator.
                try:
                    service._validate_capture_handoff(
                        db, run_id=702,
                        capture_result=replace(original, evaluation_run_id=result["recapture"]["evaluation_run_id"]),
                        snapshot_ids=original.snapshot_ids, market_cutoff=cutoff,
                    )
                except ValueError as exc:
                    result["only_attempt_owner_changed"] = str(exc)
                else:
                    raise AssertionError("Expected owner-only mutation to fail")
                db.rollback()
                # Positive control: supply the committed stage handoff through
                # the same pipeline invocation adapter, with native downstream writers.
                published = _invoke_setup_evaluation(
                    SetupLifecycleEvaluationService().evaluate_run,
                    db, 702, capture_result=original,
                    market_cutoff=cutoff, pipeline_run_id=pipeline_id,
                )
                db.commit()
                result["handoff_enabled"] = published.as_dict()
                assert published.status == "COMPLETED" and published.failed == 0
                assert dict(db.execute(select(
                    SetupSignalSnapshot.id, SetupSignalSnapshot.evaluation_run_id
                )).all()) == owners
                result["creator_provenance_preserved"] = True
        finally:
            engine.dispose()
    result["disposable_database_dropped"] = True
    destination = Path(__file__).with_name("reproduction-results.json")
    destination.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
