"""A18 controls use test-side metadata violations, not production mutations."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from datetime import UTC, datetime

import pytest

from scripts.qa import check_certification_registry as registry


@pytest.fixture
def manifest():
    return copy.deepcopy(registry.load_manifest())


@pytest.fixture
def execution(tmp_path, manifest):
    contract = manifest["source_contracts"]["contracts"][1]
    test = contract["tests"][0]
    receipt = tmp_path / "test-side-execution.json"
    source = contract["sources"][0]
    qualified = source["path"].removesuffix(".py").replace("/", ".") + "." + source["symbol"]
    receipt.write_text(
        json.dumps(
            {
                "kind": "controlled specimen",
                "source": qualified,
                "assertion": True,
                "fault": "EXPECTED_REJECTION",
            }
        )
    )
    profile = hashlib.sha256(b"test-side environment/selection").hexdigest()
    current = {
        "fingerprint": "a" * 64,
        "revision": "reviewed-revision",
        "environment_sha256": profile,
        "selection_sha256": profile,
    }
    observation = {
        "level": "FAULT_SENSITIVE",
        "kind": "EXECUTION_OBSERVATION",
        "test_id": test["id"],
        "source_fingerprint": current["fingerprint"],
        "source_revision": current["revision"],
        "started_at": datetime.now(UTC).isoformat(),
        "test_sha256": test["reviewed_test_sha256"],
        "environment_sha256": profile,
        "selection_sha256": profile,
        "fidelity": test["fidelity"],
        "artifacts": [
            {"path": receipt.name, "sha256": hashlib.sha256(receipt.read_bytes()).hexdigest()}
        ],
        "reached_sources": [contract["sources"][0]],
        "reached_assertions": ["invalid response persisted nothing"],
        "fault": {
            "outcome": "EXPECTED_REJECTION",
            "reached_assertion": "invalid response persisted nothing",
        },
        "claim_current": True,
        "execution_status": "EXPECTED_REJECTION",
        "source_witness": {"artifact": receipt.name, "pointer": "/source", "expected": qualified},
        "assertion_witness": {"artifact": receipt.name, "pointer": "/assertion", "expected": True},
        "fault_witness": {
            "artifact": receipt.name,
            "pointer": "/fault",
            "expected": "EXPECTED_REJECTION",
        },
    }
    return contract, observation, current, tmp_path


def test_bounded_mapping_is_static_intent_with_all_ten_domains(manifest):
    result = registry.validate_source_contracts(manifest)
    assert result == {"contracts": 10, "claim": "STATIC_INTENT", "execution": "NOT_VERIFIED"}
    assert len({c["id"] for c in manifest["source_contracts"]["contracts"]}) == 10


@pytest.mark.parametrize(
    "fault,expected",
    [
        ("missing-test", "test ID no longer exists"),
        ("wrong-source", "wrong contract/symbol"),
        ("static-measured", "static metadata cannot establish"),
        ("no-owner", "lacks owner"),
        ("archived-test", "historical/operational"),
        ("review-drift", "reviewed source/contract association changed"),
        ("unit-native", "lacks reviewed native fixture"),
    ],
)
def test_invalid_static_mapping_fails_closed(manifest, fault, expected):
    contract = manifest["source_contracts"]["contracts"][0]
    if fault == "missing-test":
        contract["tests"][0]["id"] = "tests/test_core_readiness_helpers.py::test_removed_forever"
    elif fault == "wrong-source":
        contract["sources"][0]["path"] = "app/services/ib_fetch_executor.py"
    elif fault == "static-measured":
        contract["coverage_claim"] = "MEASURED_EXECUTION"
    elif fault == "no-owner":
        contract["owner"] = ""
    elif fault == "archived-test":
        contract["tests"][0]["id"] = "scripts/forensics/run159_ceri_quarantine.py::main"
    elif fault == "unit-native":
        contract["tests"][0].update(tier="NATIVE", fidelity="POSTGRESQL_SERVICE")
    else:
        contract["behavior"] = "unreviewed different financial contract"
    if fault != "review-drift":
        contract["review_sha256"] = registry._contract_digest(
            {k: v for k, v in contract.items() if k != "review_sha256"}
        )
    with pytest.raises(registry.RegistryError, match=expected):
        registry.validate_source_contracts(manifest)


def test_execution_assertion_and_fault_levels_require_explicit_observations(execution):
    contract, observation, current, root = execution
    for level in ("MEASURED_EXECUTION", "ASSERTED_CONTRACT", "FAULT_SENSITIVE"):
        observation["level"] = level
        result = registry.assess_contract_observation(contract, observation, current, root)
        assert result["level"] == level and result["freshness"] == "CURRENT_IDENTITIES_MATCH"
        assert result["release_verdict"] == "NOT_EVALUATED"


@pytest.mark.parametrize(
    "fault,expected",
    [
        ("static-only", "supported only by static metadata"),
        ("old-pass", "old PASS"),
        ("unreached-negative", "no reached rejecting assertion"),
        ("no-assertion", "no reached assertion"),
        ("unit-native-promotion", "promoted beyond"),
        ("unit-live-promotion", "promoted beyond"),
        ("no-timestamp", "supported only by static metadata"),
        ("test-changed", "old PASS"),
        ("environment-changed", "old PASS"),
        ("selection-changed", "old PASS"),
        ("artifact-changed", "artifact changed"),
        ("unreached-source", "did not reach"),
        ("static-witness", "missing execution witness"),
        ("wrong-source-witness", "execution witness does not match"),
        ("missing-fingerprint", "missing execution identity"),
        ("invalid-timestamp", "timestamp missing or invalid"),
        ("unknown-status", "status is not verified"),
    ],
)
def test_unsupported_execution_claims_are_rejected(execution, fault, expected):
    contract, observation, current, root = execution
    if fault == "static-only":
        observation["kind"] = "STATIC_INTENT"
    elif fault == "old-pass":
        observation["source_fingerprint"] = "b" * 64
    elif fault == "unreached-negative":
        observation["fault"]["reached_assertion"] = "never reached"
    elif fault == "no-assertion":
        observation["reached_assertions"] = []
    elif fault == "unit-native-promotion":
        observation["fidelity"] = "POSTGRESQL_SERVICE"
    elif fault == "unit-live-promotion":
        observation["fidelity"] = "LIVE_PROVIDER"
    elif fault == "no-timestamp":
        del observation["started_at"]
    elif fault == "test-changed":
        observation["test_sha256"] = "b" * 64
    elif fault == "environment-changed":
        observation["environment_sha256"] = "b" * 64
    elif fault == "selection-changed":
        observation["selection_sha256"] = "b" * 64
    elif fault == "artifact-changed":
        (root / observation["artifacts"][0]["path"]).write_text("changed")
    elif fault == "unreached-source":
        observation["reached_sources"] = []
    elif fault == "static-witness":
        del observation["source_witness"]
    elif fault == "missing-fingerprint":
        observation["source_fingerprint"] = None
    elif fault == "invalid-timestamp":
        observation["started_at"] = "unknown"
    elif fault == "unknown-status":
        observation["execution_status"] = "UNKNOWN"
    else:
        observation["source_witness"]["expected"] = "only static intent"
    with pytest.raises(registry.RegistryError, match=expected):
        registry.assess_contract_observation(contract, observation, current, root)


def test_historical_observations_remain_historical_and_never_expire_into_deletion(execution):
    contract, observation, current, root = execution
    observation.update(claim_current=False, source_fingerprint="b" * 64)
    result = registry.assess_contract_observation(contract, observation, current, root)
    assert result["freshness"] == "HISTORICAL_ONLY"
    assert (root / observation["artifacts"][0]["path"]).is_file()


def test_changed_source_selects_relevant_checks_without_reducing_mandatory_ci(manifest):
    plan = registry.source_impact_plan(manifest, ["app/services/ib_fetch_executor.py"])
    assert [c["id"] for c in plan["affected_contracts"]] == ["IB_ADMISSION"]
    assert any("invalid_response" in t for t in plan["direct_fast_tests"])
    assert any("frozen_context_visibility" in t for t in plan["required_native_browser_tests"])
    assert plan["a04_refresh_required"] and plan["plan_only"]
    assert plan["required_release_lanes"] == manifest["full_release"]["base_lanes"]
    assert plan["conditional_extended_lanes"] == manifest["full_release"]["conditional_lanes"]
    assert plan["advisory_manual"] == manifest["full_release"]["advisory_manual"]


def test_high_risk_unknown_source_requires_explicit_owner_and_impact_review(manifest):
    plan = registry.source_impact_plan(manifest, ["app/new_unreviewed_financial_writer.py"])
    assert plan["review_required"] and plan["status"] == "REVIEW_REQUIRED"
    assert plan["unknown_impact"] == [
        {
            "path": "app/new_unreviewed_financial_writer.py",
            "owner": "UNKNOWN",
            "impact": "NOT_VERIFIED",
            "risk": "HIGH",
        }
    ]
    assert plan["current_coverage"] == "NOT_VERIFIED"


def test_historical_tools_are_review_only_and_never_selected_as_tests(manifest):
    path = "scripts/forensics/run159_ceri_quarantine.py"
    plan = registry.source_impact_plan(manifest, [path])
    assert plan["historical_manual_review_only"] == [path]
    assert plan["direct_fast_tests"] == plan["required_native_browser_tests"] == []


def test_unknown_baseline_and_selection_policy_preserve_required_obligations(manifest):
    plan = registry.source_impact_plan(manifest, ["pyproject.toml"], baseline_known=False)
    assert len(plan["affected_contracts"]) == 10 and plan["review_required"]
    assert set(plan["conditional_extended_lanes"]) == {"ceri-extended", "population-extended"}


@pytest.fixture
def comparison_repository(tmp_path, monkeypatch):
    root = tmp_path / "comparison-repository"
    root.mkdir()

    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()

    git("init", "-b", "main")
    git("config", "user.name", "Baseline policy test")
    git("config", "user.email", "test@example.invalid")
    git("commit", "--allow-empty", "-m", "root")
    parent = git("rev-parse", "HEAD")
    git("commit", "--allow-empty", "-m", "snapshot")
    source = git("rev-parse", "HEAD")
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps({"repository": {
        "full_name": "ivobog/swing-lens", "default_branch": "main",
    }}))
    for key, value in {
        "GITHUB_EVENT_PATH": str(event_path), "GITHUB_REPOSITORY": "ivobog/swing-lens",
        "GITHUB_REF": "refs/heads/main", "GITHUB_SHA": source,
    }.items():
        monkeypatch.setenv(key, value)
    return root, parent, source, event_path, git


@pytest.mark.parametrize("event", ["schedule", "workflow_dispatch"])
def test_trusted_snapshot_uses_verified_first_parent(
    comparison_repository, monkeypatch, event, manifest,
):
    root, parent, source, _event_path, _git = comparison_repository
    monkeypatch.setenv("GITHUB_EVENT_NAME", event)
    comparison = registry.ci_comparison_baseline(root)
    assert comparison == {
        "event": event, "tested_source": source, "baseline": parent,
        "policy": "trusted-default-branch-first-parent-snapshot-v1", "reason": None,
    }
    plan = registry.source_impact_plan(manifest, ["app/new_financial_writer.py"])
    assert plan["review_required"] and plan["unknown_impact"]
    assert plan["required_release_lanes"] == manifest["full_release"]["base_lanes"]


@pytest.mark.parametrize(
    "fault", ["root", "shallow", "untrusted-ref", "wrong-source", "missing-parent"],
)
def test_snapshot_missing_or_untrusted_history_remains_unknown(
    comparison_repository, monkeypatch, fault,
):
    root, parent, _source, _event_path, git = comparison_repository
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    if fault == "root":
        git("checkout", "--detach", parent)
        monkeypatch.setenv("GITHUB_SHA", parent)
    elif fault == "shallow":
        (root / ".git/shallow").write_text(git("rev-parse", "HEAD") + "\n")
    elif fault == "missing-parent":
        object_path = root / ".git/objects" / parent[:2] / parent[2:]
        object_path.chmod(0o600)
        object_path.unlink()
    elif fault == "wrong-source":
        monkeypatch.setenv("GITHUB_SHA", parent)
    else:
        monkeypatch.setenv("GITHUB_REF", "refs/heads/feature")
    comparison = registry.ci_comparison_baseline(root)
    assert comparison["baseline"] is None and comparison["reason"]


@pytest.mark.parametrize("event", ["pull_request", "push"])
@pytest.mark.parametrize("valid", [True, False])
def test_event_baseline_preserves_pr_and_push_selection(
    comparison_repository, monkeypatch, event, valid,
):
    root, parent, _source, event_path, _git = comparison_repository
    base = parent if valid else "f" * 40
    payload = {"before": base} if event == "push" else {"pull_request": {"base": {"sha": base}}}
    event_path.write_text(json.dumps(payload))
    monkeypatch.setenv("GITHUB_EVENT_NAME", event)
    comparison = registry.ci_comparison_baseline(root)
    assert comparison["baseline"] == (parent if valid else None)


@pytest.mark.parametrize("field", ["tested_source", "baseline", "policy", "baseline_known"])
def test_release_policy_rejects_changed_comparison_evidence(
    comparison_repository, monkeypatch, manifest, field,
):
    from scripts.qa import release_evidence as release

    root, _parent, _source, _event_path, _git = comparison_repository
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    comparison = registry.ci_comparison_baseline(root)
    monkeypatch.setattr(registry, "ci_comparison_baseline", lambda _root: comparison)
    impact = registry.source_impact_plan(manifest, [])
    impact["comparison"] = copy.deepcopy(comparison)
    assert release.core_policy(impact)["base_lanes"]
    if field == "baseline_known":
        impact[field] = False
    else:
        impact["comparison"][field] = "foreign"
    with pytest.raises(release.EvidenceError, match="comparison|stale"):
        release.core_policy(impact)


def test_ci_emits_impact_plan_and_retains_original_a16_producer():
    workflow = (registry.ROOT / ".github/workflows/ci.yml").read_text()
    assert "python -m scripts.qa.check_certification_registry --check --ci-impact" in workflow
    assert "--prepare-release" in workflow and "--aggregate-release" in workflow
    assert "path: test-results/release-plan/*.json" in workflow
