from __future__ import annotations

import pytest

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


def test_activation_boundaries_are_paired_and_reject_historical_active_roots(monkeypatch):
    from app.settings import Settings

    with pytest.raises(ValueError, match="configured together"):
        Settings(_env_file=None, job_activation_min_id=100)
    settings = Settings(
        _env_file=None, job_activation_min_id=100, pipeline_activation_min_id=10,
    )
    monkeypatch.setattr("app.services.pipeline_execution_authority.get_settings", lambda: settings)
    historical = PipelineRun(id=9, status="RUNNING", execution_authority_state="ACTIVE")
    current = PipelineRun(id=10, status="RUNNING", execution_authority_state="ACTIVE")
    assert pipeline_execution_is_authorized(historical) is False
    assert pipeline_execution_is_authorized(current) is True
