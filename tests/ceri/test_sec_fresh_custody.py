"""Offline controls for the independent SEC acquisition and preparation attestations."""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.ceri.sec import processor_rollback
from app.services.ceri.sec.fresh_custody import (
    FRESH_DIRECTORY,
    FRESH_JOURNAL,
    archive_url,
    bundle_reference,
    digest,
    load_fresh_body,
)
from app.services.ceri.sec.processor_signature import sec_guidance_processor_signature
from scripts.ops import sec_fresh_custody as operator

FILING = {
    "cik": "0001090872", "accession": "0001193125-26-470278",
    "filename": "a-20261116.htm", "form": "8-K", "filing_date": "2026-11-20",
    "accepted_at": "2026-11-20T21:15:14.000000Z",
}
KEY = (FILING["cik"], FILING["accession"], FILING["filename"])
BODY = b"<html><body>SECURITIES AND EXCHANGE COMMISSION, Form 8-K</body></html>"
SUBMISSIONS = "1" * 64
MANIFEST = "2" * 64
ACQUISITION = "3" * 64


def _packed(data):
    return Canonical.dumps(data).encode("utf-8")


@pytest.fixture
def isolated_signers(tmp_path, monkeypatch):
    executable = shutil.which("ssh-keygen")
    assert executable, "The protected signature regression requires OpenSSH"
    owner_key = tmp_path / "owner"
    attestor_key = tmp_path / "attestor"
    for key in (owner_key, attestor_key):
        subprocess.run(
            [executable, "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
            check=True, capture_output=True,
        )
    signers = tmp_path / "signers"
    owner_pub = owner_key.with_suffix(".pub").read_text().strip()
    attestor_pub = attestor_key.with_suffix(".pub").read_text().strip()
    signers.write_text(
        f'owner namespaces="{operator.ACQUISITION_NAMESPACE}" {owner_pub}\n'
        f'{operator.ATTESTOR_PRINCIPAL} namespaces="{operator.ATTESTATION_NAMESPACE}" '
        f'{attestor_pub}\n',
        encoding="utf-8",
    )
    auth = operator.Authenticator(
        operator.Path(executable), signers, "owner",
        operator._attestor_fingerprint(signers, "owner"),
    )
    monkeypatch.setattr(operator, "_authenticator", lambda **_: auth)
    monkeypatch.setattr(operator, "_protected_paths", lambda *_: None)

    def sign(key, namespace, content):
        return subprocess.run(
            [executable, "-Y", "sign", "-f", str(key), "-n", namespace],
            check=True, capture_output=True, input=content,
        ).stdout

    return auth, owner_key, attestor_key, sign


@pytest.fixture
def signed_bundle(tmp_path, isolated_signers):
    auth, owner_key, attestor_key, sign = isolated_signers
    root = (tmp_path.parent / f"fresh-{digest(str(tmp_path).encode())[:8]}").resolve()
    root.mkdir()

    def make(*, receipt_overrides=None, no_independent_signature=False,
             approval_overrides=None, body=BODY):
        approved = {
            "operation": "sec-fresh-custody-v1", "acquisition_id": ACQUISITION,
            "runtime_git": "4" * 40, "archive_root": str(root), "operator": "sid:S-1-5-18",
            "manifest_sha256": MANIFEST, "user_agent_sha256": "5" * 64,
            "attestor_key_sha256": auth.attestor_fingerprint, "max_get_attempts": 1,
            "max_requests_per_second": 2, "max_redirects": 0, "max_retries": 0,
            "starts_at": "2026-11-21T16:00:00.000000Z", "cutoff_at": "2026-11-21T16:04:00.000000Z",
            "filings": [dict(zip(("cik", "accession", "filename"), KEY, strict=True))],
            **(approval_overrides or {}),
        }
        receipt = {
            "filing": FILING, "submissions_sha256": SUBMISSIONS,
            "archive_url": archive_url(FILING), "http_status": 200, "redirect_count": 0,
            "wire_sha256": digest(body), "wire_bytes": len(body),
            "received_at": "2026-11-21T16:00:01.000000Z",
            "possessed_at": "2026-11-21T16:00:02.000000Z",
            **(receipt_overrides or {}),
        }
        approval_bytes = _packed(approved)
        approval_sha = digest(approval_bytes)
        journal_bytes = b"\n".join([
            _packed({
                "version": FRESH_JOURNAL, "approval_sha256": approval_sha,
                "captured_at": "2026-11-21T16:00:03.000000Z",
                "attempted": 1, "verified": 1, "stopped": None,
            }),
            _packed(receipt), b"",
        ])
        journal_sha = digest(journal_bytes)
        parent = root / bundle_reference(approval_sha, journal_sha)
        (parent / "bodies").mkdir(parents=True)
        (parent / "approval.json").write_bytes(approval_bytes)
        (parent / "journal.jsonl").write_bytes(journal_bytes)
        (parent / "approval.json.sig").write_bytes(
            sign(owner_key, operator.ACQUISITION_NAMESPACE, approval_bytes),
        )
        signer = owner_key if no_independent_signature else attestor_key
        (parent / "journal.jsonl.sig").write_bytes(
            sign(signer, operator.ATTESTATION_NAMESPACE, journal_bytes),
        )
        (parent / "bodies" / f"{digest(body)}.body").write_bytes(body)
        index = {
            "cik": KEY[0], "accession": KEY[1], "primary": KEY[2],
            "fresh_acquisition_sha256": approval_sha, "fresh_journal_sha256": journal_sha,
        }
        return index, parent

    return root, make


def _verify(root, index):
    return operator.load_fresh_proofs(
        [index], filings={KEY: (FILING, SUBMISSIONS)}, archive_root=root, manifest_sha256=MANIFEST,
    )


def test_independent_signatures_and_exact_later_body_are_required(signed_bundle):
    root, make = signed_bundle
    index, bundle = make()
    proof = _verify(root, index)[KEY]
    assert proof["approval_signature_sha256"] == digest(
        (bundle / "approval.json.sig").read_bytes(),
    )
    assert proof["journal_signature_sha256"] == digest(
        (bundle / "journal.jsonl.sig").read_bytes(),
    )
    assert proof["filing"] == FILING
    assert proof["possessed_at"] == "2026-11-21T16:00:02.000000Z"
    assert load_fresh_body(root, FILING, proof) == BODY.decode("utf-8")
    assert not (root / "ceri").exists()
    assert not (root / "sec-documents").exists()
    with pytest.raises(ValueError, match="SEC_FRESH_RECEIPT_INVALID"):
        load_fresh_body(root, FILING, {**proof, "body_reference": "elsewhere.body"})


def test_owner_cannot_attest_the_origin_with_its_own_key(signed_bundle):
    root, make = signed_bundle
    index, _ = make(no_independent_signature=True)
    with pytest.raises(ValueError, match="SEC_FRESH_OWNER_OR_ATTESTOR_SIGNATURE_INVALID"):
        _verify(root, index)


@pytest.mark.parametrize(
    ("receipt_override", "error"),
    [
        ({"received_at": "2026-11-21T15:59:59.000000Z"}, "SEC_FRESH_RECEIPT_INVALID"),
        ({"possessed_at": "2026-11-21T16:04:00.000000Z"}, "SEC_FRESH_RECEIPT_INVALID"),
        ({"submissions_sha256": "9" * 64}, "SEC_FRESH_RECEIPT_SUBMISSIONS_SCOPE_MISMATCH"),
        ({"redirect_count": 1}, "SEC_FRESH_RECEIPT_INVALID"),
    ],
)
def test_authenticated_journal_cannot_override_time_or_scope(
    signed_bundle, receipt_override, error,
):
    root, make = signed_bundle
    index, _ = make(receipt_overrides=receipt_override)
    with pytest.raises(ValueError, match=error):
        _verify(root, index)


def test_lifecycle_revalidation_rejects_mutated_body_and_journal(signed_bundle):
    root, make = signed_bundle
    index, parent = make()
    proof = _verify(root, index)[KEY]
    body_file = parent / "bodies" / f'{proof["wire_sha256"]}.body'
    body_file.write_bytes(BODY.replace(b"Form 8-K", b"Form 6-K"))
    with pytest.raises(ValueError, match="SEC_FRESH_ARCHIVE_DIGEST_OR_SIZE_INVALID"):
        load_fresh_body(root, FILING, proof)
    body_file.write_bytes(BODY)
    journal = parent / "journal.jsonl"
    journal.write_bytes(journal.read_bytes().replace(b"16:00:02", b"16:00:01"))
    with pytest.raises(ValueError, match="SEC_FRESH_ARCHIVE_DIGEST_OR_SIZE_INVALID"):
        load_fresh_body(root, FILING, proof)


@pytest.mark.parametrize("name", ["approval.json.sig", "journal.jsonl.sig"])
@pytest.mark.parametrize("delete", [False, True])
def test_lifecycle_revalidation_requires_both_exact_detached_signatures(
    signed_bundle, name, delete,
):
    root, make = signed_bundle
    index, bundle = make()
    proof = _verify(root, index)[KEY]
    result = {
        "filing": FILING,
        "body": {"body_sha256": proof["wire_sha256"], "possessed_at": proof["possessed_at"]},
        "fresh_custody": proof,
    }
    signature = sec_guidance_processor_signature()
    processor_rollback._reverify_prepared_body(result, root, None, signature)
    path = bundle / name
    if delete:
        path.unlink()
    else:
        path.write_bytes(b"Different signature bytes")
    error = "SEC_FRESH_ARCHIVE_PATH_INVALID" if delete else "SEC_FRESH_RECEIPT_INVALID"
    with pytest.raises(ValueError, match=error):
        load_fresh_body(root, FILING, proof)
    with pytest.raises(ValueError, match="SEC preparation body custody unavailable"):
        processor_rollback._reverify_prepared_body(result, root, None, signature)


def test_intake_cannot_seal_signature_bytes_changed_during_verification(
    signed_bundle, isolated_signers, monkeypatch,
):
    root, make = signed_bundle
    index, _ = make()
    auth, *_ = isolated_signers
    verify = auth.verify

    def changed_after_verify(content, path, *, owner):
        verify(content, path, owner=owner)
        path.write_bytes(b"Different signature bytes")

    monkeypatch.setattr(
        operator, "_authenticator", lambda **_: SimpleNamespace(
            verify=changed_after_verify, attestor_fingerprint=auth.attestor_fingerprint,
        ),
    )
    error = "SEC_FRESH_DETACHED_SIGNATURE_CHANGED_DURING_VERIFICATION"
    with pytest.raises(ValueError, match=error):
        _verify(root, index)


def test_capture_slot_prevents_replay_with_new_signature(tmp_path, monkeypatch):
    monkeypatch.setattr(operator, "_protected_paths", lambda *_: None)
    vault = tmp_path.resolve() / FRESH_DIRECTORY
    (vault / "used").mkdir(parents=True)
    approval = SimpleNamespace(acquisition_id=ACQUISITION)
    with operator._capture_slot(vault, approval, MANIFEST):
        with pytest.raises(FileExistsError), operator._capture_slot(vault, approval, SUBMISSIONS):
            pytest.fail("Concurrent capture was admitted")
    with pytest.raises(FileExistsError), operator._capture_slot(vault, approval, SUBMISSIONS):
        pytest.fail("Consumed acquisition was admitted")


def test_fixed_https_transport_never_follows_redirect(monkeypatch):
    calls = []

    class RedirectConnection:
        def __init__(self, host, port, **kwargs):
            calls.append((host, port))

        def request(self, method, path, *, headers):
            calls.append((method, path, headers["User-Agent"]))

        def getresponse(self):
            return self

        def __enter__(self):
            return SimpleNamespace(status=302)

        def __exit__(self, *_):
            pass

        def close(self):
            pass

    monkeypatch.setattr(operator.http.client, "HTTPSConnection", RedirectConnection)
    now = datetime.now(UTC)
    approval = SimpleNamespace(
        starts_at=now - operator.timedelta(minutes=1),
        cutoff_at=now + operator.timedelta(minutes=1),
    )
    with pytest.raises(ValueError, match="SEC_FRESH_HTTP_STATUS_302"):
        operator._download(FILING, approval, "test fixture")
    assert calls == [
        ("www.sec.gov", 443),
        ("GET", "/Archives/edgar/data/1090872/000119312526470278/a-20261116.htm", "test fixture"),
    ]



@pytest.mark.parametrize("identity_failure", [False, True])
def test_capture_retains_authenticated_terminal_result_when_every_request_fails(
    tmp_path, monkeypatch, isolated_signers, identity_failure,
):
    from scripts.ops import sec_cache_preparation

    auth, owner_key, attestor_key, sign = isolated_signers
    root = (tmp_path.parent / f"fresh-{digest(str(tmp_path).encode())[:8]}").resolve()
    (root / FRESH_DIRECTORY / "used").mkdir(parents=True)
    user_agent = "Test fixture sec-test@swinglens.invalid"
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(b"{}")
    now = datetime.now(UTC)
    approval = _packed({
        "operation": "sec-fresh-custody-v1", "acquisition_id": ACQUISITION,
        "runtime_git": "4" * 40, "archive_root": str(root), "operator": "sid:S-1-5-18",
        "manifest_sha256": digest(manifest.read_bytes()),
        "user_agent_sha256": digest(user_agent.encode("utf-8")),
        "attestor_key_sha256": auth.attestor_fingerprint, "max_get_attempts": 1,
        "max_requests_per_second": 2, "max_redirects": 0, "max_retries": 0,
        "starts_at": now - operator.timedelta(minutes=1),
        "cutoff_at": now + operator.timedelta(minutes=1),
        "filings": [dict(zip(("cik", "accession", "filename"), KEY, strict=True))],
    })
    approval_path = tmp_path / "approval.json"
    approval_path.write_bytes(approval)
    signature = tmp_path / "approval.json.sig"
    signature.write_bytes(sign(owner_key, operator.ACQUISITION_NAMESPACE, approval))
    monkeypatch.setattr(operator, "_verify_capture_runtime", lambda: auth.executable)
    monkeypatch.setattr(operator, "_capture_git_identity", lambda _: "4" * 40)
    monkeypatch.setattr(operator, "_principal", lambda: "sid:S-1-5-18")
    monkeypatch.setattr(
        operator, "get_settings", lambda: SimpleNamespace(sec_user_agent=user_agent),
    )
    monkeypatch.setattr(operator, "_private_attestor_key", lambda _: attestor_key)
    monkeypatch.setattr(
        sec_cache_preparation, "_scope_from_manifest",
        lambda *_: (None, {}, {}, {KEY: (FILING, SUBMISSIONS)}),
    )
    calls = []
    invalid = b"<html><body>Returned content lacks the expected SEC form.</body></html>"

    def offline_failure(filing, approval, agent):
        calls.append((filing, agent))
        if identity_failure:
            return invalid, datetime.now(UTC)
        raise ValueError("SEC_FRESH_HTTP_STATUS_429")

    monkeypatch.setattr(operator, "_download", offline_failure)
    result = operator._capture(approval_path, signature, manifest)
    assert calls == [(FILING, user_agent)]
    assert result["attempted"] == 1 and result["verified"] == 0
    assert result["stopped"] == (
        "SEC_FRESH_PRIMARY_BODY_IDENTITY_INVALID" if identity_failure
        else "SEC_FRESH_HTTP_STATUS_429"
    )
    assert result["quarantined"] == int(identity_failure)
    ref = bundle_reference(
        result["acquisition_approval_sha256"], result["acquisition_journal_sha256"],
    )
    terminal = root / ref
    assert (terminal / "capture-result.json").read_bytes() == _packed(result)
    assert (terminal / "attempt-001.json").is_file()
    auth.verify(
        (terminal / "journal.jsonl").read_bytes(), terminal / "journal.jsonl.sig", owner=False,
    )
    if identity_failure:
        header = operator.read_json((terminal / "journal.jsonl").read_bytes().splitlines()[0])
        assert len(header["quarantined"]) == 1
        evidence = header["quarantined"][0]
        assert evidence["wire_sha256"] == digest(invalid)
        assert evidence["wire_bytes"] == len(invalid)
        assert (terminal / evidence["body_reference"]).read_bytes() == invalid
        assert not list((terminal / "bodies").iterdir())
    with pytest.raises(ValueError, match="SEC_FRESH_JOURNAL_REQUEST_COUNT_INVALID"):
        operator.read_bundle(
            root, result["acquisition_approval_sha256"], result["acquisition_journal_sha256"],
        )
