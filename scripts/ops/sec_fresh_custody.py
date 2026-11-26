"""Capture one exact owner-approved SEC Archive scope into protected preparation custody."""

from __future__ import annotations

import argparse
import base64
import http.client
import json
import os
import shlex
import shutil
import ssl
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.ceri.sec.fresh_custody import (
    FRESH_DIRECTORY,
    FRESH_JOURNAL,
    HOLDOUT_ACCESSIONS,
    MAX_APPROVAL_BYTES,
    MAX_ATTEMPTS,
    MAX_SIGNATURE_BYTES,
    MAX_WIRE_BYTES,
    archive_url,
    bundle_reference,
    check_filing_content,
    digest,
    filing_key,
    immutable_path,
    load_fresh_body,
    make_proof,
    read_bundle,
    read_bytes,
    read_json,
    signature_digest,
)
from app.settings import get_settings
from scripts.ops.ceri_prospective_revenue import _principal, _windows_system_dir
from scripts.ops.sec_processor_rollback import (
    _assert_windows_trust_acl,
    _operator_signer,
)
from scripts.production_runtime_certification import _validate_sec_user_agent

ROOT = Path(__file__).resolve().parents[2]
ACQUISITION_NAMESPACE = "swinglens-sec-fresh-acquisition"
ATTESTATION_NAMESPACE = "swinglens-sec-fresh-attestation"
ATTESTOR_PRINCIPAL = "swinglens-sec-capture-attestor"
ATTESTOR_KEY_NAME = "sec-capture-attestor"
SSH_TIMEOUT_SECONDS = 20
COMMIT = r"^[0-9a-f]{40}$"
SHA = r"^[0-9a-f]{64}$"


class FrozenTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cik: str
    accession: str
    filename: str


class FreshAcquisitionApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    operation: Literal["sec-fresh-custody-v1"]
    acquisition_id: str = Field(pattern=SHA)
    runtime_git: str = Field(pattern=COMMIT)
    archive_root: str
    operator: str
    manifest_sha256: str = Field(pattern=SHA)
    user_agent_sha256: str = Field(pattern=SHA)
    attestor_key_sha256: str = Field(pattern=SHA)
    max_get_attempts: int
    max_requests_per_second: Literal[2]
    max_redirects: Literal[0]
    max_retries: Literal[0]
    starts_at: AwareDatetime
    cutoff_at: AwareDatetime
    filings: list[FrozenTarget]


def _parse_approval(content: bytes) -> FreshAcquisitionApproval:
    read_json(content)
    approval = FreshAcquisitionApproval.model_validate_json(content)
    keys = [filing_key(item.model_dump()) for item in approval.filings]
    if (
        len(keys) != len(set(keys))
        or not 0 < len(keys) == approval.max_get_attempts <= MAX_ATTEMPTS
        or any(key[1] in HOLDOUT_ACCESSIONS for key in keys)
        or approval.starts_at.utcoffset() != timedelta(0)
        or approval.cutoff_at.utcoffset() != timedelta(0)
        or not timedelta(0) < approval.cutoff_at - approval.starts_at <= timedelta(minutes=30)
    ):
        raise ValueError("SEC_FRESH_ACQUISITION_SCOPE_OR_WINDOW_INVALID")
    return approval


@dataclass(frozen=True)
class Authenticator:
    executable: Path
    signers: Path
    owner: str
    attestor_fingerprint: str

    def verify(self, content: bytes, signature: Path, *, owner: bool) -> None:
        principal = self.owner if owner else ATTESTOR_PRINCIPAL
        namespace = ACQUISITION_NAMESPACE if owner else ATTESTATION_NAMESPACE
        result = subprocess.run(
            [str(self.executable), "-Y", "verify", "-f", str(self.signers),
             "-I", principal, "-n", namespace, "-s", str(signature)],
            input=content, capture_output=True, timeout=SSH_TIMEOUT_SECONDS, check=False,
        )
        if result.returncode:
            raise ValueError("SEC_FRESH_OWNER_OR_ATTESTOR_SIGNATURE_INVALID")


