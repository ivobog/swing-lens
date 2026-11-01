"""Controlled API doubles only; these are never real owner acknowledgements."""

import copy
import hashlib
import json
import shutil

import pytest
import yaml
from test_golden_update_governance import isolated, proposal  # noqa: F401

from scripts.qa import golden_provenance as provenance


def encoded(value):
    return (json.dumps(value, indent=2) + "\n").encode()


class SyntheticGitHub:
    """Test transport only; no credentials, sockets, GitHub account or approval."""

    def __init__(self, root, candidate):
        self.root, self.candidate = root, candidate
        self.head, self.base, self.merge = "b" * 40, "c" * 40, "d" * 40
        self.owner_text = b"* @ivobog\n"
        self.owner_node = "OWNER_NODE"
        self.actor = {"__typename": "User", "login": "ivobog", "id": self.owner_node}
        self.editor = None
        self.last_edited = None
        self.wrong_repo = False
        self.corrupt_history = False
        self.calls = []
        self.pr = {
            "number": 7,
            "state": "open",
            "draft": False,
            "merged": False,
            "user": {"login": "ivobog", "type": "User"},
            "node_id": "PR_NODE",
            "body": "",
            "head": {"sha": self.head},
            "merge_commit_sha": self.merge,
            "base": {
                "sha": self.base,
                "ref": "main",
                "repo": {"full_name": provenance.GITHUB_REPOSITORY},
            },
        }
        self.branch = {"protected": True, "commit": {"sha": self.base}}
        self.protection = {
            "enforce_admins": {"enabled": True},
            "allow_force_pushes": {"enabled": False},
            "allow_deletions": {"enabled": False},
            "required_pull_request_reviews": {
                "required_approving_review_count": 0,
                "require_code_owner_reviews": False,
                "dismiss_stale_reviews": True,
                "require_last_push_approval": False,
                "bypass_pull_request_allowances": {"users": [], "teams": [], "apps": []},
            },
            "required_status_checks": {
                "strict": True,
                "checks": [
                    {"context": name, "app_id": provenance.GITHUB_ACTIONS_APP_ID}
                    for name in (*provenance.REQUIRED_CHECKS, provenance.APPROVAL_CHECK)
                ],
            },
        }
        self.permission = "admin"
        self.checks = [
            {
                "id": 10 + i,
                "name": name,
                "head_sha": self.head,
                "status": "completed",
                "conclusion": "success",
                "app": {"id": provenance.GITHUB_ACTIONS_APP_ID},
                "details_url": f"https://github.com/{provenance.GITHUB_REPOSITORY}/actions/runs/123/job/456",
            }
            for i, name in enumerate(provenance.REQUIRED_CHECKS)
        ]
        self.ci = {
            "event": "pull_request",
            "path": ".github/workflows/ci.yml",
            "head_sha": self.head,
            "conclusion": "success",
            "pull_requests": [{"number": 7, "head": {"sha": self.head}, "base": {"ref": "main"}}],
        }
        self.lineage = {"status": "ahead", "merge_base_commit": {"sha": "a" * 40}}
        self.race = False
        self.symlink = None
        self.reseal()

    def reseal(self, *, approve=True):
        candidate = self.candidate["candidate"]
        completion_name = candidate["completion_manifest"]
        seal = json.loads((self.root / completion_name).read_text())
        for name in seal["artifacts"]:
            path = (self.root / completion_name).parent / name
            seal["artifacts"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
        (self.root / completion_name).write_bytes(encoded(seal))
        for name in candidate["artifacts"]:
            candidate["artifacts"][name] = hashlib.sha256(
                (self.root / name).read_bytes()
            ).hexdigest()
        candidate["completion_manifest_sha256"] = candidate["artifacts"][completion_name]
        self.candidate["expectation_changes"] = [
            {
                "artifact": old,
                "pointer": pointer,
                "before": before,
                "after": after,
                "reason": "Synthetic controlled difference; not a proposed financial revision",
            }
            for old, new in candidate["expectation_artifacts"].items()
            for pointer, (before, after) in provenance._differences(
                json.loads((self.root / old).read_text()), json.loads((self.root / new).read_text())
            ).items()
        ]
        request = self.candidate["review_request"]
        request.update(
            candidate_digest=provenance.proposal_digest(self.candidate),
            expectation_revision=candidate["expectation_revision"],
            input_version=candidate["input_version"],
        )
        self.files = sorted(set(candidate["artifacts"]) | {provenance.PROPOSAL_PATH})
        self.pr["changed_files"] = len(self.files)
        if approve:
            self.pr["body"] = (
                "A13-SOLO-OWNER-ACK head:"
                + self.head
                + " sha256:"
                + provenance.proposal_digest(self.candidate)
            )
        proposal_path = self.root / provenance.PROPOSAL_PATH
        proposal_path.parent.mkdir(parents=True, exist_ok=True)
        proposal_path.write_bytes(encoded(self.candidate))

    def file(self, path, ref):
        self.calls.append(("file", path, ref))
        if path == "CODEOWNERS":
            return self.owner_text
        if path in (provenance.GITHUB_WORKFLOW, ".github/workflows/ci.yml"):
            return b"synthetic unchanged trusted workflow\n"
        if path == provenance.PROPOSAL_PATH:
            return encoded(self.candidate)
        data = (self.root / path).read_bytes()
        if (
            self.corrupt_history
            and ref == self.head
            and path.startswith(provenance.GOLDEN.as_posix())
        ):
            return data + b"tampered"
        return data

    def pages(self, path, key=None):
        self.calls.append(("pages", path))
        if path.endswith("/files"):
            return [{"filename": name} for name in self.files]
        if "/check-runs" in path:
            return copy.deepcopy(self.checks)
        raise AssertionError(path)

    def get(self, path):
        self.calls.append(("get", path))
        prefix = f"/repos/{provenance.GITHUB_REPOSITORY}"
        if path.endswith("/actions/permissions/workflow"):
            return {
                "default_workflow_permissions": "read",
                "can_approve_pull_request_reviews": False,
            }
        if "/check-runs?per_page=1" in path:
            return {"check_runs": copy.deepcopy(self.checks), "total_count": len(self.checks)}
        if path == prefix:
            return {
                "full_name": "other/repo" if self.wrong_repo else provenance.GITHUB_REPOSITORY,
                "default_branch": "main",
                "owner": {"login": "ivobog", "type": "User", "node_id": self.owner_node},
            }
        if path.endswith("/branches/main"):
            return copy.deepcopy(self.branch)
        if path.endswith("/protection"):
            return copy.deepcopy(self.protection)
        if "/codeowners/errors" in path:
            return {"errors": []}
        if "/git/commits/" in path:
            return {"tree": {"sha": "e" * 40}}
        if "/git/trees/" in path:
            return {
                "truncated": False,
                "tree": [{"path": "CODEOWNERS", "mode": "100644", "type": "blob"}]
                + [
                    {
                        "path": p.relative_to(self.root).as_posix(),
                        "type": "blob",
                        "mode": "120000"
                        if p.relative_to(self.root).as_posix() == self.symlink
                        else "100644",
                    }
                    for p in self.root.rglob("*")
                    if p.is_file()
                ],
            }
        if "/compare/" in path:
            return self.lineage
        if "/permission" in path:
            return {"permission": self.permission}
        if "/actions/runs/" in path:
            return copy.deepcopy(self.ci)
        if path.endswith("/pulls/7"):
            pr = copy.deepcopy(self.pr)
            if self.race:
                pr["head"]["sha"] = "f" * 40
            return pr
        raise AssertionError(path)

    def owner_acknowledgement(self, number):
        self.calls.append(("owner_acknowledgement", number))
        return {
            "nameWithOwner": provenance.GITHUB_REPOSITORY,
            "owner": {"__typename": "User", "login": "ivobog", "id": self.owner_node},
            "pullRequest": {
                "id": self.pr["node_id"],
                "number": number,
                "body": self.pr["body"],
                "headRefOid": self.pr["head"]["sha"],
                "baseRefOid": self.base,
                "author": self.actor,
                "editor": self.editor,
                "lastEditedAt": self.last_edited,
            },
        }


@pytest.fixture
def synthetic_remote(proposal, monkeypatch):  # noqa: F811
    root, candidate = proposal
    old = provenance.GOLDEN
    new = "proposal-only/complete-input"
    shutil.copytree(root / old, root / new)
    inputs = json.loads((root / new / "manifest.json").read_text())
    inputs["version"] = candidate["candidate"]["input_version"]
    (root / new / "manifest.json").write_bytes(encoded(inputs))
    identity = hashlib.sha256(
        json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    supplement = json.loads((root / new / "oracle-completion/manifest.json").read_text())
    supplement.update(
        expectation_revision=candidate["candidate"]["expectation_revision"],
        base_input_identity=identity,
    )
    (root / new / "oracle-completion/manifest.json").write_bytes(encoded(supplement))
    core = root / new / "oracle-completion/core-expected.json"
    value = json.loads(core.read_text())
    value["technical"][0]["dual_score"] = "9.4321"
    core.write_bytes(encoded(value))
    candidate["candidate"].update(
        input_manifest=new + "/manifest.json",
        input_identity=identity,
        input_manifest_sha256=hashlib.sha256(
            (root / new / "manifest.json").read_bytes()
        ).hexdigest(),
        completion_manifest=new + "/oracle-completion/manifest.json",
        artifacts={
            p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (root / new).rglob("*")
            if p.is_file()
        },
        expectation_artifacts={
            name: name.replace(old.as_posix(), new, 1)
            for name in candidate["candidate"]["expectation_artifacts"]
        },
    )
    remote = SyntheticGitHub(root, candidate)
    monkeypatch.setattr(provenance, "_GitHubReader", lambda _token: remote)
    monkeypatch.setattr(provenance, "_trusted_github_context", lambda *_a: copy.deepcopy(remote.pr))
    assert provenance.inspect_proposal(candidate, root=root)["proposal_content_integrity"] == "PASS"
    provenance._require_candidate_complete(candidate, root)
    return remote


def test_owner_authorization_binds_complete_candidate_and_never_activates(synthetic_remote):
    remote = synthetic_remote
    baseline = provenance.verify_baseline(remote.root)
    result = provenance.verify_github_update(7, root=remote.root)
    assert result["owner_authorization"] == "SOLO_OWNER_AUTHORIZED"
    assert result["approval_model"] == "SOLE_OWNER"
    assert result["independent_human_review"] == "NOT_CLAIMED"
    assert result["owner"] == "ivobog" == remote.pr["user"]["login"]
    assert result["review_authorized"] and not result["activation_authorized"]
    assert result["financial_equivalence"] == "NOT_EVALUATED"
    assert result["proposal_digest"] == provenance.proposal_digest(remote.candidate)
    assert result["head_sha"] == remote.head and result["base_sha"] == remote.base
    assert provenance.verify_baseline(remote.root) == baseline
    assert result["financial_certificate_authority"] == "ORIGINAL_A04_AND_A16_REMAIN_MANDATORY"


@pytest.mark.parametrize(
    "fault,code",
    [
        ("unauthorized_author", "GITHUB_OWNER_ACK_UNAUTHORIZED_ACTOR"),
        ("unauthorized_editor", "GITHUB_OWNER_ACK_UNAUTHORIZED_ACTOR"),
        ("bot", "GITHUB_OWNER_ACK_UNAUTHORIZED_ACTOR"),
        ("unknown_editor", "GITHUB_OWNER_EDITOR_UNVERIFIED"),
        ("wrong_repo", "GITHUB_CONFIGURED_REPOSITORY_OR_BRANCH_MISMATCH"),
        ("nonowner", "GITHUB_OWNER_CODEOWNERS_REQUIRED"),
        ("old_commit", "GITHUB_OWNER_ACK_STALE_HEAD"),
        ("changed_digest", "GITHUB_OWNER_ACK_DIGEST_MISMATCH"),
        ("forged_metadata", "GITHUB_OWNER_ACK_REQUIRED"),
        ("missing_ack", "GITHUB_OWNER_ACK_REQUIRED"),
        ("duplicate_ack", "GITHUB_OWNER_ACK_REQUIRED"),
        ("unprotected", "GITHUB_TARGET_BRANCH_UNPROTECTED"),
        ("review_lockout", "GITHUB_SOLO_OWNER_REVIEW_LOCKOUT"),
        ("bypass", "GITHUB_PROTECTION_ADMIN_BYPASS"),
        ("unknown_bypass", "GITHUB_PROTECTION_BYPASS_OR_UNKNOWN"),
        ("failed_ci", "GITHUB_REQUIRED_CI_MISSING_OR_FAILED"),
        ("missing_ci", "GITHUB_REQUIRED_CI_MISSING_OR_FAILED"),
        ("wrong_check_head", "GITHUB_REQUIRED_CI_MISSING_OR_FAILED"),
        ("wrong_workflow", "GITHUB_REQUIRED_CI_SOURCE_UNTRUSTED"),
        ("missing_derivation", "PRODUCTION_OUTPUTS_CANNOT_AUTHOR_EXPECTATIONS"),
        ("incomplete_oracle", "GITHUB_INDEPENDENT_ORACLE_INCOMPLETE"),
        ("historical_change", "GITHUB_HISTORICAL_GOLDEN_CHANGED"),
        ("symlink", "GITHUB_REGULAR_DATA_FILE_REQUIRED"),
        ("raced_head", "GITHUB_PR_CHANGED_DURING_VERIFICATION"),
    ],
)
def test_owner_authorization_faults_fail_at_the_intended_boundary(synthetic_remote, fault, code):
    remote = synthetic_remote
    if fault == "unauthorized_author":
        remote.actor = {"__typename": "User", "login": "other", "id": "OTHER"}
    elif fault == "unauthorized_editor":
        remote.editor = {"__typename": "User", "login": "other", "id": "OTHER"}
    elif fault == "bot":
        remote.actor["__typename"] = "Bot"
    elif fault == "unknown_editor":
        remote.last_edited = "2026-11-01T12:00:00Z"
    elif fault == "wrong_repo":
        remote.wrong_repo = True
    elif fault == "nonowner":
        remote.owner_text = b"* @other\n"
    elif fault == "old_commit":
        remote.pr["body"] = remote.pr["body"].replace(remote.head, "a" * 40)
    elif fault == "changed_digest":
        remote.candidate["affected_obligations"].append("Later controlled change")
        remote.reseal(approve=False)
    elif fault == "forged_metadata":
        remote.candidate["review_request"]["claimed_owner"] = "ivobog"
        remote.pr["body"] = ""
    elif fault == "missing_ack":
        remote.pr["body"] = ""
    elif fault == "duplicate_ack":
        remote.pr["body"] += "\n" + remote.pr["body"]
    elif fault == "unprotected":
        remote.branch["protected"] = False
    elif fault == "review_lockout":
        remote.protection["required_pull_request_reviews"]["required_approving_review_count"] = 1
    elif fault == "bypass":
        remote.protection["enforce_admins"]["enabled"] = False
    elif fault == "unknown_bypass":
        del remote.protection["required_pull_request_reviews"]["bypass_pull_request_allowances"]
    elif fault == "failed_ci":
        remote.checks[0]["conclusion"] = "failure"
    elif fault == "missing_ci":
        remote.checks = []
    elif fault == "wrong_check_head":
        remote.checks[0]["head_sha"] = "f" * 40
    elif fault == "wrong_workflow":
        remote.ci["path"] = ".github/workflows/untrusted.yml"
    elif fault == "missing_derivation":
        remote.candidate["independent_derivation"]["artifacts"] = {}
        remote.reseal()
    elif fault == "incomplete_oracle":
        path = (
            remote.root
            / remote.candidate["candidate"]["expectation_artifacts"][
                (provenance.GOLDEN / "oracle-completion/core-expected.json").as_posix()
            ]
        )
        value = json.loads(path.read_text())
        value["technical"].pop()
        path.write_bytes(encoded(value))
        remote.reseal()
    elif fault == "historical_change":
        remote.corrupt_history = True
    elif fault == "symlink":
        remote.symlink = next(iter(remote.candidate["candidate"]["artifacts"]))
    else:
        remote.race = True
    with pytest.raises(provenance.ProvenanceError, match="^" + code + "$"):
        provenance.verify_github_update(7, root=remote.root)


def test_owner_can_acknowledge_another_authors_pr_without_a_second_reviewer(synthetic_remote):
    remote = synthetic_remote
    remote.pr["user"]["login"] = "contributor"
    remote.actor = {"__typename": "User", "login": "contributor", "id": "OTHER"}
    remote.editor = {"__typename": "User", "login": "ivobog", "id": remote.owner_node}
    remote.last_edited = "2026-11-01T12:00:00Z"
    remote.protection["required_pull_request_reviews"] = None
    assert (
        provenance.verify_github_update(7, root=remote.root)["owner_authorization"]
        == "SOLO_OWNER_AUTHORIZED"
    )


def test_unauthenticated_or_push_context_cannot_authorize(monkeypatch, tmp_path):
    with pytest.raises(provenance.ProvenanceError, match="^GITHUB_READ_CREDENTIAL_REQUIRED$"):
        provenance._GitHubReader("")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    with pytest.raises(provenance.ProvenanceError, match="^GITHUB_TRUSTED_PR_CONTEXT_REQUIRED$"):
        provenance._trusted_github_context(None, 7, tmp_path)


def test_trusted_workflow_refreshes_on_description_and_head_changes():
    workflow = yaml.load(
        (provenance.ROOT / provenance.GITHUB_WORKFLOW).read_text(), Loader=yaml.BaseLoader
    )
    assert set(workflow["on"]) == {"pull_request_target"}
    assert {"edited", "synchronize"} <= set(workflow["on"]["pull_request_target"]["types"])
    assert workflow["permissions"] == "read-all"
    steps = workflow["jobs"]["authenticated-golden-approval"]["steps"]
    assert steps[0]["with"]["ref"] == "${{ github.event.pull_request.base.sha }}"
    assert steps[0]["with"]["persist-credentials"] == "false"
    assert all("head.sha" not in str(step) and "pip " not in str(step) for step in steps)


def test_no_golden_change_never_claims_owner_authorization(synthetic_remote):
    remote = synthetic_remote
    remote.files = ["app/unrelated.json"]
    remote.pr["changed_files"] = 1
    remote.branch["protected"] = False
    result = provenance.verify_github_update(7, root=remote.root)
    assert result["owner_authorization"] == "NOT_APPLICABLE_NO_GOLDEN_CHANGE"
    assert not result["activation_authorized"]


def test_owner_query_is_fixed_read_only_and_permission_errors_fail_closed(monkeypatch):
    reader = provenance._GitHubReader("synthetic-not-a-credential")
    captured = []

    def respond(request):
        captured.append(request)
        return {"data": {"repository": {"nameWithOwner": provenance.GITHUB_REPOSITORY}}}

    monkeypatch.setattr(reader, "_read_request", respond)
    assert reader.owner_acknowledgement(7)["nameWithOwner"] == provenance.GITHUB_REPOSITORY
    request = captured[0]
    payload = json.loads(request.data)
    assert request.full_url == "https://api.github.com/graphql"
    assert request.get_method() == "POST"
    assert payload["query"].startswith("query OwnerAcknowledgement")
    assert "mutation" not in payload["query"]
    assert payload["variables"] == {"number": 7}
    for number in [True, -1, "7", "7) { mutation }"]:
        with pytest.raises(provenance.ProvenanceError, match="^GITHUB_INVALID_PR_NUMBER$"):
            reader.owner_acknowledgement(number)
    monkeypatch.setattr(reader, "_read_request", lambda _: {"errors": [{"type": "FORBIDDEN"}]})
    with pytest.raises(provenance.ProvenanceError, match="^GITHUB_OWNER_IDENTITY_UNAVAILABLE$"):
        reader.owner_acknowledgement(7)


def test_missing_administration_read_is_not_a_local_snapshot(synthetic_remote):
    remote = synthetic_remote
    original = remote.get

    def deny(path):
        if path.endswith("/protection"):
            raise provenance.ProvenanceError("GITHUB_AUTHORITY_UNAVAILABLE_HTTP_403")
        return original(path)

    remote.get = deny
    with pytest.raises(provenance.ProvenanceError, match="^GITHUB_AUTHORITY_UNAVAILABLE_HTTP_403$"):
        provenance.verify_github_update(7, root=remote.root)


@pytest.fixture
def target_context(tmp_path, monkeypatch):
    """Replay the observed R11 run/PR schema; this is not a live approval."""
    base, head = "c" * 40, "b" * 40
    pr = {
        "number": 76,
        "head": {"sha": head},
        "base": {"sha": base, "ref": "main", "repo": {"full_name": provenance.GITHUB_REPOSITORY}},
    }
    association = {"number": 76, "head": {"sha": head}, "base": {"sha": base, "ref": "main"}}
    run = {
        "event": "pull_request_target",
        "path": provenance.GITHUB_WORKFLOW,
        "head_sha": head,
        "status": "in_progress",
        "pull_requests": [association],
    }
    event = {"number": 76, "pull_request": copy.deepcopy(pr)}
    event_path = tmp_path / "event.json"
    event_path.write_bytes(encoded(event))
    for key, value in {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "pull_request_target",
        "GITHUB_REPOSITORY": provenance.GITHUB_REPOSITORY,
        "GITHUB_RUN_ID": "123",
        "GITHUB_EVENT_PATH": str(event_path),
        "GITHUB_API_URL": "https://api.github.com",
    }.items():
        monkeypatch.setenv(key, value)
    state = {"checkout": base, "dirty": False, "pr": pr, "run": run, "verifier": True}

    def git(args, **kwargs):
        return (
            state["checkout"] + "\n"
            if args[1:3] == ["rev-parse", "HEAD"]
            else ("modified" if state["dirty"] else "")
        )

    class ContextAPI:
        def get(self, path):
            return copy.deepcopy(state["run"] if "/actions/runs/" in path else state["pr"])

        def file(self, path, ref):
            assert path == "scripts/qa/golden_provenance.py" and ref == base
            return (
                provenance.Path(provenance.__file__).read_bytes() if state["verifier"] else b"wrong"
            )

    monkeypatch.setattr(provenance.subprocess, "check_output", git)
    return state, ContextAPI(), tmp_path


def test_target_context_accepts_real_github_PR_head_with_trusted_base_checkout(target_context):
    state, api, root = target_context
    assert state["run"]["head_sha"] != state["checkout"]
    assert provenance._trusted_github_context(api, 76, root) == state["pr"]


@pytest.mark.parametrize(
    "fault,code",
    [
        ("wrong_head", "GITHUB_TRUSTED_WORKFLOW_REQUIRED"),
        ("run_bound_to_base", "GITHUB_TRUSTED_WORKFLOW_REQUIRED"),
        ("wrong_PR", "GITHUB_TRUSTED_WORKFLOW_REQUIRED"),
        ("missing_PR", "GITHUB_TRUSTED_WORKFLOW_REQUIRED"),
        ("wrong_run_base", "GITHUB_TRUSTED_WORKFLOW_REQUIRED"),
        ("wrong_workflow", "GITHUB_TRUSTED_WORKFLOW_REQUIRED"),
        ("wrong_event", "GITHUB_TRUSTED_WORKFLOW_REQUIRED"),
        ("completed_run", "GITHUB_TRUSTED_WORKFLOW_REQUIRED"),
        ("checkout_PR_code", "GITHUB_TRUSTED_BASE_CHECKOUT_REQUIRED"),
        ("dirty_base", "GITHUB_TRUSTED_BASE_CHECKOUT_REQUIRED"),
        ("wrong_verifier", "GITHUB_TRUSTED_VERIFIER_SOURCE_REQUIRED"),
    ],
)
def test_target_context_retains_source_and_PR_binding_failures(target_context, fault, code):
    state, api, root = target_context
    if fault == "wrong_head":
        state["run"]["head_sha"] = "f" * 40
    elif fault == "run_bound_to_base":
        state["run"]["head_sha"] = state["checkout"]
    elif fault == "wrong_PR":
        state["run"]["pull_requests"][0]["number"] = 75
    elif fault == "missing_PR":
        state["run"]["pull_requests"] = []
    elif fault == "wrong_run_base":
        state["run"]["pull_requests"][0]["base"]["sha"] = "f" * 40
    elif fault == "wrong_workflow":
        state["run"]["path"] = ".github/workflows/untrusted.yml"
    elif fault == "wrong_event":
        state["run"]["event"] = "pull_request"
    elif fault == "completed_run":
        state["run"]["status"] = "completed"
    elif fault == "checkout_PR_code":
        state["checkout"] = state["pr"]["head"]["sha"]
    elif fault == "dirty_base":
        state["dirty"] = True
    else:
        state["verifier"] = False
    with pytest.raises(provenance.ProvenanceError, match="^" + code + "$"):
        provenance._trusted_github_context(api, 76, root)


def test_non_golden_PR_proves_read_capability_without_financial_ack(synthetic_remote):
    remote = synthetic_remote
    remote.files = ["docs/testing/enforcement_pr_verification.md"]
    remote.pr["changed_files"] = 1
    remote.branch["protected"] = True
    result = provenance.verify_github_update(7, root=remote.root)
    assert result["owner_authorization"] == "NOT_APPLICABLE_NO_GOLDEN_CHANGE"
    assert result["enforcement_api_readiness"] == {
        "administration": "READ_CONFIRMED",
        "checks": "READ_CONFIRMED",
        "protection": "VERIFIED",
    }
    assert not result["activation_authorized"]


def test_non_golden_PR_does_not_hide_a_missing_admin_read(synthetic_remote):
    remote = synthetic_remote
    remote.files = ["docs/testing/enforcement_pr_verification.md"]
    remote.pr["changed_files"] = 1
    remote.branch["protected"] = False
    original = remote.get

    def denied(path):
        if path.endswith("/actions/permissions/workflow"):
            raise provenance.ProvenanceError("GITHUB_AUTHORITY_UNAVAILABLE_HTTP_403")
        return original(path)

    remote.get = denied
    with pytest.raises(provenance.ProvenanceError, match="^GITHUB_AUTHORITY_UNAVAILABLE_HTTP_403$"):
        provenance.verify_github_update(7, root=remote.root)
