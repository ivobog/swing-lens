from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.check_pre_canary_readiness import (
    GIB,
    REQUIRED_CHECKS,
    HostCommitSnapshot,
    ReadinessCheck,
    _disk_space_check,
    _host_commit_check,
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


def _commit_snapshot(percent: float, *, limit_gib: int = 100) -> HostCommitSnapshot:
    limit = limit_gib * GIB
    return HostCommitSnapshot(
        commit_limit_bytes=limit,
        commit_charge_bytes=int(limit * percent / 100),
        total_physical_bytes=64 * GIB,
        available_physical_bytes=32 * GIB,
    )


def test_host_commit_admission_passes_with_ample_capacity() -> None:
    check = _host_commit_check(snapshot_provider=lambda: _commit_snapshot(50))

    assert check.passed is True
    assert check.detail["verdict"] == "PASS"
    assert check.detail["headroom_bytes"] == 50 * GIB


def test_host_commit_admission_warns_and_reports_top_consumers() -> None:
    consumers = [{"pid": 10, "name": "large.exe", "private_bytes": 12 * GIB}]
    check = _host_commit_check(
        snapshot_provider=lambda: _commit_snapshot(86),
        consumers_provider=lambda: consumers,
    )

    assert check.passed is True
    assert check.detail["verdict"] == "WARN"
    assert check.detail["top_private_memory_consumers"] == consumers


def test_host_commit_admission_fails_at_critical_utilization() -> None:
    check = _host_commit_check(
        snapshot_provider=lambda: _commit_snapshot(91),
        consumers_provider=lambda: [],
    )

    assert check.passed is False
    assert check.detail["verdict"] == "FAIL"


def test_host_commit_admission_fails_below_four_gib_headroom() -> None:
    snapshot = HostCommitSnapshot(
        commit_limit_bytes=20 * GIB,
        commit_charge_bytes=17 * GIB,
        total_physical_bytes=128 * GIB,
        available_physical_bytes=64 * GIB,
    )

    check = _host_commit_check(snapshot_provider=lambda: snapshot, consumers_provider=lambda: [])

    assert check.passed is False
    assert check.detail["headroom_bytes"] == 3 * GIB
    assert check.detail["utilization_percent"] == 85.0


def test_host_commit_admission_fails_closed_when_windows_metric_is_unavailable() -> None:
    def unavailable() -> HostCommitSnapshot:
        raise OSError("GlobalMemoryStatusEx unavailable")

    check = _host_commit_check(snapshot_provider=unavailable)

    assert check.passed is False
    assert check.detail["verdict"] == "FAIL"
    assert "GlobalMemoryStatusEx unavailable" in check.detail["error"]


@pytest.mark.parametrize(
    ("commit_percent", "disk_free_percent", "memory_passed", "disk_passed"),
    [(91, 60, False, True), (50, 4, True, False)],
)
def test_memory_and_disk_admission_are_independent(
    tmp_path: Path,
    commit_percent: float,
    disk_free_percent: float,
    memory_passed: bool,
    disk_passed: bool,
) -> None:
    settings = SimpleNamespace(
        upload_dir=tmp_path,
        export_dir=tmp_path,
        cache_dir=tmp_path,
        db_monitor_log_dir=tmp_path,
        observability_disk_critical_percent=5,
        observability_disk_warning_percent=10,
    )
    total = 100 * GIB
    disk = _disk_space_check(
        settings,
        paths=(tmp_path,),
        usage_provider=lambda _path: SimpleNamespace(
            total=total,
            used=int(total * (100 - disk_free_percent) / 100),
            free=int(total * disk_free_percent / 100),
        ),
    )
    memory = _host_commit_check(
        snapshot_provider=lambda: _commit_snapshot(commit_percent),
        consumers_provider=lambda: [],
    )

    assert memory.passed is memory_passed
    assert disk.passed is disk_passed
