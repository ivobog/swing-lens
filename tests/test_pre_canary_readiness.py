from __future__ import annotations

from scripts.check_pre_canary_readiness import (
    REQUIRED_CHECKS,
    ReadinessCheck,
    finalize_report,
)


def test_pre_canary_readiness_clean_example_passes() -> None:
    checks = {
        name: ReadinessCheck(True, f"{name} passed") for name in REQUIRED_CHECKS
    }

    report = finalize_report(checks)

    assert report["ready"] is True
    assert report["verdict"] == "PASS"
    assert all(check["passed"] for check in report["checks"].values())


def test_pre_canary_readiness_blocked_example_fails_closed() -> None:
    checks = {
        name: ReadinessCheck(True, f"{name} passed") for name in REQUIRED_CHECKS
    }
    checks["queue_isolation"] = ReadinessCheck(
        False,
        {"unrelated_runnable_jobs": 1},
    )

    report = finalize_report(checks)

    assert report["ready"] is False
    assert report["verdict"] == "FAIL"
    assert report["checks"]["queue_isolation"] == {
        "passed": False,
        "detail": {"unrelated_runnable_jobs": 1},
    }


def test_pre_canary_readiness_missing_check_fails_closed() -> None:
    report = finalize_report({})

    assert report["ready"] is False
    assert report["verdict"] == "FAIL"
    assert report["checks"]["expected_head"] == {
        "passed": False,
        "detail": "check was not executed",
    }