def _attestor_fingerprint(signers: Path, owner: str) -> str:
    owner_keys = set()
    attestor_keys = []
    for line in signers.read_text(encoding="utf-8").splitlines():
        fields = shlex.split(line, comments=True)
        if not fields:
            continue
        principals = fields[0].split(",")
        if any(any(char in principal for char in "*?!") for principal in principals):
            raise ValueError("SEC_FRESH_TRUST_PRINCIPAL_PATTERN_UNSUPPORTED")
        if owner not in principals and ATTESTOR_PRINCIPAL not in principals:
            continue
        for index, key_type in enumerate(fields[:-1]):
            if key_type.startswith(("ssh-", "ecdsa-", "sk-")):
                fingerprint = digest(base64.b64decode(fields[index + 1], validate=True))
                break
        else:
            raise ValueError("SEC_FRESH_TRUST_KEY_INVALID")
        options = fields[1:index]
        if owner in principals:
            owner_keys.add(fingerprint)
        if ATTESTOR_PRINCIPAL in principals:
            if (
                principals != [ATTESTOR_PRINCIPAL]
                or options != [f"namespaces={ATTESTATION_NAMESPACE}"]
                or key_type != "ssh-ed25519"
            ):
                raise ValueError("SEC_FRESH_ATTESTOR_TRUST_SCOPE_INVALID")
            attestor_keys.append(fingerprint)
    if not owner_keys or len(attestor_keys) != 1 or attestor_keys[0] in owner_keys:
        raise ValueError("SEC_FRESH_ATTESTOR_NOT_INDEPENDENT")
    return attestor_keys[0]


def _authenticator(*, qualification=False):
    if os.name != "nt":
        raise ValueError("SEC_FRESH_REQUIRES_PROTECTED_WINDOWS_AUTHORITY")
    executable, signers, owner = _operator_signer(qualification)
    _assert_windows_trust_acl(executable, signers)
    return Authenticator(executable, signers, owner, _attestor_fingerprint(signers, owner))


def _protected_paths(paths: list[Path], *, confidential=False, recurse=False) -> None:
    """Check inherited owners and ACLs without displaying path or signing-key material."""
    script = """
$ErrorActionPreference = 'Stop'
$rules = ConvertFrom-Json -InputObject ([Console]::In.ReadToEnd())
$trusted = @('S-1-5-18', 'S-1-5-32-544')
$identifier = [Security.Principal.SecurityIdentifier]
$mask = if ($rules.confidential) { 0x001F01FF } else { 0x000D0156 }
$items = if ($rules.recurse) {
    foreach ($path in $rules.paths) {
        Get-Item -LiteralPath $path -Force
        Get-ChildItem -LiteralPath $path -Recurse -Force
    }
} else { foreach ($path in $rules.paths) { Get-Item -LiteralPath $path -Force } }
foreach ($item in $items) {
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { exit 1 }
    $path = $item.FullName
    $acl = Get-Acl -LiteralPath $path
    if ($acl.GetOwner($identifier).Value -notin $trusted) { exit 1 }
    foreach ($ace in $acl.GetAccessRules($true, $true, $identifier)) {
        if ($ace.AccessControlType -eq 'Allow' -and
            ($ace.FileSystemRights -band $mask) -ne 0 -and
            $ace.IdentityReference.Value -notin $trusted) { exit 1 }
    }
}
"""
    powershell = _windows_system_dir() / "WindowsPowerShell/v1.0/powershell.exe"
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        input=json.dumps({
            "paths": [str(path) for path in paths],
            "confidential": confidential, "recurse": recurse,
        }),
        text=True, capture_output=True, timeout=180 if recurse else 30, check=False,
    )
    if result.returncode:
        raise ValueError("SEC_FRESH_ARCHIVE_OR_KEY_ACL_UNPROTECTED")


def _check_exact_fresh_scope(approval, filings, archive_root, manifest_sha256):
    if approval.archive_root != str(archive_root) or approval.manifest_sha256 != manifest_sha256:
        raise ValueError("SEC_FRESH_APPROVED_ARCHIVE_OR_MANIFEST_CHANGED")
    keys = [filing_key(item.model_dump()) for item in approval.filings]
    if any(key not in filings for key in keys):
        raise ValueError("SEC_FRESH_ACQUISITION_FILING_OUTSIDE_FROZEN_SELECTION")
    return keys


