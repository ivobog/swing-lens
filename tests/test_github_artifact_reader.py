"""Contents/Git Blob transport controls; no synthetic owner approval claims."""

import base64
import hashlib

import pytest

from scripts.qa.golden_provenance import GITHUB_REPOSITORY, ProvenanceError, _GitHubReader


def reader_for(content, *, encoding="none", mutation=None):
    sha = hashlib.sha1(
        b"blob " + str(len(content)).encode() + b"\0" + content,
        usedforsecurity=False,
    ).hexdigest()
    metadata = {"type": "file", "encoding": encoding, "sha": sha, "size": len(content)}
    blob = {"encoding": "base64", "sha": sha, "size": len(content),
            "content": base64.b64encode(content).decode()}
    if encoding == "base64":
        metadata["content"] = blob["content"]
    if mutation:
        mutation(metadata, blob)
    calls = []

    def get(path):
        calls.append(path)
        if "/contents/" in path:
            return metadata
        assert path == f"/repos/{GITHUB_REPOSITORY}/git/blobs/{sha}"
        return blob

    reader = _GitHubReader("synthetic-read-only-transport-test")
    reader.get = get
    return reader, calls


def test_commit_bound_large_regular_artifact_uses_git_blob_and_preserves_bytes():
    content = b"independent frozen raw input\n" * 50000
    assert len(content) > 1024 * 1024
    reader, calls = reader_for(content)
    observed = reader.file("tests/frozen-market-data.json", "a" * 40)
    assert observed == content
    assert hashlib.sha256(observed).digest() == hashlib.sha256(content).digest()
    assert len(calls) == 2 and calls[0].endswith("?ref=" + "a" * 40)


def test_small_contents_artifact_retains_single_read():
    reader, calls = reader_for(b"retained bytes", encoding="base64")
    assert reader.file("config/example.yaml", "b" * 40) == b"retained bytes"
    assert len(calls) == 1


@pytest.mark.parametrize("fault", ["nonregular", "oversize", "bad-sha", "size", "blob-sha", "body"])
def test_invalid_or_substituted_blob_cannot_satisfy_the_artifact(fault):
    def mutate(metadata, blob):
        if fault == "nonregular":
            metadata["type"] = "symlink"
        elif fault == "oversize":
            metadata["size"] = 10 * 1024 * 1024 + 1
        elif fault == "bad-sha":
            metadata["sha"] = "../outside-repository"
        elif fault == "size":
            blob["size"] += 1
        elif fault == "blob-sha":
            blob["sha"] = "0" * 40
        else:
            blob["content"] = base64.b64encode(b"substituted! ").decode()

    reader, calls = reader_for(b"sealed bytes!", mutation=mutate)
    with pytest.raises(ProvenanceError):
        reader.file("data/frozen.json", "c" * 40)
    if fault in {"nonregular", "oversize", "bad-sha"}:
        assert len(calls) == 1
