"""Content provenance for the retained oracle; local files never approve updates.

The existing A05/A05B loaders and A04 finalizer retain financial authority.
This adapter reads content and proposal evidence. Only its trusted GitHub Actions
path can verify authenticated review. It never writes or activates expectations
or issues a pipeline certificate.
"""

from __future__ import annotations

import base64
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = Path("tests/e2e/single_run_certification/golden/v3")


class ProvenanceError(ValueError):
    pass


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ProvenanceError("PROVENANCE_OBJECT_REQUIRED")
    return value


def _sealed(root: Path, relative: str, expected: str) -> str:
    path = root / relative
    if (
        Path(relative).is_absolute()
        or ".." in Path(relative).parts
        or not path.is_file()
        or path.is_symlink()
        or not path.resolve().is_relative_to(root.resolve())
        or not re.fullmatch(r"[0-9a-f]{64}", expected)
    ):
        raise ProvenanceError("PROVENANCE_ARTIFACT_UNSAFE_OR_MISSING: " + relative)
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ProvenanceError("PROVENANCE_ARTIFACT_HASH_MISMATCH: " + relative)
    return expected


def verify_baseline(root: Path = ROOT, declaration: dict | None = None) -> dict:
    try:
        return _verify_baseline(root, declaration)
    except ProvenanceError:
        raise
    except (AssertionError, RuntimeError, OSError, ValueError, KeyError, TypeError) as exc:
        raise ProvenanceError("GOLDEN_ORACLE_PROVENANCE_INVALID: " + str(exc)) from exc


def _verify_baseline(root: Path, declaration: dict | None) -> dict:
    """Reuse pinned oracle loading, and verify its archived authoring records.

    A retained reviewed content baseline is distinct from authenticated approval
    of a new version. No policy string or digest establishes that authority.
    """
    sys.path.insert(0, str(ROOT / "tests/e2e"))
    from single_run_certification import golden_completion as completion
    from single_run_certification import golden_oracle as oracle

    if declaration is None:
        declaration = _read(root / "config/certification_registry.json")["golden_update_policy"]
    if (
        declaration.get("schema") != "swinglens.golden-update-policy.v1"
        or declaration.get("automatic_update") != "DENIED"
        or declaration.get("local_approval") != "NEVER_AUTHORIZES"
        or declaration.get("new_version_authority") != "AUTHENTICATED_SOLE_OWNER_ACK_REQUIRED"
        or declaration.get("operator_exception") != "A13-R10"
        or declaration.get("independent_derivation") != "REQUIRED"
        or declaration.get("authenticated_approval_enforcement") != "NOT_VERIFIED"
    ):
        raise ProvenanceError("GOLDEN_APPROVAL_BOUNDARY_DOWNGRADED")
    data = root / GOLDEN
    # These are the original hash/version/expectation checks, not a new oracle.
    inputs = oracle.load_golden(data, verify_sources=False)
    identity = oracle.canonical_hash(inputs)
    supplement = completion.load_completion(identity, data / "oracle-completion")
    artifacts = {
        (GOLDEN / "manifest.json").as_posix(): oracle.MANIFEST_SHA256,
        (GOLDEN / "oracle-completion/manifest.json").as_posix(): completion.COMPLETION_SHA256,
    }
    for name, expected in inputs["artifacts"].items():
        artifacts[(GOLDEN / name).as_posix()] = expected
    for name, expected in supplement["artifacts"].items():
        artifacts[(GOLDEN / "oracle-completion" / name).as_posix()] = expected
    artifacts.update(inputs["config_sources"])
    archive = declaration["authoring_archive"]
    if archive != "docs/testing/golden-provenance/a05b-r3":
        raise ProvenanceError("GOLDEN_AUTHORING_ARCHIVE_CHANGED_WITHOUT_REVIEW")
    for name, expected in supplement["authoring_provenance"].items():
        artifacts[archive + "/" + name + ".txt"] = expected
    for name, expected in artifacts.items():
        _sealed(root, name, expected)
    # Original semantic completeness and obligation expansion stay authoritative.
    for handoff in (False, True):
        oracle.require_complete(oracle.expected_manifest(handoff))
    return {
        "schema": "swinglens.golden-provenance.v1",
        "content_integrity": "PASS",
        "baseline": {
            "input_version": inputs["version"],
            "input_manifest_sha256": oracle.MANIFEST_SHA256,
            "input_identity": identity,
            "expectation_revision": supplement["expectation_revision"],
            "completion_manifest_sha256": completion.COMPLETION_SHA256,
        },
        "artifacts": artifacts,
        "contracts": sorted(supplement["contracts"]),
        "sections": len(supplement["domains"]),
        "expected_rows": sum(len(rows) for rows in supplement["domains"].values()),
        "relationships": len(completion.relationship_inventory({"completion": supplement})),
        "authenticated_update_approval": "NOT_VERIFIED",
        "new_version_activation": "BLOCKED_PENDING_AUTHENTICATED_OWNER_ACK",
    }


def proposal_digest(proposal: dict) -> str:
    payload = {key: value for key, value in proposal.items() if key != "review_request"}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _differences(before, after, pointer="") -> dict:
    """Inventory content changes, without deriving financial expectations."""
    if type(before) is type(after) and isinstance(before, dict):
        result = {}
        for key in sorted(before.keys() | after.keys()):
            child = pointer + "/" + key.replace("~", "~0").replace("/", "~1")
            if key not in before:
                result[child] = ({"absent": True}, after[key])
            elif key not in after:
                result[child] = (before[key], {"absent": True})
            else:
                result.update(_differences(before[key], after[key], child))
        return result
    if type(before) is type(after) and isinstance(before, list):
        result = {}
        for i in range(max(len(before), len(after))):
            child = pointer + "/" + str(i)
            if i >= len(before):
                result[child] = ({"absent": True}, after[i])
            elif i >= len(after):
                result[child] = (before[i], {"absent": True})
            else:
                result.update(_differences(before[i], after[i], child))
        return result
    return {pointer: (before, after)} if type(before) is not type(after) or before != after else {}