def _verified_signature_digest(auth, content, path, *, owner):
    original = signature_digest(path)
    auth.verify(content, path, owner=owner)
    if signature_digest(path) != original:
        raise ValueError("SEC_FRESH_DETACHED_SIGNATURE_CHANGED_DURING_VERIFICATION")
    return original


def load_fresh_proofs(
    rows, *, filings, archive_root, manifest_sha256, qualification=False,
):
    """Authenticate a signed preparation index's fresh bundles and each exact body."""
    if not rows:
        return {}
    auth = _authenticator(qualification=qualification)
    vault = archive_root / FRESH_DIRECTORY
    bundles = {}
    output = {}
    paths = {archive_root.parent, archive_root, vault}
    for row in rows:
        if set(row) != {
            "cik", "accession", "primary", "fresh_acquisition_sha256", "fresh_journal_sha256",
        }:
            raise ValueError("SEC_FRESH_PREPARATION_INDEX_INVALID")
        key = filing_key({**row, "filename": row["primary"]})
        if key not in filings or key in output:
            raise ValueError("SEC_FRESH_PREPARATION_FILING_DUPLICATE_OR_OUTSIDE_SCOPE")
        approval_sha = row["fresh_acquisition_sha256"]
        journal_sha = row["fresh_journal_sha256"]
        reference = bundle_reference(approval_sha, journal_sha)
        if reference not in bundles:
            parent = immutable_path(archive_root, reference)
            approval_sig = immutable_path(archive_root, reference / "approval.json.sig")
            journal_sig = immutable_path(archive_root, reference / "journal.jsonl.sig")

            protected = (parent, parent.parent, approval_sig, journal_sig,
                         parent / "approval.json", parent / "journal.jsonl", parent / "bodies")
            _protected_paths([archive_root.parent, archive_root, vault, *protected])
            raw_approval, raw_journal, _, _, receipts = read_bundle(
                archive_root, approval_sha, journal_sha,
            )
            approval_signature_sha = _verified_signature_digest(
                auth, raw_approval, approval_sig, owner=True,
            )
            signed = _parse_approval(raw_approval)
            _check_exact_fresh_scope(signed, filings, archive_root, manifest_sha256)
            if signed.attestor_key_sha256 != auth.attestor_fingerprint:
                raise ValueError("SEC_FRESH_APPROVED_ATTESTOR_CHANGED")
            journal_signature_sha = _verified_signature_digest(
                auth, raw_journal, journal_sig, owner=False,
            )
            bundles[reference] = (receipts, approval_signature_sha, journal_signature_sha)
        receipts, approval_signature_sha, journal_signature_sha = bundles[reference]
        receipt, line_sha = receipts.get(key, (None, None))
        if receipt is None:
            raise ValueError("SEC_FRESH_EXACT_SIGNED_RECEIPT_MISSING")
        filing, submissions_sha = filings[key]
        if receipt["submissions_sha256"] != submissions_sha:
            raise ValueError("SEC_FRESH_RECEIPT_SUBMISSIONS_SCOPE_MISMATCH")
        proof = make_proof(
            receipt, line_sha, approval_sha, journal_sha,
            approval_signature_sha256=approval_signature_sha,
            journal_signature_sha256=journal_signature_sha,
        )
        body = immutable_path(archive_root, reference / "bodies" / f'{proof["wire_sha256"]}.body')
        paths.add(body)
        output[key] = proof
        if proof["filing"] != filing:
            raise ValueError("SEC_FRESH_RECEIPT_FILING_SCOPE_MISMATCH")
    _protected_paths(sorted(paths))
    for key, proof in output.items():
        load_fresh_body(archive_root, filings[key][0], proof)
    return output


def _write_new(path: Path, data: bytes) -> None:
    with path.open("xb") as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())


