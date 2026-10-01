from __future__ import annotations

from app.models.tables import PipelineRun
from app.services.pipeline_execution_authority import (
    pipeline_execution_is_authorized,
    revoke_pipeline_execution_authority,
)


def test_pipeline_execution_authority_is_explicit_and_fail_closed() -> None:
    historical = PipelineRun(
        id=9,
        upload_run_id=9,
        status="WAITING_FOR_CERI_COMPLETION",
        execution_authority_state=None,
    )
    active = PipelineRun(
        id=10,
        upload_run_id=10,
        status="RUNNING",
        execution_authority_state="ACTIVE",
    )
    terminal = PipelineRun(
        id=11,
        upload_run_id=11,
        status="FAILED",
        execution_authority_state="ACTIVE",
    )

    assert pipeline_execution_is_authorized(historical) is False
    assert pipeline_execution_is_authorized(active) is True
    assert pipeline_execution_is_authorized(terminal) is False


def test_revocation_is_durable_and_does_not_rewrite_pipeline_status() -> None:
    pipeline = PipelineRun(
        id=12,
        upload_run_id=12,
        status="WAITING_DEPENDENCY",
        execution_authority_state="ACTIVE",
    )

    revoke_pipeline_execution_authority(
        pipeline,
        state="QUARANTINED",
        reason="historical incident retained",
    )

    assert pipeline.status == "WAITING_DEPENDENCY"
    assert pipeline.execution_authority_state == "QUARANTINED"
    assert pipeline.execution_authority_reason == "historical incident retained"
    assert pipeline.execution_authority_changed_at is not None
    assert pipeline_execution_is_authorized(pipeline) is False