def inspect_proposal(proposal: dict, *, root: Path = ROOT) -> dict:
    """Check proposed content and review binding; never authenticate approval.

    Future financial equivalence still requires the original independent-oracle
    and native A04 review. A valid proposal is not an approved oracle revision.
    """
    required = {
        "schema",
        "previous",
        "candidate",
        "contract_change",
        "independent_derivation",
        "expectation_changes",
        "affected_obligations",
        "historical_artifacts",
        "review_request",
    }
    if (
        not required <= proposal.keys()
        or proposal["schema"] != "swinglens.golden-update-proposal.v1"
    ):
        raise ProvenanceError("GOLDEN_PROPOSAL_FIELDS_MISSING")
    baseline = verify_baseline(root)
    if proposal["previous"] != baseline["baseline"]:
        raise ProvenanceError("GOLDEN_PREVIOUS_VERSION_MISMATCH")
    candidate = proposal["candidate"]
    if (
        not candidate.get("input_version")
        or not candidate.get("expectation_revision")
        or not candidate.get("artifacts")
        or (candidate["input_version"], candidate["expectation_revision"])
        == (proposal["previous"]["input_version"], proposal["previous"]["expectation_revision"])
        or not all(
            re.fullmatch(r"[0-9a-f]{64}", candidate.get(key, ""))
            for key in ("input_manifest_sha256", "completion_manifest_sha256", "input_identity")
        )
    ):
        raise ProvenanceError("GOLDEN_NEW_VERSION_IDENTITY_REQUIRED")
    change = proposal["contract_change"]
    if (
        not change.get("reason")
        or not re.fullmatch(r"[0-9a-f]{40}", change.get("source_revision", ""))
        or not change.get("configuration_sha256")
    ):
        raise ProvenanceError("GOLDEN_FINANCIAL_CONTRACT_AND_SOURCE_REQUIRED")
    derivation = proposal["independent_derivation"]
    if (
        derivation.get("method") != "INDEPENDENT_FORMULA_REVIEW"
        or derivation.get("production_outputs_used") is not False
        or not derivation.get("artifacts")
    ):
        raise ProvenanceError("PRODUCTION_OUTPUTS_CANNOT_AUTHOR_EXPECTATIONS")
    for declarations in (
        candidate["artifacts"],
        derivation["artifacts"],
        change["configuration_sha256"],
    ):
        for name, expected in declarations.items():
            _sealed(root, name, expected)
    for field, hash_field in (
        ("input_manifest", "input_manifest_sha256"),
        ("completion_manifest", "completion_manifest_sha256"),
    ):
        if candidate.get(field) not in candidate["artifacts"] or (
            candidate["artifacts"][candidate[field]] != candidate[hash_field]
        ):
            raise ProvenanceError("GOLDEN_CANDIDATE_MANIFEST_HASH_UNBOUND")
    input_manifest = _read(root / candidate["input_manifest"])
    completion_manifest = _read(root / candidate["completion_manifest"])
    if (
        input_manifest.get("version") != candidate["input_version"]
        or completion_manifest.get("expectation_revision") != candidate["expectation_revision"]
        or completion_manifest.get("base_input_identity") != candidate["input_identity"]
        or hashlib.sha256(
            json.dumps(
                input_manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ).encode()
        ).hexdigest()
        != candidate["input_identity"]
    ):
        raise ProvenanceError("GOLDEN_CANDIDATE_INPUT_EXPECTATION_VERSION_MISMATCH")
    if proposal["historical_artifacts"] != baseline["artifacts"]:
        raise ProvenanceError("GOLDEN_HISTORY_PRESERVATION_REQUIRED")
    for name, expected in proposal["historical_artifacts"].items():
        _sealed(root, name, expected)
    mappings = candidate.get("expectation_artifacts", {})
    expected_files = {
        name
        for name in baseline["artifacts"]
        if name.endswith(("/expected-results.json", "-expected.json"))
    }
    if set(mappings) != expected_files:
        raise ProvenanceError("GOLDEN_EXPECTATION_ARTIFACT_SET_INCOMPLETE")
    actual_changes = {}
    for old_name, new_name in mappings.items():
        if new_name not in candidate["artifacts"] or new_name in baseline["artifacts"]:
            raise ProvenanceError("GOLDEN_EXPECTATIONS_MUST_USE_NEW_VERSION_PATHS")
        for pointer, values in _differences(_read(root / old_name), _read(root / new_name)).items():
            actual_changes[(old_name, pointer)] = values
    differences = proposal["expectation_changes"]
    if not differences or any(
        not {"artifact", "pointer", "before", "after", "reason"} <= row.keys()
        or not row["reason"]
        or row["before"] == row["after"]
        for row in differences
    ):
        raise ProvenanceError("GOLDEN_EACH_EXPECTATION_CHANGE_REQUIRES_REASON")
    declared = {
        (row["artifact"], row["pointer"]): (row["before"], row["after"]) for row in differences
    }
    if len(declared) != len(differences) or json.dumps(
        sorted((artifact, pointer, *values) for (artifact, pointer), values in declared.items())
    ) != json.dumps(
        sorted(
            (artifact, pointer, *values) for (artifact, pointer), values in actual_changes.items()
        )
    ):
        raise ProvenanceError("GOLDEN_NUMERIC_SEMANTIC_DIFF_NOT_FULLY_JUSTIFIED")
    if not proposal["affected_obligations"]:
        raise ProvenanceError("GOLDEN_AFFECTED_A04_A05B_OBLIGATIONS_REQUIRED")
    request = proposal["review_request"]
    if (
        request.get("candidate_digest") != proposal_digest(proposal)
        or request.get("source_revision") != change["source_revision"]
        or request.get("input_version") != candidate["input_version"]
        or request.get("expectation_revision") != candidate["expectation_revision"]
    ):
        raise ProvenanceError("GOLDEN_REVIEW_EVIDENCE_STALE_OR_WRONG_VERSION")
    if request.get("producer") == "ACTUAL_RESULT_GENERATOR":
        raise ProvenanceError("AUTOMATIC_RESULT_PROCESS_CANNOT_APPROVE_EXPECTATIONS")
    if request.get("status") != "PROPOSED":
        raise ProvenanceError("EDITABLE_METADATA_CANNOT_PROVE_APPROVAL")
    return {
        "proposal_content_integrity": "PASS",
        "financial_equivalence": "NOT_EVALUATED",
        "authenticated_human_approval": "NOT_VERIFIED",
        "activation_authorized": False,
        "proposal_digest": proposal_digest(proposal),
    }


