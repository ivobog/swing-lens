"""Explicit candidate financial validation; never selects an active golden.

The retained loaders and their pins remain unchanged. Only the supported A04
parent enables candidate data in its native child. This module cannot authorize
an owner, promote an oracle, or change NORMAL application configuration.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from single_run_certification import golden_completion as retained_completion
from single_run_certification import golden_oracle as retained
from single_run_certification.golden_oracle import (  # noqa: F401
    OracleMismatch,
    audit_fundamental_worksheet,
    canonical_hash,
    check_ownership,
    check_receipt,
    compare_records,
    decimal_equal,
)

ROOT = retained.ROOT
PROPOSAL = Path("docs/testing/golden-update-proposal.json")
CANDIDATE_ENV = "SWINGLENS_CERTIFICATION_CANDIDATE_VALIDATION"


def candidate_requested() -> bool:
    return os.environ.get(CANDIDATE_ENV) == "1"


def candidate_available() -> bool:
    return (ROOT / PROPOSAL).is_file()


def candidate_proposal() -> dict:
    from scripts.qa.golden_provenance import _require_candidate_complete, inspect_proposal

    proposal = json.loads((ROOT / PROPOSAL).read_text(encoding="utf-8"))
    inspect_proposal(proposal, root=ROOT)
    _require_candidate_complete(proposal, ROOT)
    return proposal


def candidate_binding() -> dict:
    from scripts.qa.golden_provenance import proposal_digest

    proposal = candidate_proposal()
    candidate = proposal["candidate"]
    return {
        "scope": "CANDIDATE_FINANCIAL_VALIDATION_ONLY",
        "proposal_digest": proposal_digest(proposal),
        "input_version": candidate["input_version"],
        "input_sha256": candidate["input_identity"],
        "oracle_sha256": candidate["completion_manifest_sha256"],
        "expectation_revision": candidate["expectation_revision"],
        "owner_authorization": "NOT_EVALUATED",
        "activation_authorized": False,
    }


def load_golden(*, candidate: bool | None = None) -> dict:
    if not (candidate_requested() if candidate is None else candidate):
        return retained.load_golden()
    declaration = candidate_proposal()["candidate"]
    manifest = json.loads((ROOT / declaration["input_manifest"]).read_text(encoding="utf-8"))
    from scripts.qa.golden_provenance import _sealed

    for path, digest in manifest["config_sources"].items():
        _sealed(ROOT, path, digest)
    return manifest


def artifact(name: str):
    if not candidate_requested():
        return retained.artifact(name)
    declaration = candidate_proposal()["candidate"]
    directory = (ROOT / declaration["input_manifest"]).parent
    manifest = load_golden(candidate=True)
    if name not in manifest["artifacts"] or Path(name).name != name:
        raise OracleMismatch("CANDIDATE_ARTIFACT_NOT_DECLARED")
    return json.loads((directory / name).read_text(encoding="utf-8"))


def expected_manifest(handoff: bool, *, candidate: bool | None = None) -> dict:
    selected = candidate_requested() if candidate is None else candidate
    if not selected:
        return retained.expected_manifest(handoff)
    if type(handoff) is not bool:
        raise OracleMismatch("HANDOFF_MUST_BE_BOOLEAN")
    declaration = candidate_proposal()["candidate"]
    directory = (ROOT / declaration["input_manifest"]).parent
    inputs = load_golden(candidate=True)

    def data(name):
        return json.loads((directory / name).read_text(encoding="utf-8"))

    # This proposal retains the exact runtime input population/flags. Expansion
    # requires the existing trusted-verifier review, not inferred defaults here.
    if data("runtime-settings.json") != retained.artifact("runtime-settings.json"):
        raise OracleMismatch("CANDIDATE_RUNTIME_SPECIMEN_CHANGE_REQUIRES_REVIEW")
    flags = retained.expected_manifest(handoff)["flags"]
    supplement = json.loads((ROOT / declaration["completion_manifest"]).read_text(encoding="utf-8"))
    completion_directory = (ROOT / declaration["completion_manifest"]).parent
    domains, variants = {}, {}
    for name in (
        "core-expected.json",
        "context-expected.json",
        "ceri-expected.json",
        "downstream-expected.json",
    ):
        values = json.loads((completion_directory / name).read_text(encoding="utf-8"))
        domains.update(
            {key: value for key, value in values.items() if key in retained_completion.KEYS}
        )
        variants.update(values.get("handoff_variants", {}))
    return {
        **copy.deepcopy(data("expected-results.json")),
        "input_manifest_sha256": canonical_hash(inputs),
        "business_time": inputs["business_time"],
        "source_availability": inputs.get("source_availability"),
        "flags": flags,
        "configuration_envelope_sha256": canonical_hash(
            {"configuration": data("configuration.json"), "flags": flags}
        ),
        "completion": {
            **supplement,
            "manifest_sha256": declaration["completion_manifest_sha256"],
            "domains": domains,
            "variants": variants,
        },
        "unresolved": supplement["unresolved"],
        "candidate_validation": candidate_binding(),
    }


def require_complete(manifest: dict) -> None:
    if "candidate_validation" not in manifest:
        retained.require_complete(manifest)
        return
    if manifest["candidate_validation"] != candidate_binding():
        raise OracleMismatch("CANDIDATE_BINDING_MISMATCH")
    mode = manifest["flags"]["setup_capture_handoff_enabled"]
    if manifest != expected_manifest(mode, candidate=True):
        raise OracleMismatch("CANDIDATE_EXPECTED_CONTENT_MISMATCH")
    if manifest["unresolved"]:
        raise OracleMismatch("CANDIDATE_INDEPENDENT_ORACLE_INCOMPLETE")


def eligible_winner_children(ticker: str, *, eligible: bool, exclusion_reason=None) -> dict:
    # The conditional population contract is unchanged and sealed in both corpora.
    if candidate_requested():
        if (
            artifact("expected-results.json")["conditional_obligations"]
            != retained.artifact("expected-results.json")["conditional_obligations"]
        ):
            raise OracleMismatch("CANDIDATE_WINNER_POPULATION_CHANGE_REQUIRES_REVIEW")
    return retained.eligible_winner_children(
        ticker, eligible=eligible, exclusion_reason=exclusion_reason
    )


# Native fixture modules import these only after the parent has selected the
# execution mode. Ordinary regressions and historical loaders retain v3.
if candidate_requested():
    _selection = candidate_proposal()["candidate"]
    VERSION = _selection["input_version"]
    DATA = (ROOT / _selection["input_manifest"]).parent
    COMPLETION = (ROOT / _selection["completion_manifest"]).parent
else:
    VERSION = retained.VERSION
    DATA = retained.DATA
    COMPLETION = retained_completion.COMPLETION
