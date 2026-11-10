from dataclasses import replace

import pytest
from test_pipeline_executor import PipelineExecutorFakeDb, _dependencies

from app.services.pipeline_executor import PipelineContinuationSuppressed, execute_full_pipeline
from app.services.pipeline_prerequisites import PipelineBlockedError


def _interrupted_sector():
    db = PipelineExecutorFakeDb(tickers=["MSFT"])
    target = next(step for step in db.steps if step.step_name == "SECTOR_ROTATION_SNAPSHOT")
    for step in db.steps:
        if step.step_order < target.step_order:
            step.status = "COMPLETED"
    target.status = "INTERRUPTED"
    db.pipeline.status = "QUEUED"
    db.pipeline.current_step = target.step_name
    db.pipeline.result_json["recovery_continuation"] = {
        "boundary": target.step_name,
        "validation_state": "PENDING",
        "cause_code": "WORKER_MEMORY_CRITICAL",
    }
    return db, target


def test_invalid_sector_checkpoint_blocks_without_upstream_or_sector_recalculation():
    db, sector = _interrupted_sector()
    calls = []

    def invalid(*_args, **_kwargs):
        raise ValueError("SECTOR_RESUME_SOURCE_AUTHORITY_MISMATCH")

    with pytest.raises(PipelineBlockedError) as caught:
        execute_full_pipeline(
            db,
            pipeline_run_id=3,
            resume_from_step="SECTOR_ROTATION_SNAPSHOT",
            dependencies=replace(_dependencies(calls), validate_sector_resume_checkpoint=invalid),
        )
    assert caught.value.reason_code == "SECTOR_RESUME_CHECKPOINT_INVALID"
    assert db.pipeline.status == sector.status == "BLOCKED"
    assert db.pipeline.result_json["recovery_continuation"]["validation_state"] == "INVALID"
    assert calls == []
    assert all(step.retry_count == 0 for step in db.steps)


def test_certified_published_sector_is_reused_without_republication():
    db, sector = _interrupted_sector()
    calls = []
    dependencies = replace(
        _dependencies(calls),
        validate_sector_resume_checkpoint=lambda *_args, **_kwargs: {
            "validated": True,
            "restored_result": {},
            "published_sector_result": {
                "sector_rotation_snapshots": 1,
                "sector_rotation_sector_count": 11,
                "sector_rotation_snapshot_id": 67,
                "sector_rotation_evidence_id": 901,
            },
        },
    )
    execute_full_pipeline(
        db,
        pipeline_run_id=3,
        resume_from_step="SECTOR_ROTATION_SNAPSHOT",
        dependencies=dependencies,
    )
    assert calls == []
    assert sector.status == "COMPLETED"
    assert sector.retry_count == 1
    assert db.pipeline.result_json["sector_rotation_snapshot_id"] == 67
    assert db.pipeline.result_json["sector_rotation_evidence_id"] == 901
    assert db.pipeline.result_json["sector_rotation_snapshot_reused"] is True
    assert db.pipeline.result_json["recovery_continuation"]["validation_state"] == "VALIDATED"


@pytest.mark.parametrize("status", ["COMPLETED", "PARTIAL", "CANCELLED", "FAILED", "BLOCKED"])
def test_root_delivery_of_terminal_pipeline_cannot_reopen_it(status):
    db = PipelineExecutorFakeDb(tickers=["MSFT"])
    db.pipeline.status = status
    db.pipeline.result_json["retained_financial_result"] = "published"
    calls = []
    with pytest.raises(PipelineContinuationSuppressed):
        execute_full_pipeline(db, pipeline_run_id=3, dependencies=_dependencies(calls))
    assert calls == []
    assert db.pipeline.status == status
    assert db.pipeline.result_json["retained_financial_result"] == "published"