def require_authorized_update(
    proposal: dict, *, root: Path = ROOT, github_pr: int | None = None
) -> dict:
    inspect_proposal(proposal, root=root)
    if github_pr is None:
        raise ProvenanceError("AUTHENTICATED_OWNER_ACK_REQUIRED: activation denied")
    result = verify_github_update(github_pr, root=root)
    if result.get("proposal_digest") != proposal_digest(proposal):
        raise ProvenanceError("GITHUB_REVIEW_CANDIDATE_MISMATCH")
    return result


# Trusted base-branch code defines this repository's existing GitHub boundary.
# None of these declarations authenticates a reviewer without fresh API reads.
GITHUB_REPOSITORY = "ivobog/swing-lens"
GITHUB_BRANCH = "main"
GITHUB_ACTIONS_APP_ID = 15368
GITHUB_WORKFLOW = ".github/workflows/golden-approval.yml"
PROPOSAL_PATH = "docs/testing/golden-update-proposal.json"
APPROVAL_CHECK = "Authenticated golden approval"
REQUIRED_CHECKS = (
    "Lint, Test, and Migration Gates",
    "Supported deterministic A04 certificate",
    "Core-release certification verdict",
)
PROTECTED_PATHS = (
    "CODEOWNERS",
    GITHUB_WORKFLOW,
    ".github/workflows/ci.yml",
    "scripts/qa/golden_provenance.py",
    "scripts/qa/release_evidence.py",
    "scripts/qa/check_certification_registry.py",
    "scripts/production_runtime_certification.py",
    "config/certification_registry.json",
    "config/ceri.yaml",
    "config/setup_lifecycle.yaml",
    "config/winner_probability.yaml",
    "docs/testing/golden_update_policy.md",
    PROPOSAL_PATH,
    "docs/testing/golden-provenance/a05b-r3/derive_core.py.txt",
    "tests/test_golden_update_governance.py",
    "tests/test_golden_github_approval.py",
    "tests/e2e/single_run_certification/golden_oracle.py",
    "tests/e2e/single_run_certification/golden_completion.py",
    "tests/e2e/single_run_certification/golden_assertions.py",
    "tests/e2e/single_run_certification/golden/v3/manifest.json",
)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ProvenanceError(code)


def _relative_data_path(name: str) -> str:
    _require(
        isinstance(name, str) and bool(re.fullmatch(r"[A-Za-z0-9_./-]+", name)),
        "GITHUB_UNSAFE_DATA_PATH",
    )
    _require(
        not name.startswith("/") and all(p not in {"", ".", ".."} for p in name.split("/")),
        "GITHUB_UNSAFE_DATA_PATH",
    )
    return name


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProvenanceError("GITHUB_API_REDIRECT_DENIED")