@contextmanager
def _capture_slot(vault: Path, approval, approval_sha256: str):
    """Serialize request pacing globally and consume this signed acquisition once."""
    lock = vault / "capture.lock"
    _write_new(lock, approval_sha256.encode("ascii"))
    try:
        used = vault / "used"
        _protected_paths([vault, used])
        if used.resolve(strict=True) != used or not used.is_dir():
            raise ValueError("SEC_FRESH_ACQUISITION_INVENTORY_INVALID")
        _write_new(used / f"{approval.acquisition_id}.consumed", approval_sha256.encode("ascii"))
        root = vault / approval_sha256
        root.mkdir()
        staged = root / "capture"
        staged.mkdir()
        (staged / "bodies").mkdir()
        _protected_paths([root, staged, staged / "bodies"])
        yield staged
    finally:
        lock.unlink()


class CaptureWindowEnded(ValueError):
    """The exact signed provider window has ended."""


def _check_window(approval) -> None:
    if not approval.starts_at <= datetime.now(UTC) < approval.cutoff_at:
        raise CaptureWindowEnded("SEC_FRESH_ACQUISITION_WINDOW_INACTIVE")


def _download(filing, approval, user_agent):
    url = urlsplit(archive_url(filing))
    _check_window(approval)
    timeout = min(20, (approval.cutoff_at - datetime.now(UTC)).total_seconds())
    connection = http.client.HTTPSConnection(
        url.hostname, 443, context=ssl.create_default_context(), timeout=max(0.001, timeout),
    )
    try:
        _check_window(approval)
        connection.request(
            "GET", url.path,
            headers={
                "User-Agent": user_agent, "Accept": "text/html", "Accept-Encoding": "identity",
            },
        )
        with connection.getresponse() as response:
            if response.status != 200:
                raise ValueError(f"SEC_FRESH_HTTP_STATUS_{response.status}")
            if (
                response.headers.get_content_type() not in {"text/html", "application/xhtml+xml"}
                or response.headers.get("Content-Encoding", "identity").lower() != "identity"
            ):
                raise ValueError("SEC_FRESH_HTTP_CONTENT_TYPE_OR_ENCODING_INVALID")
            body = bytearray()
            while len(body) <= MAX_WIRE_BYTES:
                _check_window(approval)
                chunk = response.read1(min(65536, MAX_WIRE_BYTES + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
            received = datetime.now(UTC)
            content_length = response.headers.get("Content-Length")
            if (
                not 0 < len(body) <= MAX_WIRE_BYTES
                or content_length is not None and int(content_length) != len(body)
                or received >= approval.cutoff_at
            ):
                raise ValueError("SEC_FRESH_HTTP_BODY_INCOMPLETE_OR_LATE")
            return bytes(body), received
    finally:
        connection.close()


class CaptureIdentityRejected(ValueError):
    """A live response was preserved as quarantine, without an admissible receipt."""

    def __init__(self, evidence: dict):
        super().__init__("SEC_FRESH_PRIMARY_BODY_IDENTITY_INVALID")
        self.evidence = evidence


def _attempt(staged, filing, submissions_sha, approval, user_agent):
    body, received = _download(filing, approval, user_agent)
    wire_sha = digest(body)
    receipt = {
        "filing": filing, "submissions_sha256": submissions_sha,
        "archive_url": archive_url(filing), "http_status": 200, "redirect_count": 0,
        "wire_sha256": wire_sha, "wire_bytes": len(body), "received_at": received,
    }
    try:
        check_filing_content(body, filing)
    except ValueError as error:
        reference = PurePosixPath("quarantine", f"{wire_sha}.body")
        path = staged / reference
        path.parent.mkdir()
        _protected_paths([path.parent])
        _write_new(path, body)
        raise CaptureIdentityRejected({
            **receipt, "possessed_at": datetime.now(UTC),
            "body_reference": reference.as_posix(),
            "disposition": "QUARANTINED_DOCUMENT_IDENTITY_UNPROVED",
        }) from error
    body_path = staged / "bodies" / f"{wire_sha}.body"
    if body_path.exists():
        read_bytes(body_path, MAX_WIRE_BYTES, wire_sha)
    else:
        _write_new(body_path, body)
    possessed = datetime.now(UTC)
    _check_window(approval)
    return {**receipt, "possessed_at": possessed}


def _verify_capture_runtime() -> Path:
    """Require the administrator-staged standalone checkout and isolated interpreter."""
    git = shutil.which("git")
    redirected_git = ("commondir", "objects/info/alternates")
    tls_overrides = (
        "SSL_CERT_FILE", "SSL_CERT_DIR", "OPENSSL_CONF", "OPENSSL_MODULES", "SSLKEYLOGFILE",
    )
    if (
        not sys.flags.isolated or git is None or Path.cwd().resolve() != ROOT
        or not (ROOT / ".git").is_dir() or (ROOT / ".git").is_symlink()
        or any((ROOT / ".git" / name).is_file() for name in redirected_git)
        or any(os.environ.get(name) for name in tls_overrides)
        or _principal() != "sid:S-1-5-18"
    ):
        raise ValueError("SEC_FRESH_REQUIRES_ISOLATED_SYSTEM_CHECKOUT")
    executable = Path(sys.executable)
    import_paths = {Path(path) for path in sys.path if path and Path(path).exists()}
    paths = {
        ROOT.parent, ROOT, ROOT / ".git", ROOT / ".git" / "config",
        ROOT / ".git" / "objects", ROOT / "app", ROOT / "scripts",
        executable, executable.parent, executable.parent.parent,
        Path(sys.base_prefix), Path(sys.base_prefix).parent,
        Path(git), Path(git).parent, Path(git).parent.parent, *import_paths,
    }
    if any(path.resolve(strict=True) != path for path in paths):
        raise ValueError("SEC_FRESH_CAPTURE_RUNTIME_PATH_REDIRECTED")
    _protected_paths(sorted(paths))
    packages = {path for path in import_paths if path != ROOT and path.is_dir()}
    _protected_paths(sorted({ROOT / "app", ROOT / "scripts", *packages}), recurse=True)
    return Path(git)


def _capture_git_identity(git: Path) -> str:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "PATH": os.pathsep.join([str(git.parent), str(_windows_system_dir())]),
    })
    command = [
        str(git), "-C", str(ROOT), "-c", f"safe.directory={ROOT.as_posix()}",
        "-c", "core.fsmonitor=false", "-c", f"core.hooksPath={os.devnull}",
    ]
    if subprocess.check_output(
        [*command, "status", "--porcelain", "--untracked-files=normal"],
        env=env, timeout=20,
    ).strip():
        raise ValueError("SEC_FRESH_CAPTURE_CHECKOUT_DIRTY")
    return subprocess.check_output(
        [*command, "rev-parse", "HEAD"], env=env, timeout=5,
    ).decode("ascii").strip()


