"""Write destinations follow native directory descriptors without widening ownership."""

import os
import tempfile

import evidence_output as evidence
import pytest


@pytest.mark.parametrize(
    "event,args",
    [
        ("os.remove", ("config", 42)),
        ("os.rmdir", (b"config", 42)),
        ("os.mkdir", ("config", 0o700, 42)),
        ("os.chmod", ("config", 0o700, 42)),
        ("os.utime", ("config", None, None, 42)),
        ("os.rename", ("config", "new", 42, 43)),
        ("os.link", ("config", "new", 43, 42)),
        ("os.symlink", ("target", "config", 42)),
    ],
)
def test_descriptor_relative_destinations_keep_protection(tmp_path, monkeypatch, event, args):
    protected = tmp_path / "repository"
    temporary = tmp_path / "temporary"
    protected.mkdir()
    temporary.mkdir()
    anchors = {42: temporary, 43: temporary}
    monkeypatch.setattr(evidence, "_directory_descriptor_path", anchors.__getitem__)
    guard = evidence.history_write_guard(protected=[protected], allowed=[])
    guard(event, args)
    anchors[42] = protected
    with pytest.raises(evidence.EvidenceWriteError, match="HISTORICAL_EVIDENCE_WRITE_REFUSED"):
        guard(event, args)


def test_unknown_descriptor_cannot_fall_back_to_working_directory(tmp_path):
    guard = evidence.history_write_guard(protected=[tmp_path], allowed=[])
    with pytest.raises(evidence.EvidenceWriteError, match="UNRESOLVED_WRITE_DIRECTORY_DESCRIPTOR"):
        guard("os.remove", ("outside-looking-name", 987654321))


def test_native_temporary_cleanup_and_protected_descriptor(tmp_path):
    # The native fd branch is exercised on Linux CI; Windows uses absolute paths.
    with tempfile.TemporaryDirectory(dir=tmp_path) as directory:
        target = os.path.join(directory, "config")
        os.mkdir(target)
        with open(os.path.join(target, "fixture.json"), "w") as handle:
            handle.write("temporary fixture")
    assert not os.path.exists(directory)
    if os.remove in os.supports_dir_fd:
        protected = tmp_path / "history"
        protected.mkdir()
        (protected / "keep.json").write_text("immutable")
        descriptor = os.open(protected, os.O_RDONLY)
        try:
            guard = evidence.history_write_guard(protected=[protected], allowed=[])
            with pytest.raises(
                evidence.EvidenceWriteError, match="HISTORICAL_EVIDENCE_WRITE_REFUSED"
            ):
                guard("os.remove", ("keep.json", descriptor))
            with pytest.raises(
                evidence.EvidenceWriteError, match="HISTORICAL_EVIDENCE_WRITE_REFUSED"
            ):
                guard("os.rename", ("keep.json", "elsewhere", descriptor, -1))
            assert (protected / "keep.json").read_text() == "immutable"
        finally:
            os.close(descriptor)
