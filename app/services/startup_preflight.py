from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.pool import NullPool

from app.services.redaction import redact_text
from app.settings import Settings, get_settings


class StartupPreflightError(RuntimeError):
    """A mandatory startup dependency is unavailable or inconsistent."""


@dataclass(frozen=True)
class StartupPreflightReport:
    database_endpoint: str
    alembic_heads: tuple[str, ...]
    storage_paths: tuple[Path, ...]


EngineFactory = Callable[..., Engine]


def run_startup_preflight(
    settings: Settings | None = None,
    *,
    repo_root: Path | None = None,
    engine_factory: EngineFactory = create_engine,
) -> StartupPreflightReport:
    """Verify startup gates without applying migrations or touching business data."""

    settings = settings or get_settings()
    root = (repo_root or Path.cwd()).resolve()
    endpoint = _database_endpoint(settings.database_url)
    expected_heads = _repository_alembic_heads(root)

    engine: Engine | None = None
    try:
        url = make_url(settings.database_url)
        if not url.drivername.startswith("postgresql"):
            raise StartupPreflightError(
                f"configured authoritative database at {endpoint} is not PostgreSQL"
            )
        engine = engine_factory(
            settings.database_url,
            poolclass=NullPool,
            connect_args={"connect_timeout": settings.database_connect_timeout_seconds},
        )
        with engine.connect() as connection:
            connection.execute(text("select 1"))
            current_heads = tuple(
                sorted(
                    str(row[0])
                    for row in connection.execute(text("select version_num from alembic_version"))
                )
            )
    except StartupPreflightError:
        raise
    except Exception as exc:
        raise StartupPreflightError(
            f"database unavailable at {endpoint}: {redact_text(str(exc))}"
        ) from exc
    finally:
        if engine is not None:
            engine.dispose()

    if current_heads != expected_heads:
        current = ",".join(current_heads) if current_heads else "<missing>"
        expected = ",".join(expected_heads) if expected_heads else "<missing>"
        raise StartupPreflightError(
            f"migration head mismatch at {endpoint}: current={current}; expected={expected}; "
            "run the lifecycle start command to apply migrations"
        )

    storage_paths = tuple(
        _resolve_storage_path(root, path)
        for path in (settings.upload_dir, settings.export_dir, settings.cache_dir)
    )
    for path in storage_paths:
        _verify_storage_access(path)

    return StartupPreflightReport(
        database_endpoint=endpoint,
        alembic_heads=expected_heads,
        storage_paths=storage_paths,
    )


def _repository_alembic_heads(repo_root: Path) -> tuple[str, ...]:
    try:
        config = Config(str(repo_root / "alembic.ini"))
        config.set_main_option("script_location", str(repo_root / "alembic"))
        return tuple(sorted(ScriptDirectory.from_config(config).get_heads()))
    except (OSError, ValueError) as exc:
        raise StartupPreflightError(
            f"cannot read repository Alembic heads: {redact_text(str(exc))}"
        ) from exc


def _resolve_storage_path(repo_root: Path, path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (repo_root / path).resolve()


def _verify_storage_access(path: Path) -> None:
    try:
        if not path.is_dir():
            raise OSError(f"required storage directory does not exist: {path}")
        if not os.access(path, os.R_OK | os.W_OK):
            raise OSError(f"required storage directory is not readable and writable: {path}")
        probe_fd, probe_name = tempfile.mkstemp(prefix=".swinglens-preflight-", dir=path)
        os.close(probe_fd)
        Path(probe_name).unlink()
    except OSError as exc:
        raise StartupPreflightError(
            f"required local storage is unavailable: {redact_text(str(exc))}"
        ) from exc


def _database_endpoint(database_url: str) -> str:
    try:
        url = make_url(database_url)
    except Exception as exc:
        raise StartupPreflightError("DATABASE_URL is invalid") from exc
    host = url.host or "<local>"
    port = url.port or 5432
    database = url.database or "<missing>"
    return f"{host}:{port}/{database}"