class _GitHubReader:
    """Authenticated REST reads and one fixed GraphQL query; never a mutation."""

    def __init__(self, token: str):
        _require(bool(token) and "\n" not in token, "GITHUB_READ_CREDENTIAL_REQUIRED")
        self._token = token
        self._opener = build_opener(_NoRedirect())

    def get(self, path: str):
        prefix = f"/repos/{GITHUB_REPOSITORY}"
        _require(path == prefix or path.startswith(prefix + "/"), "GITHUB_API_SCOPE_DENIED")
        request = Request(
            "https://api.github.com" + path,
            method="GET",
            headers={
                "Authorization": "Bearer " + self._token,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2026-03-10",
                "User-Agent": "SwingLens-A13-provenance-verifier",
            },
        )
        return self._read_request(request)

    def owner_acknowledgement(self, number: int) -> dict:
        # REST's PR author is not necessarily the actor who last edited its body.
        # This fixed query authenticates that actor without accepting caller queries.
        _require(type(number) is int and number > 0, "GITHUB_INVALID_PR_NUMBER")
        query = """query OwnerAcknowledgement($number: Int!) {
          repository(owner: "ivobog", name: "swing-lens") {
            nameWithOwner owner { __typename login id }
            pullRequest(number: $number) {
              id number body headRefOid baseRefOid lastEditedAt
              author { __typename login ... on User { id } }
              editor { __typename login ... on User { id } }
            }
          }
        }"""
        request = Request(
            "https://api.github.com/graphql",
            data=json.dumps({"query": query, "variables": {"number": number}}).encode(),
            method="POST",  # Read-only GraphQL queries use POST, not a mutation.
            headers={
                "Authorization": "Bearer " + self._token,
                "Content-Type": "application/json",
                "User-Agent": "SwingLens-A13-provenance-verifier",
            },
        )
        result = self._read_request(request)
        _require(
            isinstance(result, dict) and not result.get("errors"),
            "GITHUB_OWNER_IDENTITY_UNAVAILABLE",
        )
        repository = (result.get("data") or {}).get("repository")
        _require(isinstance(repository, dict), "GITHUB_OWNER_IDENTITY_UNAVAILABLE")
        return repository

    def _read_request(self, request: Request):
        try:
            with self._opener.open(request, timeout=30) as response:
                content = response.read(10 * 1024 * 1024 + 1)
            _require(len(content) <= 10 * 1024 * 1024, "GITHUB_API_RESPONSE_TOO_LARGE")
            return json.loads(content)
        except HTTPError as exc:
            # Do not log headers, credentials, response bodies or arbitrary URLs.
            raise ProvenanceError(f"GITHUB_AUTHORITY_UNAVAILABLE_HTTP_{exc.code}") from None
        except (URLError, OSError, ValueError):
            raise ProvenanceError("GITHUB_AUTHORITY_UNAVAILABLE") from None

    def pages(self, path: str, key: str | None = None) -> list:
        rows = []
        for page in range(1, 11):
            value = self.get(path + ("&" if "?" in path else "?") + f"per_page=100&page={page}")
            batch = value[key] if key else value
            _require(isinstance(batch, list), "GITHUB_API_LIST_REQUIRED")
            rows.extend(batch)
            if len(batch) < 100:
                if key and "total_count" in value:
                    _require(len(rows) == value["total_count"], "GITHUB_API_LIST_INCOMPLETE")
                return rows
        raise ProvenanceError("GITHUB_API_PAGINATION_LIMIT")

    def file(self, path: str, ref: str) -> bytes:
        _relative_data_path(path)
        _require(bool(re.fullmatch(r"[0-9a-f]{40}", ref)), "GITHUB_EXACT_COMMIT_REQUIRED")
        value = self.get(f"/repos/{GITHUB_REPOSITORY}/contents/{quote(path, safe='/')}?ref={ref}")
        blob_sha = None
        if value.get("type") == "file" and value.get("encoding") == "none":
            # Contents omits bodies above 1 MiB. Read the exact commit-bound Git
            # object instead; never follow download_url or an arbitrary host.
            blob_sha = value.get("sha", "")
            _require(
                bool(re.fullmatch(r"[0-9a-f]{40}", blob_sha))
                and type(value.get("size")) is int
                and 0 <= value["size"] <= 10 * 1024 * 1024,
                "GITHUB_INVALID_FILE_RESPONSE",
            )
            blob = self.get(f"/repos/{GITHUB_REPOSITORY}/git/blobs/{blob_sha}")
            _require(
                blob.get("sha") == blob_sha and blob.get("size") == value["size"]
                and blob.get("encoding") == "base64",
                "GITHUB_INVALID_FILE_RESPONSE",
            )
            value = {**value, "encoding": blob["encoding"], "content": blob.get("content")}
        _require(
            value.get("type") == "file" and value.get("encoding") == "base64",
            "GITHUB_REGULAR_DATA_FILE_REQUIRED",
        )
        _require(value.get("size", 10**10) <= 10 * 1024 * 1024, "GITHUB_API_RESPONSE_TOO_LARGE")
        try:
            content = base64.b64decode(value["content"], validate=False)
        except (ValueError, KeyError):
            raise ProvenanceError("GITHUB_INVALID_FILE_RESPONSE") from None
        _require(len(content) == value["size"], "GITHUB_INVALID_FILE_RESPONSE")
        if blob_sha is not None:
            identity = b"blob " + str(len(content)).encode() + b"\0" + content
            _require(
                hashlib.sha1(identity, usedforsecurity=False).hexdigest() == blob_sha,
                "GITHUB_INVALID_FILE_RESPONSE",
            )
        return content


def _codeowners(text: str, paths: set[str]) -> dict[str, set[str]]:
    """Conservative CODEOWNERS subset; unsupported syntax fails closed.

    GitHub's errors endpoint and actual write permission supply independent checks.
    Teams/email patterns require a separately reviewed expansion; never infer members.
    """
    owners = {path: set() for path in paths}
    for line in text.splitlines():
        fields = line.partition("#")[0].split()
        if not fields:
            continue
        pattern, *names = fields
        _require(
            not any(ch in pattern for ch in "![]\\?") and len(names) > 0,
            "GITHUB_CODEOWNERS_SYNTAX_UNSUPPORTED",
        )
        _require(
            all(re.fullmatch(r"@[A-Za-z0-9][A-Za-z0-9-]*", n) for n in names),
            "GITHUB_CODEOWNERS_INDIVIDUAL_OWNERS_REQUIRED",
        )
        for path in paths:
            normalized = pattern.lstrip("/")
            matches = (
                pattern == "*"
                or (normalized.endswith("/") and path.startswith(normalized))
                or fnmatch.fnmatchcase(path, normalized)
                or (
                    "/" not in normalized
                    and fnmatch.fnmatchcase(path.rsplit("/", 1)[-1], normalized)
                )
            )
            if matches:
                owners[path] = {n[1:].lower() for n in names}
    _require(all(owners.values()), "GITHUB_PROTECTED_PATH_WITHOUT_CODE_OWNER")
    return owners


