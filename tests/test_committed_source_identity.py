from __future__ import annotations

import subprocess
from pathlib import Path

from scripts.qa.committed_source_identity import source_freeze


def _run(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def test_checkout_line_endings_do_not_change_committed_identity(tmp_path: Path) -> None:
    repo = tmp_path / "source"
    repo.mkdir()
    _run(repo, "init")
    _run(repo, "config", "user.name", "Release Test")
    _run(repo, "config", "user.email", "release@example.invalid")
    (repo / "app").mkdir()
    (repo / "app" / "example.py").write_bytes(b"a = 1\nb = 2\n")
    _run(repo, "add", "app/example.py")
    _run(repo, "commit", "-m", "initial")
    first = source_freeze(repo, "HEAD")
    lf, crlf = tmp_path / "lf", tmp_path / "crlf"
    subprocess.check_call(["git", "clone", "-q", "-c", "core.autocrlf=false", str(repo), str(lf)])
    subprocess.check_call(["git", "clone", "-q", "-c", "core.autocrlf=true", str(repo), str(crlf)])
    assert source_freeze(lf, "HEAD") == source_freeze(crlf, "HEAD") == first
    assert (lf / "app" / "example.py").read_bytes() == b"a = 1\nb = 2\n"
    assert (crlf / "app" / "example.py").read_bytes() == b"a = 1\r\nb = 2\r\n"

    # A new committed byte changes the blob, commit, and aggregate fingerprint.
    (repo / "app" / "example.py").write_bytes(b"a = 1\nb = 3\n")
    _run(repo, "add", "app/example.py")
    _run(repo, "commit", "-m", "real source change")
    second = source_freeze(repo, "HEAD")
    assert second["fingerprint_sha256"] != first["fingerprint_sha256"]
    assert second["entries"][0]["blob_id"] != first["entries"][0]["blob_id"]
