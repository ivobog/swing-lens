import pytest

from app.models.tables import BackgroundJob
from app.services.background_job_service import JobStatus
from app.services.background_worker import execute_job


def test_execute_job_dispatches_to_registered_handler() -> None:
    job = BackgroundJob(id=1, job_type="TEST_JOB", status=JobStatus.RUNNING)
    calls = {}

    def handler(db, handled_job):
        calls["job"] = handled_job
        return {"handled": True}

    result = execute_job(
        db=object(),
        job=job,
        handlers={"TEST_JOB": handler},
    )

    assert result == {"handled": True}
    assert calls["job"] is job


def test_execute_job_rejects_unsupported_job_type() -> None:
    job = BackgroundJob(id=1, job_type="UNKNOWN", status=JobStatus.RUNNING)

    with pytest.raises(ValueError, match="Unsupported job type: UNKNOWN"):
        execute_job(db=object(), job=job, handlers={})