def _protected_branch(api, branch: dict) -> tuple[set[str], str]:
    _require(branch.get("protected") is True, "GITHUB_TARGET_BRANCH_UNPROTECTED")
    protection = api.get(f"/repos/{GITHUB_REPOSITORY}/branches/{GITHUB_BRANCH}/protection")
    review = protection.get("required_pull_request_reviews")
    _require(
        protection.get("enforce_admins", {}).get("enabled") is True,
        "GITHUB_PROTECTION_ADMIN_BYPASS",
    )
    _require(
        isinstance(review, dict)
        and review.get("required_approving_review_count") == 0
        and not review.get("require_code_owner_reviews")
        and not review.get("require_last_push_approval"),
        "GITHUB_SOLO_OWNER_REVIEW_LOCKOUT",
    )
    if "bypass_pull_request_allowances" not in review:
        repository = api.get(f"/repos/{GITHUB_REPOSITORY}")
        owner = repository.get("owner") if isinstance(repository, dict) else None
        _require(
            isinstance(repository, dict)
            and repository.get("full_name") == GITHUB_REPOSITORY
            and isinstance(owner, dict)
            and owner.get("type") == "User"
            and owner.get("login") == GITHUB_REPOSITORY.split("/")[0],
            "GITHUB_PROTECTION_BYPASS_OR_UNKNOWN",
        )
        bypass_status = "NOT_APPLICABLE_PERSONAL_REPOSITORY"
    else:
        bypass = review["bypass_pull_request_allowances"]
        _require(
            isinstance(bypass, dict)
            and set(bypass) >= {"users", "teams", "apps"}
            and all(bypass[k] == [] for k in ("users", "teams", "apps")),
            "GITHUB_PROTECTION_BYPASS_OR_UNKNOWN",
        )
        bypass_status = "VERIFIED_EMPTY_BYPASS_LISTS"
    _require(
        protection.get("allow_force_pushes", {}).get("enabled") is False
        and protection.get("allow_deletions", {}).get("enabled") is False,
        "GITHUB_PROTECTION_DESTRUCTIVE_PUSH_ALLOWED",
    )
    checks = protection.get("required_status_checks") or {}
    rows = checks.get("checks") or []
    _require(
        checks.get("strict") is True
        and rows
        and all(row.get("app_id") == GITHUB_ACTIONS_APP_ID for row in rows),
        "GITHUB_REQUIRED_CHECK_SOURCE_UNTRUSTED",
    )
    names = {row.get("context") for row in rows}
    _require(
        set(REQUIRED_CHECKS) | {APPROVAL_CHECK} <= names, "GITHUB_REQUIRED_FINANCIAL_CHECK_MISSING"
    )
    return names - {APPROVAL_CHECK}, bypass_status


def _owner_acknowledgement(api, repo: dict, pr: dict, owners: dict, digest: str) -> str:
    owner = repo.get("owner") or {}
    login = GITHUB_REPOSITORY.split("/")[0]
    _require(
        owner.get("type") == "User"
        and owner.get("login", "").lower() == login
        and bool(owner.get("node_id")),
        "GITHUB_REPOSITORY_OWNER_REQUIRED",
    )
    live = api.owner_acknowledgement(pr["number"])
    body = live.get("pullRequest") or {}
    _require(
        live.get("nameWithOwner") == GITHUB_REPOSITORY
        and live.get("owner")
        == {"__typename": "User", "login": owner["login"], "id": owner["node_id"]},
        "GITHUB_REPOSITORY_OWNER_REQUIRED",
    )
    _require(
        body.get("id") == pr.get("node_id")
        and bool(pr.get("node_id"))
        and body.get("number") == pr["number"]
        and body.get("headRefOid") == pr["head"]["sha"]
        and body.get("baseRefOid") == pr["base"]["sha"]
        and body.get("body") == pr.get("body"),
        "GITHUB_OWNER_ACK_PR_CHANGED",
    )
    actor = body.get("editor")
    if actor is None:
        _require(body.get("lastEditedAt") is None, "GITHUB_OWNER_EDITOR_UNVERIFIED")
        actor = body.get("author") or {}
    _require(
        actor.get("__typename") == "User"
        and actor.get("id") == owner["node_id"]
        and actor.get("login", "").lower() == login,
        "GITHUB_OWNER_ACK_UNAUTHORIZED_ACTOR",
    )
    permission = api.get(f"/repos/{GITHUB_REPOSITORY}/collaborators/{login}/permission")
    _require(permission.get("permission") == "admin", "GITHUB_REPOSITORY_OWNER_REQUIRED")
    _require(
        all(login in allowed for allowed in owners.values()), "GITHUB_OWNER_CODEOWNERS_REQUIRED"
    )
    lines = [
        line
        for line in (body.get("body") or "").splitlines()
        if line.startswith("A13-SOLO-OWNER-ACK")
    ]
    _require(len(lines) == 1, "GITHUB_OWNER_ACK_REQUIRED")
    acknowledgement = re.fullmatch(
        r"A13-SOLO-OWNER-ACK head:([0-9a-f]{40}) sha256:([0-9a-f]{64})", lines[0]
    )
    _require(acknowledgement is not None, "GITHUB_OWNER_ACK_REQUIRED")
    _require(acknowledgement[1] == pr["head"]["sha"], "GITHUB_OWNER_ACK_STALE_HEAD")
    _require(acknowledgement[2] == digest, "GITHUB_OWNER_ACK_DIGEST_MISMATCH")
    return login


