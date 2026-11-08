"""CERI may reuse repository metadata only with current identical source bytes."""
import os
from pathlib import Path

import pytest

from app.services import alembic_heads as heads
from app.services.ceri import deployment_identity as deployment


def _repository(root: Path, revision: str = "first") -> Path:
    (root / "alembic/versions").mkdir(parents=True)
    (root / "alembic.ini").write_text("[alembic]\nscript_location = alembic\n")
    _revision(root, "first.py", revision, None)
    return root


def _revision(root: Path, name: str, revision: str, parent: str | None) -> Path:
    path = root / "alembic/versions" / name
    path.write_text(f"revision = {revision!r}\ndown_revision = {parent!r}\n")
    return path


@pytest.fixture(autouse=True)
def _clear_graph_results():
    heads._heads_for_migration_sources.cache_clear()
    yield
    heads._heads_for_migration_sources.cache_clear()


def test_repeated_identities_use_one_native_graph_for_identical_source(tmp_path, monkeypatch):
    root = _repository(tmp_path / "root")
    native = heads.repository_alembic_heads
    calls = []

    def load(root):
        calls.append(root)
        return native(root)

    monkeypatch.setattr(heads, "repository_alembic_heads", load)
    for _ in range(20):
        assert heads.cached_repository_alembic_heads(root) == ("first",)
    assert len(calls) == 1


def test_added_deleted_and_ambiguous_heads_are_observed_after_warmup(tmp_path):
    root = _repository(tmp_path / "root")
    assert heads.cached_repository_alembic_heads(root) == ("first",)
    second = _revision(root, "second.py", "second", "first")
    assert heads.cached_repository_alembic_heads(root) == ("second",)
    branch = _revision(root, "branch.py", "branch", "first")
    assert heads.cached_repository_alembic_heads(root) == ("branch", "second")
    branch.unlink()
    assert heads.cached_repository_alembic_heads(root) == ("second",)
    second.unlink()
    assert heads.cached_repository_alembic_heads(root) == ("first",)


def test_same_size_edit_with_preserved_timestamp_invalidates_graph(tmp_path):
    root = _repository(tmp_path / "root")
    source = root / "alembic/versions/first.py"
    assert heads.cached_repository_alembic_heads(root) == ("first",)
    stat = source.stat()
    original = source.read_bytes()
    source.write_bytes(original.replace(b"first", b"other"))
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert source.stat().st_size == stat.st_size
    assert heads.cached_repository_alembic_heads(root) == ("other",)


def test_source_only_timestamp_change_retains_identical_native_result(tmp_path, monkeypatch):
    root = _repository(tmp_path / "root")
    assert heads.cached_repository_alembic_heads(root) == ("first",)
    source = root / "alembic/versions/first.py"
    stat = source.stat()
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000))
    monkeypatch.setattr(heads, "repository_alembic_heads", lambda _: pytest.fail("unchanged bytes"))
    assert heads.cached_repository_alembic_heads(root) == ("first",)


def test_repository_roots_never_share_a_graph_result(tmp_path):
    first = _repository(tmp_path / "one", "first")
    second = _repository(tmp_path / "two", "other")
    assert heads.cached_repository_alembic_heads(first) == ("first",)
    assert heads.cached_repository_alembic_heads(second) == ("other",)


def test_changed_helper_and_configuration_bytes_require_native_reload(tmp_path, monkeypatch):
    root = _repository(tmp_path / "root")
    native = heads.repository_alembic_heads
    calls = []
    monkeypatch.setattr(heads, "repository_alembic_heads",
                        lambda root: (calls.append(root), native(root))[1])
    assert heads.cached_repository_alembic_heads(root) == ("first",)
    (root / "alembic/helper.json").write_text('{"deployment":"new"}')
    assert heads.cached_repository_alembic_heads(root) == ("first",)
    with (root / "alembic.ini").open("a") as stream:
        stream.write("\n# current deployment\n")
    assert heads.cached_repository_alembic_heads(root) == ("first",)
    assert len(calls) == 3


def test_invalid_or_unreadable_sources_cannot_return_previous_heads(tmp_path, monkeypatch):
    root = _repository(tmp_path / "root")
    assert heads.cached_repository_alembic_heads(root) == ("first",)
    source = root / "alembic/versions/first.py"
    source.write_text("this is not valid Python !!!")
    with pytest.raises(SyntaxError):
        heads.cached_repository_alembic_heads(root)
    _revision(root, "first.py", "first", None)
    assert heads.cached_repository_alembic_heads(root) == ("first",)
    monkeypatch.setattr(heads, "_migration_source_digest",
                        lambda _: (_ for _ in ()).throw(OSError("unreadable source")))
    with pytest.raises(OSError, match="unreadable source"):
        heads.cached_repository_alembic_heads(root)


def test_graph_changed_during_native_load_is_rejected_without_retaining_result(
    tmp_path, monkeypatch,
):
    root = _repository(tmp_path / "root")
    native = heads.repository_alembic_heads
    changed = False

    def load(root):
        nonlocal changed
        result = native(root)
        if not changed:
            changed = True
            _revision(root, "next.py", "next", "first")
        return result

    monkeypatch.setattr(heads, "repository_alembic_heads", load)
    with pytest.raises(RuntimeError, match="changed while loading"):
        heads.cached_repository_alembic_heads(root)
    assert heads.cached_repository_alembic_heads(root) == ("next",)


@pytest.mark.parametrize("option", ("version_locations = versions", "sourceless = true",
                                    "recursive_version_locations = true",
                                    "alembic-symlink", "versions-junction"))
def test_nonstandard_graph_layout_keeps_original_fresh_reader(tmp_path, monkeypatch, option):
    root = _repository(tmp_path / "root")
    if option in {"alembic-symlink", "versions-junction"}:
        attribute = "is_symlink" if option == "alembic-symlink" else "is_junction"
        linked = root / ("alembic" if option == "alembic-symlink" else "alembic/versions")
        original = getattr(Path, attribute)
        monkeypatch.setattr(Path, attribute, lambda path: path == linked or original(path))
    else:
        with (root / "alembic.ini").open("a") as stream:
            stream.write(option + "\n")
    results = iter((("first",), ("new",)))
    monkeypatch.setattr(heads, "repository_alembic_heads", lambda _: next(results))
    assert heads.cached_repository_alembic_heads(root) == ("first",)
    assert heads.cached_repository_alembic_heads(root) == ("new",)


def test_deployment_schema_mismatch_still_rejects_after_graph_warmup(tmp_path):
    root = _repository(tmp_path / "root")
    original = deployment.current_deployment_identity(
        config_hash="cfg", calculation_version="calc", repo_root=root,
        database_schema_revision="first")
    with pytest.raises(deployment.DeploymentSchemaMismatch, match="database=stale"):
        deployment.current_deployment_identity(config_hash="cfg", calculation_version="calc",
            repo_root=root, database_schema_revision="stale")
    _revision(root, "branch.py", "branch", None)
    with pytest.raises(deployment.DeploymentSchemaMismatch, match="HEAD_AMBIGUOUS"):
        deployment.current_deployment_identity(config_hash="cfg", calculation_version="calc",
            repo_root=root, database_schema_revision="first")
    assert original["schema_revision"] == "first" and original["schema_revision_match"]
