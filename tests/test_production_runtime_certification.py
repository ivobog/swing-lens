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
        "gates": {
            "PRODUCTION_RUNTIME_CERTIFICATION": "PASS",
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
