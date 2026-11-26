from pathlib import Path

import pytest

from app.services.ceri.sec import processor_rollback
from app.services.ceri.sec.processor_signature import (
    SEC_SCOPE_EVIDENCE_V1,
    SEC_SCOPE_EVIDENCE_V2,
    SEC_SCOPE_EVIDENCE_VERSION,
    requires_prepared_scope,
    scope_evidence_contract,
    sec_guidance_processor_signature,
)


def test_historical_signatures_retain_exact_preparation_contracts():
    v1 = "sec-guidance:755df092d79e7a53"
    v2 = "sec-guidance:66a29b585c469f21"
    current = sec_guidance_processor_signature()
    assert scope_evidence_contract(v1) == SEC_SCOPE_EVIDENCE_V1
    assert scope_evidence_contract(v2) == SEC_SCOPE_EVIDENCE_V2
    assert scope_evidence_contract(current) == SEC_SCOPE_EVIDENCE_VERSION
    assert not requires_prepared_scope(v1)
    assert requires_prepared_scope(v2)
    assert requires_prepared_scope(current)
    assert current != v2


def test_release_reopens_exact_fresh_archive_instead_of_ordinary_cache(tmp_path, monkeypatch):
    filing = {
        "cik": "0001496443",
        "accession": "0001683168-26-006021",
        "filename": "paysign_i10q-063026.htm",
    }
    observed = "2026-11-26T12:00:01+00:00"
    fingerprint = "9" * 64
    custody = {"wire_sha256": fingerprint, "possessed_at": observed}
    result = {
        "filing": filing,
        "body": {"body_sha256": fingerprint, "possessed_at": observed},
        "fresh_custody": custody,
    }
    calls: list[tuple[Path, dict, dict]] = []

    def reopen(root, identity, proof):
        calls.append((root, identity, proof))
        return "reverified content"

    monkeypatch.setattr(processor_rollback, "load_fresh_body", reopen)
    signature = sec_guidance_processor_signature()
    processor_rollback._reverify_prepared_body(result, tmp_path, None, signature)
    assert calls == [(tmp_path, filing, custody)]

    for bad_result, bad_signature in (
        (result, "sec-guidance:66a29b585c469f21"),
        ({**result, "body": {**result["body"], "body_sha256": "8" * 64}}, signature),
        ({**result, "body": {**result["body"], "possessed_at": "2026-11-26T11:00:01Z"}}, signature),
    ):
        with pytest.raises(ValueError, match="body custody unavailable"):
            processor_rollback._reverify_prepared_body(bad_result, tmp_path, None, bad_signature)
    assert len(calls) == 1

    def tampered(*_args):
        raise ValueError("changed archive bytes")

    monkeypatch.setattr(processor_rollback, "load_fresh_body", tampered)
    with pytest.raises(ValueError, match="body custody unavailable"):
        processor_rollback._reverify_prepared_body(result, tmp_path, None, signature)