def _require_candidate_complete(proposal: dict, root: Path) -> None:
    """Retain the existing reviewed corpus/contract shape; never derive numbers.

    A different population or obligation schema needs a prior trusted-verifier
    review. Approval cannot make a missing row, section or contract complete.
    """
    from single_run_certification import golden_completion as completion
    from single_run_certification import golden_oracle as oracle

    candidate = proposal["candidate"]
    inputs = _read(root / candidate["input_manifest"])
    supplement = _read(root / candidate["completion_manifest"])
    baseline_inputs = oracle.load_golden(root / GOLDEN, verify_sources=False)
    reviewed = completion.load_completion(
        oracle.canonical_hash(baseline_inputs), root / GOLDEN / "oracle-completion"
    )
    _require(
        set(inputs) == set(baseline_inputs)
        and set(inputs.get("artifacts", {})) == set(baseline_inputs["artifacts"]),
        "GITHUB_INDEPENDENT_ORACLE_INCOMPLETE",
    )
    for field, manifest_path in (
        (inputs, candidate["input_manifest"]),
        (supplement, candidate["completion_manifest"]),
    ):
        for name, digest in field.get("artifacts", {}).items():
            _require(Path(name).name == name, "GITHUB_INDEPENDENT_ORACLE_INCOMPLETE")
            relative = (Path(manifest_path).parent / name).as_posix()
            _require(
                candidate["artifacts"].get(relative) == digest,
                "GITHUB_INDEPENDENT_ORACLE_INCOMPLETE",
            )
            _sealed(root, relative, digest)
    _require(
        supplement.get("contracts") == reviewed["contracts"]
        and supplement.get("required_fields") == reviewed["required_fields"]
        and supplement.get("unresolved") == [],
        "GITHUB_INDEPENDENT_ORACLE_INCOMPLETE",
    )
    domains, variants = {}, {}
    for old, new in candidate["expectation_artifacts"].items():
        if "/oracle-completion/" not in old:
            continue
        values = _read(root / new)
        domains.update({key: value for key, value in values.items() if key in completion.KEYS})
        variants.update(values.get("handoff_variants", {}))
    _require(
        set(domains) == set(reviewed["domains"]) and set(variants) == set(reviewed["variants"]),
        "GITHUB_INDEPENDENT_ORACLE_INCOMPLETE",
    )
    for section, expected in reviewed["domains"].items():
        rows = domains[section]
        _require(
            isinstance(rows, list) and len(rows) == len(expected),
            "GITHUB_INDEPENDENT_ORACLE_INCOMPLETE",
        )
        fields = set(reviewed["required_fields"].get(section, ()))
        _require(
            all(isinstance(row, dict) and fields <= row.keys() for row in rows),
            "GITHUB_INDEPENDENT_ORACLE_INCOMPLETE",
        )
        _require(
            sorted(completion.relationship_key(section, row) for row in rows)
            == sorted(completion.relationship_key(section, row) for row in expected),
            "GITHUB_INDEPENDENT_ORACLE_INCOMPLETE",
        )
    _require(
        len(completion.relationship_inventory({"completion": {"domains": domains}}))
        == len(completion.relationship_inventory({"completion": reviewed})),
        "GITHUB_INDEPENDENT_ORACLE_INCOMPLETE",
    )


def _successful_checks(api, pr: dict, required: set[str]) -> None:
    prefix = f"/repos/{GITHUB_REPOSITORY}"
    rows = []
    for sha in {pr["head"]["sha"], pr.get("merge_commit_sha")} - {None}:
        rows.extend(api.pages(prefix + f"/commits/{sha}/check-runs?filter=latest", "check_runs"))
    for name in required:
        matches = [r for r in rows if r.get("name") == name]
        _require(bool(matches), "GITHUB_REQUIRED_CI_MISSING_OR_FAILED")
        check = max(matches, key=lambda row: row["id"])
        _require(
            check.get("status") == "completed"
            and check.get("conclusion") == "success"
            and check.get("head_sha") in {pr["head"]["sha"], pr.get("merge_commit_sha")}
            and check.get("app", {}).get("id") == GITHUB_ACTIONS_APP_ID,
            "GITHUB_REQUIRED_CI_MISSING_OR_FAILED",
        )
        match = re.fullmatch(
            r"https://github\.com/"
            + re.escape(GITHUB_REPOSITORY)
            + r"/actions/runs/(\d+)(?:/job/\d+)?",
            check.get("details_url", ""),
        )
        _require(match is not None, "GITHUB_REQUIRED_CI_SOURCE_UNTRUSTED")
        run = api.get(prefix + "/actions/runs/" + match[1])
        _require(
            run.get("event") == "pull_request"
            and run.get("path") == ".github/workflows/ci.yml"
            and run.get("head_sha") in {pr["head"]["sha"], pr.get("merge_commit_sha")}
            and run.get("conclusion") == "success"
            and any(
                item.get("number") == pr["number"]
                and item.get("head", {}).get("sha") == pr["head"]["sha"]
                and item.get("base", {}).get("ref") == GITHUB_BRANCH
                for item in run.get("pull_requests", [])
            ),
            "GITHUB_REQUIRED_CI_SOURCE_UNTRUSTED",
        )


def _trusted_github_context(api, number: int, root: Path) -> dict:
    _require(
        os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("GITHUB_EVENT_NAME") == "pull_request_target"
        and os.environ.get("GITHUB_REPOSITORY") == GITHUB_REPOSITORY
        and os.environ.get("GITHUB_API_URL", "https://api.github.com") == "https://api.github.com",
        "GITHUB_TRUSTED_PR_CONTEXT_REQUIRED",
    )
    event = _read(Path(os.environ["GITHUB_EVENT_PATH"]))
    _require(
        event.get("number") == number
        and event.get("pull_request", {}).get("base", {}).get("repo", {}).get("full_name")
        == GITHUB_REPOSITORY,
        "GITHUB_TRUSTED_PR_CONTEXT_REQUIRED",
    )
    pr = api.get(f"/repos/{GITHUB_REPOSITORY}/pulls/{number}")
    checked_out = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    _require(
        checked_out == pr["base"]["sha"]
        and not subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=no"], cwd=root, text=True
        ).strip(),
        "GITHUB_TRUSTED_BASE_CHECKOUT_REQUIRED",
    )
    _require(
        event["pull_request"]["head"]["sha"] == pr["head"]["sha"],
        "GITHUB_PR_CHANGED_DURING_VERIFICATION",
    )
    own = api.get(f"/repos/{GITHUB_REPOSITORY}/actions/runs/" + os.environ["GITHUB_RUN_ID"])
    _require(
        own.get("event") == "pull_request_target"
        and own.get("path") == GITHUB_WORKFLOW
        # GitHub associates a target-workflow run with the PR head. The code
        # checkout and verifier bytes remain bound independently to the base.
        and own.get("head_sha") == pr["head"]["sha"]
        and any(
            item.get("number") == number
            and item.get("head", {}).get("sha") == pr["head"]["sha"]
            and item.get("base", {}).get("sha") == pr["base"]["sha"]
            and item.get("base", {}).get("ref") == GITHUB_BRANCH
            for item in own.get("pull_requests", [])
        )
        and own.get("status") == "in_progress",
        "GITHUB_TRUSTED_WORKFLOW_REQUIRED",
    )
    _require(
        api.file("scripts/qa/golden_provenance.py", checked_out) == Path(__file__).read_bytes(),
        "GITHUB_TRUSTED_VERIFIER_SOURCE_REQUIRED",
    )
    return pr


