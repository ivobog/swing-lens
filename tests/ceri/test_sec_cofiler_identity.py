from __future__ import annotations

import gzip
import hashlib
import json
import sys
from pathlib import Path

import pytest

from scripts.ops import validate_sec_cofiler as validator

DATA = Path(__file__).with_name("fixtures") / "pbf-000043-sec-primary.body.gz"
PBF = validator.APPROVED["0001534504-26-000043"]
REAL_SHA256 = "e75a70173a024f5f691f2edd61736a2a0f77e3ec796fde34ad03ec3312772da8"


@pytest.fixture
def retained_body():
    raw = gzip.decompress(DATA.read_bytes())
    assert hashlib.sha256(raw).hexdigest() == REAL_SHA256
    return raw


def test_real_quarantined_filing_binds_cofilers_and_blocks_financial_attribution(retained_body):
    result = validator.verify_response(
        PBF, retained_body, archive_url=PBF.archive_url, wire_sha256=REAL_SHA256
    )
    assert result["registrant_contexts"] == {
        "c-1": "0001534504",
        "c-2": "0001566011",
        "c-3": "0001566011",
    }
    assert result["wire_sha256"] == REAL_SHA256
    assert result["document_identity"] == "VERIFIED"
    assert result["financial_attribution"] == "BLOCKED"
    assert result["production_cache_admission"] == "BLOCKED"
    assert "guidance" not in result and "financial_records" not in result


@pytest.mark.parametrize(
    ("original", "changed"),
    [
        (b">0001566011<", b">0009999999<"),
        (b'name="dei:EntityCentralIndexKey" id="f-25"', b'name="dei:UnrelatedFact" id="f-25"'),
        (b'name="dei:EntityRegistrantName" id="f-4"', b'name="dei:UnrelatedFact" id="f-4"'),
        (b'<xbrli:context id="c-2">', b'<xbrli:context id="c-3">'),
        (
            b'contextRef="c-3" name="dei:EntityCentralIndexKey"',
            b'contextRef="c-1" name="dei:EntityCentralIndexKey"',
        ),
        (
            b'contextRef="c-3" name="dei:EntityCentralIndexKey"',
            b'contextRef="missing" name="dei:EntityCentralIndexKey"',
        ),
        (b'dimension="dei:LegalEntityAxis"', b'dimension="dei:StatementBusinessSegmentsAxis"'),
        (b"pbf:PBFHoldingMember", b"pbf:UnrelatedHolderMember"),
        (b'name="dei:DocumentType" id="f-1">8-K', b'name="dei:DocumentType" id="f-1">10-K'),
        (
            b'xmlns:dei="http://xbrl.sec.gov/dei/2025"',
            b'xmlns:dei="https://example.invalid/dei/2025"',
        ),
        (b">2023-11-02<", b">2026-10-27<"),
    ],
)
def test_cik_scope_and_malformed_or_conflicting_roles_fail_closed(retained_body, original, changed):
    assert original in retained_body
    changed_body = retained_body.replace(
        original,
        changed,
        -1 if original == b">2023-11-02<" else 1,
    )
    with pytest.raises(validator.FilingIdentityError):
        validator._registrant_contexts(changed_body, PBF)


@pytest.mark.parametrize(
    ("cik", "accession", "filename", "form"),
    [
        ("0001534504", "0001534504-26-000044", "pbf-20261027.htm", "8-K"),
        ("0001534504", "0001534504-26-000039", "pbf-20261027.htm", "8-K"),
        ("0001534504", PBF.accession, "pbf-20261029.htm", "8-K"),
        ("0001566011", PBF.accession, PBF.filename, "8-K"),
        ("0001534504", PBF.accession, PBF.filename, "10-K"),
    ],
)
def test_accession_filename_issuer_and_form_are_exact(cik, accession, filename, form):
    with pytest.raises(validator.FilingIdentityError, match="outside the reviewed scope"):
        validator.reviewed_filing(cik, accession, filename, form)


def test_another_body_or_archive_path_cannot_borrow_retained_provenance(retained_body):
    with pytest.raises(validator.FilingIdentityError, match="primary archive identity"):
        validator.verify_response(
            PBF, retained_body, archive_url=PBF.index_url, wire_sha256=REAL_SHA256
        )
    edited = retained_body + b"\n"
    with pytest.raises(validator.FilingIdentityError, match="differ from the acquisition receipt"):
        validator.verify_response(PBF, edited, archive_url=PBF.archive_url, wire_sha256=REAL_SHA256)
    with pytest.raises(validator.FilingIdentityError, match="original quarantined body"):
        validator.verify_response(
            PBF, edited, archive_url=PBF.archive_url, wire_sha256=hashlib.sha256(edited).hexdigest()
        )
    other = validator.APPROVED["0001534504-26-000039"]
    with pytest.raises(validator.FilingIdentityError, match="authenticated retained wire response"):
        validator.verify_response(
            other, retained_body, archive_url=other.archive_url, wire_sha256=REAL_SHA256
        )


def test_cli_binds_frozen_selection_and_never_writes_cache_or_guidance(
    retained_body, tmp_path, monkeypatch, capsys
):
    selected = {
        "ticker": PBF.ticker,
        "cik": PBF.cik,
        "document_name": PBF.filename,
        "form": "8-K",
        "filing_date": PBF.filing_date,
        "sec_filing_accepted_at_utc": PBF.accepted_at,
        "accession_number": PBF.accession,
    }
    manifest = tmp_path / "manifest.json"
    evidence = {
        "issuer_results": [{"authoritative_manifest": True, "selected_documents": [selected]}]
    }
    raw_manifest = json.dumps(evidence).encode()
    manifest.write_bytes(raw_manifest)
    monkeypatch.setattr(validator, "DISCOVERY_SHA256", hashlib.sha256(raw_manifest).hexdigest())
    body = tmp_path / "document.body"
    body.write_bytes(retained_body)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_sec_cofiler",
            "--cik",
            PBF.cik,
            "--accession",
            PBF.accession,
            "--filename",
            PBF.filename,
            "--form",
            "8-K",
            "--archive-url",
            PBF.archive_url,
            "--manifest",
            str(manifest),
            "--body",
            str(body),
            "--wire-sha256",
            REAL_SHA256,
        ],
    )
    validator.main()
    proof = json.loads(capsys.readouterr().out)
    assert proof["financial_attribution"] == "BLOCKED"
    assert proof["production_cache_admission"] == "BLOCKED"
    assert set(p.name for p in tmp_path.iterdir()) == {"manifest.json", "document.body"}
    manifest.write_bytes(raw_manifest + b" ")
    with pytest.raises(SystemExit, match="2"):
        validator.main()
    assert "Discovery manifest digest changed" in capsys.readouterr().err
