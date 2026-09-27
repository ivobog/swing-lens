from __future__ import annotations

from pathlib import Path

from scripts.check_architecture_registry import (
    Finding,
    _check_modes,
    _compare_set,
    _validate_test_coverage,
    check_recovery_authority,
    detect_dangerous_reads_in_source,
)


def _codes(findings: list[Finding]) -> set[str]:
    return {finding.code for finding in findings}


def test_rejects_unclassified_mutating_route() -> None:
    findings: list[Finding] = []
    _compare_set(
        findings,
        "ARCH_NEW_MUTATING_ROUTE_UNCLASSIFIED",
        "mutating route",
        [{"method": "POST", "path": "/new", "function": "app.routers.new.create"}],
        [],
        ("method", "path", "function"),
    )
    assert "ARCH_NEW_MUTATING_ROUTE_UNCLASSIFIED" in _codes(findings)


def test_rejects_unregistered_job_type() -> None:
    findings: list[Finding] = []
    _compare_set(
        findings,
        "ARCH_NEW_JOB_TYPE_UNREGISTERED",
        "job",
        [{"job_type": "NEW_JOB"}],
        [],
        ("job_type",),
    )
    assert "ARCH_NEW_JOB_TYPE_UNREGISTERED" in _codes(findings)


def test_rejects_new_mutable_table() -> None:
    findings: list[Finding] = []
    _compare_set(
        findings,
        "ARCH_MUTABLE_TABLE_UNREGISTERED",
        "table",
        [{"table": "new_table", "model": "app.models.NewTable"}],
        [],
        ("table", "model"),
    )
    assert "ARCH_MUTABLE_TABLE_UNREGISTERED" in _codes(findings)


def test_rejects_recovery_caller_without_typed_authority() -> None:
    findings = check_recovery_authority(
        [
            {
                "path": "app/services/new_recovery.py",
                "function": "recover",
                "primitive": "recover_stale_jobs",
                "authority_keyword": False,
            }
        ]
    )
    assert _codes(findings) == {"ARCH_RECOVERY_CALLER_UNAUTHORIZED"}


def test_rejects_predicate_free_large_table_read() -> None:
    findings = detect_dangerous_reads_in_source(
        """
def load(session):
    return session.scalars(select(CeriSourceRecord)).all()
""",
        path="app/services/new_reader.py",
        high_risk_models={"CeriSourceRecord"},
    )
    assert findings == [
        {
            "path": "app/services/new_reader.py",
            "function": "load",
            "line": 3,
            "models": ["CeriSourceRecord"],
            "shape": "PREDICATE_FREE_MATERIALIZATION",
        }
    ]


def test_rejects_autonomous_trigger_without_mode_policy() -> None:
    findings: list[Finding] = []
    _check_modes(findings, "autonomous trigger AUTO-999", {"NORMAL": "ENABLED"})
    assert _codes(findings) == {"ARCH_RUNTIME_MODE_UNCLASSIFIED"}


def test_rejects_material_path_with_missing_test_reference(tmp_path: Path) -> None:
    findings: list[Finding] = []
    registry = {
        "recovery_paths": [{"id": "REC-999"}],
        "autonomous_triggers": [],
        "transaction_families": [],
        "state_transitions": [],
        "test_coverage": {
            "REC-999": ["tests/missing.py"],
            "GAP-009": ["tests/missing.py"],
            "GAP-012": ["tests/missing.py"],
            "ROUTES": ["tests/missing.py"],
            "JOBS": ["tests/missing.py"],
            "DANGEROUS-READS": ["tests/missing.py"],
        },
    }
    _validate_test_coverage(findings, registry, tmp_path)
    assert "ARCH_TEST_REFERENCE_MISSING" in _codes(findings)
