"""Read-only validation of immutable, preparation-only SEC acquisition evidence."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

FRESH_DIRECTORY = "SEC_FRESH_CUSTODY"
FRESH_JOURNAL = "swinglens-sec-fresh-custody-v1"
MAX_ATTEMPTS = 522
# The frozen selection includes retained originals up to 35,346,901 bytes.
MAX_WIRE_BYTES = 40 * 1024 * 1024
MAX_APPROVAL_BYTES = 128 * 1024
MAX_JOURNAL_BYTES = 2 * 1024 * 1024
SHA256 = re.compile(r"[0-9a-f]{64}")
KEY_FIELDS = ("cik", "accession", "filename")
ALLOWED_FORMS = frozenset({"8-K", "10-K", "10-Q", "6-K", "20-F"})
HOLDOUT_ACCESSIONS = frozenset({"0001193125-26-470295", "0001534504-26-000039"})
RECEIPT_FIELDS = frozenset({
    "filing", "submissions_sha256", "archive_url", "http_status", "redirect_count",
    "wire_sha256", "wire_bytes", "received_at", "possessed_at",
})


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def filing_key(filing: dict) -> tuple[str, str, str]:
    cik, accession, filename = (filing.get(key, "") for key in KEY_FIELDS)
    if (
        not all(isinstance(value, str) for value in (cik, accession, filename))
        or not re.fullmatch("[0-9]{10}", cik)
        or not 0 < int(cik)
        or not re.fullmatch("[0-9]{10}-[0-9]{2}-[0-9]{6}", accession)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,254}", filename)
    ):
        raise ValueError("SEC_FRESH_FILING_IDENTITY_INVALID")
    return cik, accession, filename


def archive_url(filing: dict) -> str:
    cik, accession, filename = filing_key(filing)
    return (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
        f"{accession.replace('-', '')}/{filename}"
    )


def _digest_value(value: str) -> str:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise ValueError("SEC_FRESH_DIGEST_INVALID")
    return value


def bundle_reference(approval_sha256: str, journal_sha256: str) -> PurePosixPath:
    approval = _digest_value(approval_sha256)
    journal = _digest_value(journal_sha256)
    # Keep the full two-digest identity while leaving room for Windows body paths.
    return PurePosixPath(FRESH_DIRECTORY, digest(f"{approval}\n{journal}".encode("ascii")))


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("SEC_FRESH_DUPLICATE_EVIDENCE_FIELD")
        value[key] = item
    return value


def read_json(data: bytes) -> dict:
    parsed = json.loads(data.decode("utf-8"), object_pairs_hook=_object)
    if not isinstance(parsed, dict):
        raise ValueError("SEC_FRESH_EVIDENCE_OBJECT_REQUIRED")
    return parsed


def immutable_path(archive_root: str | Path, reference: PurePosixPath) -> Path:
    root = Path(archive_root)
    target = root.joinpath(*reference.parts)
    try:
        if not root.is_absolute() or root.resolve(strict=True) != root:
            raise ValueError("SEC_FRESH_ARCHIVE_ROOT_INVALID")
        if (
            reference.is_absolute()
            or ".." in reference.parts
            or not target.is_relative_to(root)
            or target.resolve(strict=True) != target
        ):
            raise ValueError("SEC_FRESH_ARCHIVE_PATH_INVALID")
    except OSError as error:
        raise ValueError("SEC_FRESH_ARCHIVE_PATH_INVALID") from error
    return target


def read_bytes(path: Path, limit: int, expected: str | None = None) -> bytes:
    try:
        if not path.is_file():
            raise ValueError("SEC_FRESH_ARCHIVE_FILE_MISSING")
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
    except OSError as error:
        raise ValueError("SEC_FRESH_ARCHIVE_FILE_UNREADABLE") from error
    if len(data) > limit or (expected is not None and digest(data) != expected):
        raise ValueError("SEC_FRESH_ARCHIVE_DIGEST_OR_SIZE_INVALID")
    return data

def utc(value):
    if not isinstance(value, str):
        raise ValueError("SEC_FRESH_TIMESTAMP_INVALID")
    stamp = datetime.fromisoformat(value)
    if stamp.utcoffset() != timedelta(0):
        raise ValueError("SEC_FRESH_TIMESTAMP_MUST_BE_UTC")
    return stamp


def check_filing_content(body: bytes, filing: dict) -> str:
    from app.services.ceri.sec.preparation_evidence import _identity_consistent

    text = body.decode("utf-8")
    cik, _, _ = filing_key(filing)
    form = filing.get("form")
    if (
        form not in ALLOWED_FORMS
        or not _identity_consistent(text, cik, form)
        or not re.search(rf"(?<![A-Za-z0-9]){re.escape(form)}(?![A-Za-z0-9])", text, re.I)
        or not re.search(r"securities.{0,64}exchange.{0,64}commission", text, re.I | re.S)
    ):
        raise ValueError("SEC_FRESH_PRIMARY_BODY_IDENTITY_INVALID")
    return text


def read_bundle(archive_root, approval_sha256, journal_sha256):
    """Resolve the sealed raw bytes. Signature validation is performed by the operator."""
    reference = bundle_reference(approval_sha256, journal_sha256)
    approval_bytes = read_bytes(
        immutable_path(archive_root, reference / "approval.json"),
        MAX_APPROVAL_BYTES, approval_sha256,
    )
    journal_bytes = read_bytes(
        immutable_path(archive_root, reference / "journal.jsonl"),
        MAX_JOURNAL_BYTES, journal_sha256,
    )
    approval = read_json(approval_bytes)
    lines = journal_bytes.splitlines()
    if not lines or len(lines) > 1 + MAX_ATTEMPTS:
        raise ValueError("SEC_FRESH_JOURNAL_LENGTH_INVALID")
    header = read_json(lines[0])
    if (
        header.get("version") != FRESH_JOURNAL
        or header.get("approval_sha256") != approval_sha256
        or type(header.get("verified")) is not int or header["verified"] != len(lines) - 1
    ):
        raise ValueError("SEC_FRESH_JOURNAL_HEADER_INVALID")
    targets = [filing_key(item) for item in approval["filings"]]
    receipts = {}
    for line in lines[1:]:
        receipt = read_json(line)
        key = filing_key(receipt["filing"])
        if set(receipt) != RECEIPT_FIELDS or key not in targets or key in receipts:
            raise ValueError("SEC_FRESH_JOURNAL_SCOPE_INVALID")
        receipts[key] = (receipt, digest(line))
    if (
        type(header.get("attempted")) is not int
        or not 0 < len(receipts) <= header["attempted"] <= len(targets) <= MAX_ATTEMPTS
        or len(targets) != len(set(targets)) or len(targets) != approval["max_get_attempts"]
        or list(receipts) != targets[:len(receipts)] or header["attempted"] > len(receipts) + 1
    ):
        raise ValueError("SEC_FRESH_JOURNAL_REQUEST_COUNT_INVALID")
    return approval_bytes, journal_bytes, approval, header, receipts


def make_proof(receipt: dict, line_sha256: str, approval_sha256: str, journal_sha256: str):
    reference = bundle_reference(approval_sha256, journal_sha256)
    body = reference / "bodies" / f'{_digest_value(receipt["wire_sha256"])}.body'
    return {
        **receipt, "archive_receipt_sha256": _digest_value(line_sha256),
        "acquisition_approval_sha256": approval_sha256,
        "acquisition_journal_sha256": journal_sha256,
        "body_reference": body.as_posix(),
    }


def load_fresh_body(archive_root, filing, proof) -> str:
    """Recheck the exact signed inventory bytes, receipt and sidecar body at use."""
    try:
        approval_sha = proof["acquisition_approval_sha256"]
        journal_sha = proof["acquisition_journal_sha256"]
        _, _, approval, header, receipts = read_bundle(archive_root, approval_sha, journal_sha)
        receipt, line_sha = receipts[filing_key(filing)]
        if (
            proof != make_proof(receipt, line_sha, approval_sha, journal_sha)
            or Path(approval["archive_root"]) != Path(archive_root)
            or receipt["filing"] != filing
            or receipt["archive_url"] != archive_url(filing)
            or type(receipt["http_status"]) is not int or receipt["http_status"] != 200
            or type(receipt["redirect_count"]) is not int or receipt["redirect_count"] != 0
            or not _digest_value(receipt["submissions_sha256"])
            or not 0 < receipt["wire_bytes"] <= MAX_WIRE_BYTES
            or type(receipt["wire_bytes"]) is not int
            or not utc(filing["accepted_at"]) <= utc(approval["starts_at"])
            <= utc(receipt["received_at"]) <= utc(receipt["possessed_at"])
            < utc(approval["cutoff_at"])
            or not utc(receipt["possessed_at"]) <= utc(header["captured_at"]) <= datetime.now(UTC)
            or not timedelta(0) < utc(approval["cutoff_at"]) - utc(approval["starts_at"])
            <= timedelta(minutes=30)
        ):
            raise ValueError("SEC_FRESH_RECEIPT_INVALID")
        body = read_bytes(
            immutable_path(archive_root, PurePosixPath(proof["body_reference"])),
            receipt["wire_bytes"], receipt["wire_sha256"],
        )
        if len(body) != receipt["wire_bytes"]:
            raise ValueError("SEC_FRESH_BODY_LENGTH_INVALID")
        return check_filing_content(body, filing)
    except (AttributeError, KeyError, TypeError) as error:
        raise ValueError("SEC_FRESH_RECEIPT_INVALID") from error
