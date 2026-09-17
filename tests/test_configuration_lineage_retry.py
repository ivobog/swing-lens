import pytest

from app.services.background_job_service import classify_job_failure


@pytest.mark.parametrize(
    "code",
    [
        "MISSING_CONFIGURATION_ANCHOR_BINDING",
        "CONFIGURATION_ANCHOR_BINDING_MISMATCH",
        "CONFIGURATION_ANCHOR_PARENT_MISMATCH",
        "CONFIGURATION_EXECUTION_LINEAGE_MISMATCH",
        "MISSING_CONFIGURATION_ANCHOR",
        "MISSING_CONFIGURATION_ANCHOR_PARENT",
        "MISSING_CONFIGURATION_ANCHOR_FOR_RESUME",
        "MISSING_OR_MISMATCHED_CONFIGURATION_ANCHOR",
        "CONFIGURATION_ANCHOR_INTEGRITY_MISMATCH",
        "CONFIGURATION_ANCHOR_RECORD_MISMATCH",
        "MISSING_FROZEN_CONFIGURATION_RECORD",
        "MISSING_FROZEN_CONFIGURATION",
        "SEC_REPAIR_PROCESSOR_SIGNATURE_MISMATCH",
    ],
)
def test_configuration_lineage_errors_are_deterministic(code):
    assert classify_job_failure(ValueError(code + ": diagnostic context")) == {
        "kind": "DETERMINISTIC",
        "retryable": False,
        "code": code,
    }


@pytest.mark.parametrize(
    "error", [TimeoutError("provider timeout"), ConnectionError("temporary network")]
)
def test_transient_provider_failures_keep_retries(error):
    result = classify_job_failure(error)
    assert result["retryable"] is True
    assert result["kind"] == "TRANSIENT"


def test_unrelated_value_error_does_not_disable_retries():
    assert classify_job_failure(ValueError("unrelated error"))["retryable"] is True
