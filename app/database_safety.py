from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.pool import NullPool

from app.services.redaction import redact_text
from app.settings import get_settings

DEFAULT_DISPOSABLE_PREFIXES = (
    "swinglens_pytest_",
    "swinglens_qa_",
    "swinglens_obs_cert_",
)


@dataclass(frozen=True)
class DisposableDatabaseIdentity:
    database_name: str
    server_identity: str
    safe_url: str


def safe_database_url(database_url: str | URL) -> str:
    url = database_url if isinstance(database_url, URL) else make_url(database_url)
    return redact_text(url.render_as_string(hide_password=True))


def assert_disposable_database(
    candidate_database_url: str | URL,
    *,
    active_database_url: str | URL | None = None,
    allowed_prefixes: tuple[str, ...] | None = None,
) -> DisposableDatabaseIdentity:
    """Fail closed before Alembic is invoked for a disposable test database."""
    try:
        candidate = (
            candidate_database_url
            if isinstance(candidate_database_url, URL)
            else make_url(candidate_database_url)
        )
    except Exception as exc:
        raise RuntimeError("candidate database URL is malformed") from exc
    if not candidate.drivername.startswith("postgresql") or not candidate.database:
        raise RuntimeError("candidate database must be an explicitly named PostgreSQL database")

    prefixes = allowed_prefixes or _configured_prefixes()
    active_value = active_database_url or get_settings().database_url
    active_url = active_value if isinstance(active_value, URL) else make_url(active_value)
    candidate_name, candidate_server = _read_identity(candidate)
    active_name, active_server = _read_identity(active_url)
    if candidate_name != candidate.database:
        raise RuntimeError("candidate URL database does not match connected database identity")
    if candidate_name == active_name and candidate_server == active_server:
        raise RuntimeError("refusing to use the active SwingLens database as disposable")
    if not any(candidate_name.startswith(prefix) for prefix in prefixes):
        raise RuntimeError("connected database identity does not match a disposable prefix")
    identity = DisposableDatabaseIdentity(
        database_name=candidate_name,
        server_identity=candidate_server,
        safe_url=safe_database_url(candidate),
    )
    print(
        "verified disposable database before Alembic: "
        f"database={identity.database_name} server={identity.server_identity} "
        f"url={identity.safe_url}"
    )
    return identity


def configure_guarded_alembic(config, candidate_database_url: str | URL):
    identity = assert_disposable_database(candidate_database_url)
    config.attributes["database_url"] = (
        candidate_database_url.render_as_string(hide_password=False)
        if isinstance(candidate_database_url, URL)
        else candidate_database_url
    )
    config.attributes["disposable_database_identity"] = identity
    return identity


def run_guarded_alembic_upgrade(
    config,
    candidate_database_url: str | URL,
    revision: str = "head",
    *,
    upgrade=None,
) -> DisposableDatabaseIdentity:
    """Verify identity before the Alembic command boundary is entered."""
    identity = configure_guarded_alembic(config, candidate_database_url)
    if upgrade is None:
        from alembic import command

        upgrade = command.upgrade
    upgrade(config, revision)
    return identity


def assert_alembic_connection_matches(connection, expected: DisposableDatabaseIdentity) -> None:
    actual_name = str(connection.execute(text("select current_database() ")).scalar_one())
    actual_server = _server_identity(connection)
    if actual_name != expected.database_name or actual_server != expected.server_identity:
        raise RuntimeError(
            "Alembic connected to a database other than the verified disposable target"
        )


def _read_identity(database_url: URL) -> tuple[str, str]:
    engine = create_engine(database_url, poolclass=NullPool, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            return (
                str(connection.execute(text("select current_database() ")).scalar_one()),
                _server_identity(connection),
            )
    except Exception as exc:
        raise RuntimeError("database identity preflight failed") from exc
    finally:
        engine.dispose()


def _server_identity(connection) -> str:
    return str(
        connection.execute(
            text("select coalesce(inet_server_addr()::text, 'local') || ':' || inet_server_port()")
        ).scalar_one()
    )


def _configured_prefixes() -> tuple[str, ...]:
    configured = os.environ.get("SWINGLENS_DISPOSABLE_DATABASE_PREFIXES", "")
    values = tuple(item.strip() for item in configured.split(",") if item.strip())
    return values or DEFAULT_DISPOSABLE_PREFIXES
