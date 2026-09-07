from __future__ import annotations

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
