"""Version-2 release source identity over Git's committed tree, never checkout bytes."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

SOURCE_ROOTS = ("app/", "alembic/", "config/", "scripts/")
SOURCE_FILES = frozenset({".env.example", "alembic.ini", "pyproject.toml"})
SCHEMA_VERSION = "git-committed-source-freeze-v2"
TOOL_VERSION = "release-remediation-1"


def _git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *args])


def source_freeze(root: Path, revision: str) -> dict:
    """Bind a resolved commit, ordered path set, and exact Git blob identities."""
    commit = _git(root, "rev-parse", "--verify", f"{revision}^{{commit}}").decode().strip()
    tree = _git(root, "ls-tree", "-r", "-z", "--full-tree", commit)
    entries: list[dict[str, str]] = []
    for record in tree.split(b"\0"):
        if not record:
            continue
        descriptor, raw_path = record.split(b"\t", 1)
        mode, kind, object_id = descriptor.decode("ascii").split()
        if kind != "blob":
            continue
        path = raw_path.decode("utf-8", errors="surrogateescape")
        if path.startswith(SOURCE_ROOTS) or path in SOURCE_FILES:
            group = "implementation"
        elif path.startswith("tests/"):
            group = "tests"
        else:
            continue
        entries.append({"path": path, "mode": mode, "blob_id": object_id, "group": group})
    entries.sort(key=lambda entry: entry["path"].encode("utf-8", errors="surrogateescape"))
    payload = {
        "source_freeze_version": 2,
        "schema_version": SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "commit": commit,
        "entries": entries,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return {
        **payload,
        "implementation_count": sum(entry["group"] == "implementation" for entry in entries),
        "test_count": sum(entry["group"] == "tests" for entry in entries),
        "fingerprint_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def committed_blob_sha256(root: Path, revision: str, path: str) -> str:
    """Hash exact committed bytes for immutable historical artifact checks."""
    return hashlib.sha256(_git(root, "show", f"{revision}:{path}")).hexdigest()


def current_committed_source_bytes(root: Path, path: str) -> bytes:
    """Use a clean tracked blob; reject uncommitted changes to an authority file."""
    if not _git(root, "ls-files", "--cached", "--", path):
        return (root / path).read_bytes()
    if _git(root, "diff", "--name-only", "HEAD", "--", path):
        raise ValueError(f"Uncommitted authority source cannot be certified: {path}")
    return _git(root, "show", f"HEAD:{path}")


def classify_legacy_checkout_hash(root: Path, revision: str, path: str, expected: str) -> str:
    """Prove a legacy byte pin from one exact committed blob and Git's checkout filter.

    This is a bridge for pre-v2 certificates, not the v2 source identity. A
    mismatch under both concrete materializations is a hard failure.
    """
    blob = _git(root, "show", f"{revision}:{path}")
    if hashlib.sha256(blob).hexdigest() == expected:
        return "GIT_COMMITTED_BLOB"
    filtered = subprocess.check_output(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "core.autocrlf=true",
            "cat-file",
            "--filters",
            f"--path={path}",
            f"{revision}:{path}",
        ]
    )
    if hashlib.sha256(filtered).hexdigest() == expected:
        return "GIT_CHECKOUT_AUTOCRLF_TRUE"
    raise ValueError(f"legacy hash does not match committed blob or Git checkout: {path}")
