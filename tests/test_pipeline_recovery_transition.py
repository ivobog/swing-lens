import pytest

from app.models.tables import PipelineRun
from app.services.pipeline_state_machine import PipelineTransitionError, transition_pipeline


@pytest.mark.parametrize("status", ["FAILED", "BLOCKED", "PARTIAL", "COMPLETED", "CANCELLED"])
def test_recovery_cannot_reopen_a_terminal_parent(status):
    parent = PipelineRun(id=66, upload_run_id=62, status=status)
    with pytest.raises(PipelineTransitionError, match="PIPELINE_TRANSITION_FORBIDDEN"):
        transition_pipeline(object(), parent, "QUEUED", actor="recovery")
    assert parent.status == status