def _pr_identity(pr: dict) -> tuple:
    return (
        pr["number"],
        pr["head"]["sha"],
        pr["base"]["sha"],
        pr["base"]["ref"],
        pr["base"]["repo"]["full_name"],
        pr["user"]["login"],
        pr.get("merge_commit_sha"),
        pr.get("state"),
        pr.get("draft"),
        pr.get("merged"),
        pr.get("body"),
    )


def verify_github_update(number: int, *, root: Path = ROOT) -> dict:
    """Live authenticated reads from trusted base code; no offline receipt input.

    This verifies review, never activates files or replaces the A04/A16 checks.
    Unit transport doubles establish only local behavior, not real approval.
    """
    api = _GitHubReader(
        os.environ.get("GOLDEN_REVIEW_READ_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
    )
    pr = _trusted_github_context(api, number, root)
    prefix = f"/repos/{GITHUB_REPOSITORY}"
    repo = api.get(prefix)
    _require(
        repo.get("full_name") == GITHUB_REPOSITORY and repo.get("default_branch") == GITHUB_BRANCH,
        "GITHUB_CONFIGURED_REPOSITORY_OR_BRANCH_MISMATCH",
    )
    _require(
        pr.get("state") == "open"
        and not pr.get("draft")
        and not pr.get("merged")
        and pr["base"].get("ref") == GITHUB_BRANCH
        and pr["base"]["repo"]["full_name"] == GITHUB_REPOSITORY,
        "GITHUB_PROTECTED_PR_REQUIRED",
    )
    branch = api.get(prefix + "/branches/" + GITHUB_BRANCH)
    _require(branch["commit"]["sha"] == pr["base"]["sha"], "GITHUB_BASE_BRANCH_CHANGED")
    errors = api.get(prefix + "/codeowners/errors?ref=" + pr["base"]["sha"])
    _require(errors.get("errors") == [], "GITHUB_CODEOWNERS_ERRORS_OR_UNKNOWN")
    # Never silently use the root file when GitHub uses a higher-priority file.
    commit = api.get(prefix + "/git/commits/" + pr["base"]["sha"])
    base_tree = api.get(prefix + "/git/trees/" + commit["tree"]["sha"] + "?recursive=1")
    _require(base_tree.get("truncated") is False, "GITHUB_TREE_INCOMPLETE")
    _require(
        any(r["path"] == "CODEOWNERS" and r.get("mode") == "100644" for r in base_tree["tree"]),
        "GITHUB_REGULAR_CODEOWNERS_REQUIRED",
    )
    _require(
        not any(r["path"] == ".github/CODEOWNERS" for r in base_tree["tree"]),
        "GITHUB_CODEOWNERS_LOCATION_CHANGED",
    )
    files = api.pages(prefix + f"/pulls/{number}/files")
    _require(len(files) == pr["changed_files"], "GITHUB_CHANGED_FILE_LIST_INCOMPLETE")
    changed = {r["filename"] for r in files} | {
        r["previous_filename"] for r in files if "previous_filename" in r
    }
    governance_change = any(
        n.startswith("tests/e2e/single_run_certification/golden/")
        or n.startswith("tests/e2e/single_run_certification/golden_")
        or n == PROPOSAL_PATH
        for n in changed
    )
    if not governance_change:
        # Exercise the actual workflow credential before enabling protection.
        # These are read-only capability checks, never financial authorization.
        admin_read = api.get(prefix + "/actions/permissions/workflow")
        checks_read = api.get(prefix + "/commits/" + pr["head"]["sha"] + "/check-runs?per_page=1")
        _require(
            admin_read.get("default_workflow_permissions") in {"read", "write"}
            and isinstance(checks_read.get("check_runs"), list),
            "GITHUB_ENFORCEMENT_READ_CAPABILITY_UNVERIFIED",
        )
        bypass_status = "NOT_EVALUATED_UNPROTECTED"
        if branch.get("protected") is True:
            _, bypass_status = _protected_branch(api, branch)
        _require(
            _pr_identity(api.get(prefix + f"/pulls/{number}")) == _pr_identity(pr),
            "GITHUB_PR_CHANGED_DURING_VERIFICATION",
        )
        return {
            "owner_authorization": "NOT_APPLICABLE_NO_GOLDEN_CHANGE",
            "approval_model": "SOLE_OWNER",
            "activation_authorized": False,
            "source": "GITHUB_AUTHENTICATED_READ_ONLY",
            "bypass_verification": bypass_status,
            "enforcement_api_readiness": {
                "administration": "READ_CONFIRMED",
                "checks": "READ_CONFIRMED",
                "protection": "VERIFIED" if branch.get("protected") is True else "NOT_ENABLED",
            },
        }
    # Non-golden PRs can establish the check's real GitHub association before
    # protection is enabled. They never authorize a financial update. Financial
    # proposals still require every protected-branch predicate below.
    required, bypass_status = _protected_branch(api, branch)
    _require(PROPOSAL_PATH in changed, "GITHUB_GOLDEN_PROPOSAL_REQUIRED")
    for path in ("CODEOWNERS", GITHUB_WORKFLOW, ".github/workflows/ci.yml"):
        _require(
            api.file(path, pr["base"]["sha"]) == api.file(path, pr["head"]["sha"]),
            "GITHUB_TRUSTED_REVIEW_POLICY_CHANGED",
        )
    proposal = json.loads(api.file(PROPOSAL_PATH, pr["head"]["sha"]))
    source_revision = proposal["contract_change"]["source_revision"]
    _require(
        bool(re.fullmatch(r"[0-9a-f]{40}", source_revision)), "GITHUB_PROPOSAL_SOURCE_MISMATCH"
    )
    lineage = api.get(prefix + "/compare/" + source_revision + "..." + pr["head"]["sha"])
    # A proposal cannot contain the SHA of its own enclosing commit. Its source
    # commit must be an ancestor; the authenticated REVIEW binds the latest head.
    _require(
        lineage.get("status") in {"ahead", "identical"}
        and lineage.get("merge_base_commit", {}).get("sha") == source_revision,
        "GITHUB_PROPOSAL_SOURCE_MISMATCH",
    )
    baseline = verify_baseline(root)
    declared = dict(proposal["candidate"]["artifacts"])
    for mapping in (
        proposal["independent_derivation"]["artifacts"],
        proposal["contract_change"]["configuration_sha256"],
    ):
        for name, digest in mapping.items():
            _require(
                name not in declared or declared[name] == digest, "GITHUB_ARTIFACT_HASH_CONFLICT"
            )
            declared[name] = digest
    _require(0 < len(declared) <= 200, "GITHUB_CANDIDATE_SIZE_LIMIT")
    paths = set(PROTECTED_PATHS) | set(declared) | set(baseline["artifacts"])
    owners = _codeowners(api.file("CODEOWNERS", pr["base"]["sha"]).decode("utf-8"), paths)
    with tempfile.TemporaryDirectory(prefix="swinglens-a13-review-") as directory:
        staging = Path(directory)
        for name, expected in baseline["artifacts"].items():
            _require(
                hashlib.sha256(api.file(name, pr["head"]["sha"])).hexdigest() == expected,
                "GITHUB_HISTORICAL_GOLDEN_CHANGED",
            )
            target = staging / _relative_data_path(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / name, target)
        registry = staging / "config/certification_registry.json"
        registry.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / "config/certification_registry.json", registry)
        total = 0
        head_commit = api.get(prefix + "/git/commits/" + pr["head"]["sha"])
        tree = api.get(prefix + "/git/trees/" + head_commit["tree"]["sha"] + "?recursive=1")
        _require(tree.get("truncated") is False, "GITHUB_TREE_INCOMPLETE")
        regular = {
            r["path"] for r in tree["tree"] if r.get("mode") == "100644" and r.get("type") == "blob"
        }
        _require(PROPOSAL_PATH in regular, "GITHUB_REGULAR_PROPOSAL_REQUIRED")
        for name, expected in declared.items():
            _relative_data_path(name)
            _require(
                name in regular and Path(name).suffix in {".json", ".txt", ".md", ".csv", ".yaml"},
                "GITHUB_REGULAR_DATA_FILE_REQUIRED",
            )
            content = api.file(name, pr["head"]["sha"])
            total += len(content)
            _require(total <= 25 * 1024 * 1024, "GITHUB_CANDIDATE_SIZE_LIMIT")
            _require(
                hashlib.sha256(content).hexdigest() == expected,
                "GITHUB_REVIEWED_FILE_DIGEST_MISMATCH",
            )
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        inspect_proposal(proposal, root=staging)
        digest = proposal_digest(proposal)
        owner = _owner_acknowledgement(api, repo, pr, owners, digest)
        _successful_checks(api, pr, required)
        _require_candidate_complete(proposal, staging)
    # Re-read live identities/reviews/protection. Never accept a raced receipt.
    final = api.get(prefix + f"/pulls/{number}")
    _require(_pr_identity(final) == _pr_identity(pr), "GITHUB_PR_CHANGED_DURING_VERIFICATION")
    final_branch = api.get(prefix + "/branches/" + GITHUB_BRANCH)
    _require(
        final_branch["commit"]["sha"] == pr["base"]["sha"]
        and _protected_branch(api, final_branch) == (required, bypass_status),
        "GITHUB_PROTECTION_CHANGED_DURING_VERIFICATION",
    )
    _require(
        api.get(prefix).get("owner") == repo.get("owner")
        and _owner_acknowledgement(api, repo, final, owners, digest) == owner,
        "GITHUB_OWNER_ACK_CHANGED_DURING_VERIFICATION",
    )
    return {
        "owner_authorization": "SOLO_OWNER_AUTHORIZED",
        "approval_model": "SOLE_OWNER",
        "independent_human_review": "NOT_CLAIMED",
        "financial_equivalence": "NOT_EVALUATED",
        "review_authorized": True,
        "activation_authorized": False,
        "proposal_digest": digest,
        "input_version": proposal["candidate"]["input_version"],
        "expectation_revision": proposal["candidate"]["expectation_revision"],
        "head_sha": pr["head"]["sha"],
        "base_sha": pr["base"]["sha"],
        "owner": owner,
        "required_checks": sorted(required),
        "bypass_verification": bypass_status,
        "financial_certificate_authority": "ORIGINAL_A04_AND_A16_REMAIN_MANDATORY",
    }


def github_actions_check() -> None:
    """Entry for the existing provenance adapter from the trusted base workflow."""
    try:
        event = _read(Path(os.environ["GITHUB_EVENT_PATH"]))
        result = verify_github_update(event["number"])
    except (ProvenanceError, KeyError, OSError, ValueError, TypeError) as exc:
        code = str(exc) if isinstance(exc, ProvenanceError) else "GITHUB_VERIFICATION_INPUT_INVALID"
        print(
            json.dumps(
                {
                    "owner_authorization": "NOT_VERIFIED",
                    "approval_model": "SOLE_OWNER",
                    "activation_authorized": False,
                    "reason": code,
                }
            )
        )
        raise SystemExit(2) from None
    print(json.dumps(result, sort_keys=True))