def _private_attestor_key(auth: Authenticator) -> Path:
    system = _windows_system_dir()
    key = Path(system.anchor) / "Program Files/SwingLens" / ATTESTOR_KEY_NAME
    if not key.is_file() or key != key.resolve(strict=True):
        raise ValueError("SEC_FRESH_ATTESTOR_PRIVATE_KEY_UNAVAILABLE")
    _protected_paths([key.parent])
    _protected_paths([key], confidential=True)
    result = subprocess.run(
        [str(auth.executable), "-y", "-f", str(key)], stdin=subprocess.DEVNULL,
        capture_output=True, timeout=SSH_TIMEOUT_SECONDS, check=False,
        env={**os.environ, "SSH_ASKPASS_REQUIRE": "never"},
    )
    public = result.stdout.split()
    if (
        result.returncode or len(public) < 2 or public[0] != b"ssh-ed25519"
        or digest(base64.b64decode(public[1], validate=True)) != auth.attestor_fingerprint
    ):
        raise ValueError("SEC_FRESH_ATTESTOR_PRIVATE_KEY_BINDING_INVALID")
    return key


def _capture(approval_path: Path, signature_path: Path, manifest_path: Path):
    from scripts.ops.sec_cache_preparation import _scope_from_manifest

    git = _verify_capture_runtime()
    auth = _authenticator()
    approval_bytes = read_bytes(approval_path, MAX_APPROVAL_BYTES)
    signature_bytes = read_bytes(signature_path, MAX_SIGNATURE_BYTES)
    auth.verify(approval_bytes, signature_path, owner=True)
    approval = _parse_approval(approval_bytes)
    manifest = read_bytes(manifest_path, 16_000_000, approval.manifest_sha256)
    archive_root = Path(approval.archive_root)
    vault = immutable_path(archive_root, PurePosixPath(FRESH_DIRECTORY))
    _protected_paths([archive_root.parent, archive_root, vault])
    user_agent = get_settings().sec_user_agent.strip()
    valid_agent, _ = _validate_sec_user_agent(user_agent)
    if (
        _capture_git_identity(git) != approval.runtime_git
        or _principal() != approval.operator or approval.operator != "sid:S-1-5-18"
        or not valid_agent or digest(user_agent.encode("utf-8")) != approval.user_agent_sha256
        or auth.attestor_fingerprint != approval.attestor_key_sha256
    ):
        raise ValueError("SEC_FRESH_ACQUISITION_RUNTIME_AUTHORITY_CHANGED")
    _, _, _, filings = _scope_from_manifest(read_json(manifest), archive_root)
    keys = _check_exact_fresh_scope(approval, filings, archive_root, digest(manifest))
    key = _private_attestor_key(auth)
    _check_window(approval)
    approval_sha = digest(approval_bytes)
    with _capture_slot(vault, approval, approval_sha) as staged:
        _write_new(staged / "approval.json", approval_bytes)
        _write_new(staged / "approval.json.sig", signature_bytes)
        auth.verify(approval_bytes, staged / "approval.json.sig", owner=True)
        receipts, quarantined, attempted, stopped = [], [], 0, None
        for target in keys:
            if attempted:
                time.sleep(0.5)
            try:
                _check_window(approval)
                filing, submissions_sha = filings[target]
                attempt_path = staged / f"attempt-{attempted + 1:03}.json"
                _write_new(attempt_path, Canonical.dumps({
                    "filing": filing, "attempted_at": datetime.now(UTC),
                }).encode("utf-8"))
                attempted += 1
                receipts.append(_attempt(staged, filing, submissions_sha, approval, user_agent))
            except CaptureIdentityRejected as error:
                quarantined.append(error.evidence)
                stopped = str(error)
                break
            except (ValueError, OSError, http.client.HTTPException) as error:
                reason = str(error)
                stopped = reason if reason.startswith("SEC_FRESH_") else type(error).__name__
                break

        header = {
            "version": FRESH_JOURNAL, "approval_sha256": approval_sha,
            "captured_at": datetime.now(UTC), "attempted": attempted,
            "verified": len(receipts), "stopped": stopped, "quarantined": quarantined,
        }
        journal = "".join(
            f"{Canonical.dumps(row)}\n" for row in [header, *receipts]
        ).encode("utf-8")
        _write_new(staged / "journal.jsonl", journal)
        signed = subprocess.run(
            [str(auth.executable), "-Y", "sign", "-n", ATTESTATION_NAMESPACE, "-f", str(key)],
            input=journal, capture_output=True, timeout=SSH_TIMEOUT_SECONDS, check=False,
            env={**os.environ, "SSH_ASKPASS_REQUIRE": "never"},
        )
        if signed.returncode or not 0 < len(signed.stdout) <= MAX_SIGNATURE_BYTES:
            raise ValueError("SEC_FRESH_CAPTURE_ATTESTATION_FAILED")
        _write_new(staged / "journal.jsonl.sig", signed.stdout)
        auth.verify(journal, staged / "journal.jsonl.sig", owner=False)
        journal_sha = digest(journal)
        final = archive_root / bundle_reference(approval_sha, journal_sha)
        if final.exists():
            raise ValueError("SEC_FRESH_CAPTURE_BUNDLE_ALREADY_EXISTS")
        _write_new(staged / "capture-result.json", Canonical.dumps({
            "attempted": attempted, "verified": len(receipts), "stopped": stopped,
            "quarantined": len(quarantined),
            "acquisition_approval_sha256": approval_sha,
            "acquisition_journal_sha256": journal_sha,
        }).encode("utf-8"))
        staged.rename(final)
    return {"attempted": attempted, "verified": len(receipts), "stopped": stopped,
            "quarantined": len(quarantined),
            "acquisition_approval_sha256": approval_sha, "acquisition_journal_sha256": journal_sha}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--signature", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(_capture(args.approval, args.signature, args.manifest), sort_keys=True))


if __name__ == "__main__":
    main()
