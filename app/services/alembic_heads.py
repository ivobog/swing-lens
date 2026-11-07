from __future__ import annotations

import hashlib
import os
from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection


def repository_alembic_heads(repo_root: Path | None = None) -> tuple[str, ...]:
    root = (repo_root or Path.cwd()).resolve()
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    return tuple(sorted(set(ScriptDirectory.from_config(config).get_heads())))


def _migration_source_digest(root: Path) -> str:
    """Read current graph source bytes, including changes that preserve timestamps."""
    digest = hashlib.sha256()

    def update(name: str, content: bytes | None) -> None:
        encoded = name.encode()
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        if content is None:
            digest.update(b"\x00")
        else:
            digest.update(b"\x01")
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)

    try:
        with open(root / "alembic.ini", "rb") as stream:
            update("alembic.ini", stream.read())
    except FileNotFoundError:
        update("alembic.ini", None)

    def visit(directory: Path | str, prefix: str) -> None:
        with os.scandir(directory) as scan:
            entries = sorted(scan, key=lambda entry: entry.name)
        for entry in entries:
            if entry.name == "__pycache__":
                continue
            name = prefix + "/" + entry.name
            if entry.is_dir(follow_symlinks=False):
                visit(entry.path, name)
            elif entry.is_file():
                with open(entry.path, "rb") as stream:
                    update(name, stream.read())

    visit(root / "alembic", "alembic")
    return digest.hexdigest()


@lru_cache(maxsize=8)
def _heads_for_migration_sources(root: Path, source_digest: str) -> tuple[str, ...]:
    heads = repository_alembic_heads(root)
    if _migration_source_digest(root) != source_digest:
        raise RuntimeError("repository migration sources changed while loading their heads")
    return heads


def cached_repository_alembic_heads(repo_root: Path | None = None) -> tuple[str, ...]:
    """Reuse native Alembic results only for freshly verified identical graph bytes.

    CERI records deployment identity per ingestion run. Importing every migration
    for each record dominated native provider ingestion. Keep only immutable head
    tuples, bound to the root and a fresh digest of the entire migration tree.
    Nonstandard version locations and sourceless graphs use the original reader.
    Database heads are deliberately not retained here.
    """
    root = (repo_root or Path.cwd()).resolve()
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    linked_layout = any(
        path.is_symlink() or path.is_junction()
        for path in (root / "alembic", root / "alembic/versions")
    )
    if linked_layout or config.get_main_option("version_locations") or any(
        str(config.get_main_option(option) or "false").lower() not in {"false", "0", "no", "off"}
        for option in ("sourceless", "recursive_version_locations")
    ):
        return repository_alembic_heads(root)
    return _heads_for_migration_sources(root, _migration_source_digest(root))


def database_alembic_heads(connection: Connection) -> tuple[str, ...]:
    # Lightweight preflight doubles expose only execute(); production
    # connections use inspection so a missing table is a normal empty set.
    if (
        hasattr(connection, "dialect")
        and "alembic_version" not in inspect(connection).get_table_names()
    ):
        return ()
    return tuple(
        sorted(
            {
                str(row[0])
                for row in connection.execute(text("select version_num from alembic_version"))
            }
        )
    )


def schema_is_at_head(connection: Connection, repo_root: Path | None = None) -> bool:
    return database_alembic_heads(connection) == repository_alembic_heads(repo_root)
