from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from types import SimpleNamespace

from app.services.ceri.pit_eligibility import source_record_historical_eligibility_at
from app.services.ceri.preparation_visibility import PREPARATION_SOURCE
from app.services.ceri.sec.preparation_evidence import archive_exclusion_proof, examine_attribution
from app.services.ceri.sec.preparation_sources import PreparationSourceOwner
from app.services.ceri.sec.processor_signature import (
    SEC_SCOPE_EVIDENCE_V1,
    SEC_SCOPE_EVIDENCE_VERSION,
    scope_evidence_contract,
    sec_guidance_processor_signature,
)

FILING = {
    "cik": "0001090872", "accession": "0001090872-26-000064",
    "filename": "a-20260731.htm", "form": "10-Q", "filing_date": "2026-09-01",
    "accepted_at": "2026-09-01T20:11:15.000000Z",
}


def test_separate_preparation_identity_is_bound_to_exact_generation():
    owner = PreparationSourceOwner("f" * 64, sec_guidance_processor_signature(), 1, "scope", 1)
    canonical = f'{FILING["accession"]}:{FILING["accession"]}/{FILING["filename"]}#paragraph-1'
    custody = {"body_sha256": "b" * 64, "possessed_at": "2026-10-02T00:12:24.301528Z"}
    identity = owner.source_identity(FILING, custody, canonical)
    assert identity != canonical
    assert owner.source_identity(FILING, custody, canonical) == identity
    assert replace(owner, generation=2).source_identity(FILING, custody, canonical) != identity
    assert replace(owner, run_id=2).source_identity(FILING, custody, canonical) != identity
    changed_custody = {**custody, "body_sha256": "c" * 64}
    assert owner.source_identity(FILING, changed_custody, canonical) != identity
    assert source_record_historical_eligibility_at(SimpleNamespace(
        dataset=PREPARATION_SOURCE, retrieved_at=datetime(2026, 10, 2, tzinfo=UTC),
    )) is None
    assert scope_evidence_contract("sec-guidance:755df092d79e7a53") == SEC_SCOPE_EVIDENCE_V1
    current_contract = scope_evidence_contract(sec_guidance_processor_signature())
    assert current_contract == SEC_SCOPE_EVIDENCE_VERSION


def test_refusal_requires_verified_archive_wire_custody_and_exact_body():
    body = "<html><body>No attributable financial disclosure.</body></html>"
    document = SimpleNamespace(cik=FILING["cik"], accession_number=FILING["accession"])
    refusal = examine_attribution(document, body)
    assert refusal == "SEC_FINANCIAL_ISSUER_UNPROVED"
    fingerprint = sha256(body.encode()).hexdigest()
    recorded = {"body_sha256": fingerprint, "possessed_at": "2026-10-02T00:12:24.301528Z"}
    proof = {
        "filing": FILING,
        "archive_url": "https://www.sec.gov/Archives/edgar/data/"
        "1090872/000109087226000064/a-20260731.htm",
        "http_status": 200, "redirect_count": 0, "wire_sha256": fingerprint,
        "wire_bytes": len(body.encode()), "received_at": "2026-10-02T00:12:23Z",
        "archive_receipt_sha256": "a" * 64, "submissions_sha256": "b" * 64,
    }
    assert archive_exclusion_proof(FILING, recorded, body, proof, refusal) == proof
    assert archive_exclusion_proof(FILING, recorded, body, None, refusal) is None
    assert archive_exclusion_proof(FILING, recorded, body + " ", proof, refusal) is None
    assert archive_exclusion_proof(
        FILING, recorded, body, {**proof, "redirect_count": 1}, refusal,
    ) is None
    assert archive_exclusion_proof(
        FILING, recorded, body, proof, "SEC_PROJECTED_SOURCE_QUARANTINED",
    ) is None
