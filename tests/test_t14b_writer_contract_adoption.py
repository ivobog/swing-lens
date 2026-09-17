"""Exact handoff and source-qualified adapter coverage; not native DB proof."""

import hashlib
import importlib
import inspect
import json
from pathlib import Path

import pytest

from app.services.domain_mutation import (
    DomainMutationContext,
    MutationEntryPointDescriptor,
    MutationEvidenceReference,
    MutationWriterDescriptor,
    validate_mutation_context,
)

DOCS = Path(__file__).resolve().parents[1] / "docs/remediation/calculation-lineage"
HANDOFF = json.loads((DOCS / "T14B_checked_handoff.json").read_text())


def native_owner(address):
    path, name = address.split(":")
    owner = importlib.import_module(path[:-3].replace("/", "."))
    for part in name.split("."):
        owner = getattr(owner, part)
    return owner


@pytest.mark.parametrize("family", HANDOFF["writer_families"], ids=lambda f: f["writer_family_id"])
def test_each_exact_assigned_writer_has_a_reviewable_native_boundary(family):
    owner = native_owner(family["canonical_owner"])
    fid = family["writer_family_id"]
    distinct = {
        "WF_UPLOAD_INITIALIZATION",
        "WF_TECHNICAL_FEATURE_CACHE",
        "WF_REGIME_DERIVED_DELETE",
    }
    if fid in distinct:
        source = inspect.getsource(owner)
        required = {
            "WF_UPLOAD_INITIALIZATION": "LEGACY_SERVING_ONLY",
            "WF_TECHNICAL_FEATURE_CACHE": "TECHNICAL_CACHE_KEY_FINGERPRINT_MISMATCH",
            "WF_REGIME_DERIVED_DELETE": "REGIME_CERTIFIED_ARTIFACT_DELETE_FORBIDDEN",
        }
        assert required[fid] in source
        return
    layers = []
    while hasattr(owner, "__wrapped__"):
        layers.append(inspect.getclosurevars(owner).nonlocals)
        owner = owner.__wrapped__
    assert layers, fid
    source = next((layer for layer in layers if "role" in layer and "domain" in layer), None)
    if source is not None:
        context = DomainMutationContext(
            domain=source["domain"],
            semantic_mode=source["mode"],
            entrypoint=MutationEntryPointDescriptor(
                family["canonical_owner"], "NATIVE_SOURCE_WRITER"
            ),
            writer=MutationWriterDescriptor(
                source["owner"], "phase5-source-writer-v1", source["domain"]
            ),
            reason="Test source role against its existing native domain policy",
            evidence=(
                MutationEvidenceReference(
                    source["role"], "native_request_manifest", "a" * 64, "a" * 64
                ),
            ),
        )
        assert validate_mutation_context(context).valid, fid
    else:
        assert any("writer" in layer or "mechanism" in layer for layer in layers), fid


def test_foundation_handoff_and_historical_reviews_are_unchanged():
    expected = {
        "T14A_phase5_handoff.json": (
            "8eed64f6a7e42bc9178c6c001d756c7e20ce9f68a6b9b2d3ff60cef58d32318d"
        ),
        "T14A_semantic_family_review.json": (
            "713699148421c210a6b59a16c8c1bdbe1cb4d8f1c9145a562c1bd06b2eec86a3"
        ),
        "T14A_semantic_review_source_pins.json": (
            "52290bac1d030e0df37e6a432ef01f2d1d8642cd75b501e2f4d63027168062cc"
        ),
    }
    for name, digest in expected.items():
        assert hashlib.sha256((DOCS / name).read_bytes()).hexdigest() == digest
    assert len(HANDOFF["writer_families"]) == 32
    assert len(HANDOFF["initiators"]) == 36
