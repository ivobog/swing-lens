"""Maintenance extends the existing GitHub check without authorizing golden updates."""

import copy
from pathlib import Path
from urllib.parse import quote

import pytest
import yaml
from test_golden_github_approval import (  # noqa: F401
    encoded,
    proposal,
    synthetic_remote,
    target_context,
)
from test_golden_update_governance import isolated  # noqa: F401

from scripts.qa import golden_provenance as provenance


@pytest.mark.parametrize(
    ("fault", "error"),
    [
        (None, None),
        ("stale_main", "GITHUB_MAINTENANCE_VERIFIER_NOT_TRUSTED"),
        ("unprotected_main", "GITHUB_MAINTENANCE_VERIFIER_NOT_TRUSTED"),
        ("maintenance_checkout", "GITHUB_TRUSTED_BASE_CHECKOUT_REQUIRED"),
        ("wrong_event_base", "GITHUB_MAINTENANCE_VERIFIER_NOT_TRUSTED"),
        ("wrong_run_base", "GITHUB_TRUSTED_WORKFLOW_REQUIRED"),
        ("unknown_target", "GITHUB_PROTECTED_PR_REQUIRED"),
    ],
)
def test_maintenance_verifier_is_bound_to_protected_main_and_the_live_pr(
    target_context,  # noqa: F811
    monkeypatch,
    fault,
    error,  # noqa: F811
):
    state, api, root = target_context
    original_get, original_file = api.get, api.file
    ref = provenance.MAINTENANCE_BRANCH if fault != "unknown_target" else "maintenance/0097"
    state["pr"]["base"]["ref"] = ref
    state["run"]["pull_requests"][0]["base"]["ref"] = "main" if fault == "wrong_run_base" else ref
    event_pr = copy.deepcopy(state["pr"])
    if fault == "wrong_event_base":
        event_pr["base"]["ref"] = "main"
    (root / "event.json").write_bytes(encoded({"number": 76, "pull_request": event_pr}))
    trusted = "a" * 40
    monkeypatch.setenv("GITHUB_SHA", trusted)
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    if fault != "maintenance_checkout":
        state["checkout"] = trusted

    def get(path):
        if path.endswith("/branches/main"):
            return {
                "protected": fault != "unprotected_main",
                "commit": {"sha": "e" * 40 if fault == "stale_main" else trusted},
            }
        return original_get(path)

    def file(path, ref):
        if ref == trusted:
            return Path(provenance.__file__).read_bytes()
        return original_file(path, ref)

    api.get, api.file = get, file
    if error:
        with pytest.raises(provenance.ProvenanceError, match="^" + error + "$"):
            provenance._trusted_github_context(api, 76, root)
    else:
        assert provenance._trusted_github_context(api, 76, root) == state["pr"]


@pytest.mark.parametrize(
    ("changed", "break_protection", "changed_base", "error"),
    [
        ("app/services/pipeline_executor.py", False, False, None),
        ("app/services/pipeline_executor.py", True, False, "GITHUB_TARGET_BRANCH_UNPROTECTED"),
        (
            "app/services/pipeline_executor.py",
            False,
            True,
            "GITHUB_PROTECTION_CHANGED_DURING_VERIFICATION",
        ),
        (
            "alembic/versions/new_migration.py",
            False,
            False,
            "GITHUB_MAINTENANCE_FINANCIAL_CHANGE_FORBIDDEN",
        ),
        (
            "app/services/ceri/sec/processor_signature.py",
            False,
            False,
            "GITHUB_MAINTENANCE_FINANCIAL_CHANGE_FORBIDDEN",
        ),
        (provenance.PROPOSAL_PATH, False, False, "GITHUB_MAINTENANCE_FINANCIAL_CHANGE_FORBIDDEN"),
    ],
)
def test_maintenance_requires_existing_protection_and_refuses_financial_changes(
    synthetic_remote,  # noqa: F811
    changed,
    break_protection,
    changed_base,
    error,  # noqa: F811
):
    remote = synthetic_remote
    ref = provenance.MAINTENANCE_BRANCH
    ref_path = "/branches/" + quote(ref, safe="")
    remote.pr["base"]["ref"] = ref
    remote.files = [changed]
    remote.pr["changed_files"] = 1
    remote.branch["protected"] = not break_protection
    original = remote.get
    branch_reads = 0

    def get(path):
        nonlocal branch_reads
        if path.endswith(ref_path):
            branch_reads += 1
            branch = copy.deepcopy(remote.branch)
            if changed_base and branch_reads == 2:
                branch["commit"]["sha"] = "f" * 40
            return branch
        return original(path)

    remote.get = get
    if error:
        with pytest.raises(provenance.ProvenanceError, match="^" + error + "$"):
            provenance.verify_github_update(7, root=remote.root)
    else:
        result = provenance.verify_github_update(7, root=remote.root)
        assert result["owner_authorization"] == "NOT_APPLICABLE_NO_GOLDEN_CHANGE"
        assert result["enforcement_api_readiness"]["protection"] == "VERIFIED"
        assert not result["activation_authorized"]
        assert branch_reads == 2
        assert any(path.endswith(ref_path + "/protection") for _, path in remote.calls)


def test_workflow_keeps_trusted_checkout_for_main_and_only_the_approved_maintenance_ref():
    workflow = yaml.load(
        (provenance.ROOT / provenance.GITHUB_WORKFLOW).read_text(), Loader=yaml.BaseLoader
    )
    assert workflow["on"]["pull_request_target"]["branches"] == [
        "main",
        provenance.MAINTENANCE_BRANCH,
    ]
    main, maintenance, *_ = workflow["jobs"]["authenticated-golden-approval"]["steps"]
    assert main["with"] == {
        "ref": "${{ github.event.pull_request.base.sha }}",
        "persist-credentials": "false",
    }
    assert maintenance["if"] == "github.event.pull_request.base.ref == 'maintenance/0096'"
    assert maintenance["with"] == {"ref": "${{ github.sha }}", "persist-credentials": "false"}
