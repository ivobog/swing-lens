import pytest

from scripts.qa.verify_pipeline_scheduler_qualification import verify


def _receipt():
    return {
        "status": "PASS",
        "pytest_exit_code": 0,
        "tests": 10,
        "failed_errored_or_skipped": 0,
        "source_unchanged_during_checks": True,
        "git_commit": "candidate",
        "source_sha256": {"planner": "fixed"},
    }


def test_release_accepts_matching_qualified_scheduler():
    verify(_receipt(), revision="candidate", hashes={"planner": "fixed"})


@pytest.mark.parametrize(
    "revision,hashes",
    [
        ("older-baseline", {"planner": "fixed"}),
        ("candidate", {"planner": "pre-fix"}),
        ("candidate", {}),
    ],
)
def test_release_rejects_omitted_fix_or_different_baseline(revision, hashes):
    with pytest.raises(RuntimeError, match="QUALIFICATION_STALE"):
        verify(_receipt(), revision=revision, hashes=hashes)


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "FAIL"),
        ("pytest_exit_code", 1),
        ("tests", 0),
        ("failed_errored_or_skipped", 1),
        ("source_unchanged_during_checks", False),
    ],
)
def test_release_rejects_failed_skipped_or_changing_checks(field, value):
    receipt = _receipt()
    receipt[field] = value
    with pytest.raises(RuntimeError, match="QUALIFICATION_REQUIRED"):
        verify(receipt, revision="candidate", hashes={"planner": "fixed"})
