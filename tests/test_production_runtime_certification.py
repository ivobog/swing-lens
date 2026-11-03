from __future__ import annotations

import json

from scripts import production_runtime_certification as certification


def test_sec_user_agent_rejects_placeholder_and_requires_contact_email() -> None:
    assert certification._validate_sec_user_agent("SwingLens/0.1.0 operator@example.invalid") == (
        False,
        "SEC_USER_AGENT still contains a placeholder contact identity.",
    )
    assert certification._validate_sec_user_agent("SwingLens operations") == (
        False,
        "SEC_USER_AGENT must contain a real contact email address.",
    )
    assert certification._validate_sec_user_agent("SwingLens/1.0 operations@real-company.ch") == (
        True,
        None,
    )


def test_matching_deterministic_report_requires_same_fingerprint_and_passed_gates(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(certification, "REPORT_ROOT", tmp_path)
    stale = tmp_path / "20261001T010000Z"
    stale.mkdir()
    (stale / "report.json").write_text(
        json.dumps(
            {
                "provider_mode": "deterministic",
                "working_tree_fingerprint": "old",
                "gates": {
                    "PRODUCTION_RUNTIME_CERTIFICATION": "PASS",
                    "SMALL_LIVE_PROVIDER_CANARY": "NOT_RUN",
                },
            }
        ),
        encoding="utf-8",
    )
    current = tmp_path / "20261001T020000Z"
    current.mkdir()
    expected = {
        "execution_id": "candidate",
        "provider_mode": "deterministic",
        "working_tree_fingerprint": "candidate-fingerprint",
        "verdict": "CERTIFIED",
        "gates": {
            "PRODUCTION_RUNTIME_CERTIFICATION": "PASS",
            "EXACT_PIPELINE_CERTIFICATION": "PASS",
            "FULL_RELEASE_REGRESSIONS": "PASS",
            "SMALL_LIVE_PROVIDER_CANARY": "NOT_RUN",
        },
    }
    (current / "report.json").write_text(json.dumps(expected), encoding="utf-8")
    assert certification._matching_deterministic_report("old-but-not-requested") is None
    assert certification._matching_deterministic_report("candidate-fingerprint") == expected


def test_live_certification_fails_closed_before_runtime_when_sec_identity_is_invalid(
    tmp_path, monkeypatch
) -> None:
    report_dir = tmp_path / "blocked"
    monkeypatch.setattr(
        certification,
        "_live_provider_preflight",
        lambda: {
            "sec": {"ready": False, "reason": "SEC identity is invalid."},
            "eodhd": {"ready": True, "reason": None},
            "ib": {"ready": True, "reason": None},
        },
    )
    monkeypatch.setattr(certification, "_matching_deterministic_report", lambda _: {})
    monkeypatch.setattr(certification, "_working_tree_identity", lambda: ("fp", True))
    monkeypatch.setattr(certification, "_git", lambda *args: "git-value")
    monkeypatch.setattr(certification, "_configuration_fingerprint", lambda: "config-fp")
    monkeypatch.setattr(certification, "_sanitized_database_identity", lambda: "isolated")
    monkeypatch.setattr(
        certification,
        "_run_test_group",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("live runtime must not start while preflight is blocked")
        ),
    )

    result = certification._run_live_certification(report_dir, "execution")

    assert result == 2
    report = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
    assert report["verdict"] == "BLOCKED"
    assert report["gates"]["SMALL_LIVE_PROVIDER_CANARY"] == "BLOCKED"
    assert report["safe_for_normal_run"] is False


def _exact_invocation():
    return {
        "selected": list(certification.EXACT_TEST_IDS),
        "collected": list(certification.EXACT_TEST_IDS),
        "executed": list(certification.EXACT_TEST_IDS),
        "failed": [],
        "exit_code": 0,
    }


def test_exact_selector_includes_both_collected_handoff_modes_and_no_larger_case():
    runtime = dict(certification.TEST_GROUPS)["production-runtime"]
    assert runtime == certification.EXACT_TEST_IDS
    assert all(node.endswith("-10]") for node in runtime)
    assert "chromium-False-10" in runtime[0] and "chromium-True-10" in runtime[1]


def test_wrapper_rejects_missing_duplicate_stale_zero_and_failed_scenario_results():
    import copy

    invocation = _exact_invocation()
    certificates = [
        {"test_id": node, "verdict": "CERTIFIED"} for node in certification.EXACT_TEST_IDS
    ]
    assert certification.invocation_verdict(invocation, certificates, []) == "CERTIFIED"
    for reports in (
        [],
        certificates[:1],
        certificates + certificates[:1],
        [{**certificates[0], "test_id": "stale"}, certificates[1]],
    ):
        assert certification.invocation_verdict(invocation, reports, []) == "FAILED"
    for key, value in (
        ("selected", []),
        ("collected", []),
        ("executed", []),
        ("exit_code", 1),
        ("failed", ["pytest failure"]),
    ):
        invalid = {**copy.deepcopy(invocation), key: value}
        assert certification.invocation_verdict(invalid, certificates, []) == "FAILED"
    assert certification.verdict_exit_code("CERTIFIED") == 0
    assert all(
        certification.verdict_exit_code(state) != 0 for state in ("FAILED", "BLOCKED", "INCOMPLETE")
    )


def test_empty_runtime_diagnostics_cannot_pass():
    assert not certification._runtime_invariants_pass([])


def test_junit_execution_accounts_for_skips_and_actual_node_ids(tmp_path):
    path = tmp_path / "result.xml"
    path.write_text(
        '<testsuites><testsuite><testcase classname="tests.e2e.single_run_certification.'
        'test_real_writer_cardinality" name="test_frozen_real_writer_cardinality'
        '[chromium-False-10]"/><testcase classname="tests.e2e.single_run_certification.'
        'test_real_writer_cardinality" name="test_frozen_real_writer_cardinality'
        '[chromium-True-10]"><skipped/></testcase></testsuite></testsuites>'
    )
    execution = certification._junit_execution(path)
    assert execution["executed"] == list(certification.EXACT_TEST_IDS)
    assert execution["failed"] == [certification.EXACT_TEST_IDS[1]]


def test_extra_prerequisite_browser_preparation_is_confined_to_ci(tmp_path, monkeypatch):
    from types import SimpleNamespace

    calls = []

    def prepare(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="prepared", stderr="")

    monkeypatch.setattr(certification.subprocess, "run", prepare)
    certification._prepare_prerequisite_browsers(tmp_path, {}, ["firefox"])
    assert calls == []
    certification._prepare_prerequisite_browsers(
        tmp_path, {"GITHUB_ACTIONS": "true"}, ["chromium", "firefox"],
    )
    assert calls[0][1:] == ["-m", "playwright", "install", "--with-deps", "chromium", "firefox"]


def test_failed_or_unreviewed_prerequisite_browser_preparation_blocks(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import pytest

    from scripts.qa.release_evidence import EvidenceError

    monkeypatch.setattr(
        certification.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="failed"),
    )
    for browsers in (["firefox"], ["unreviewed"]):
        with pytest.raises(EvidenceError):
            certification._prepare_prerequisite_browsers(
                tmp_path, {"GITHUB_ACTIONS": "true"}, browsers,
            )
