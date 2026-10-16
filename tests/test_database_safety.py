from __future__ import annotations

import pytest
from sqlalchemy.engine import make_url

from app.database_safety import resolve_test_postgres_admin_url


def test_local_configured_credentials_resolve_to_admin_database(monkeypatch) -> None:
    monkeypatch.delenv("SWINGLENS_TEST_POSTGRES_ADMIN_URL", raising=False)

    resolved = make_url(
        resolve_test_postgres_admin_url(
            configured_database_url=(
                "postgresql+psycopg://test-user:test-secret@127.0.0.1:5439/swinglens"
            )
        )
    )

    assert resolved.drivername == "postgresql"
    assert resolved.database == "postgres"
    assert resolved.host == "127.0.0.1"
    assert resolved.port == 5439
    assert resolved.username == "test-user"
    assert resolved.password == "test-secret"


def test_nonlocal_fallback_is_rejected(monkeypatch) -> None:
    monkeypatch.delenv("SWINGLENS_TEST_POSTGRES_ADMIN_URL", raising=False)

    with pytest.raises(RuntimeError, match="is required"):
        resolve_test_postgres_admin_url(
            configured_database_url="postgresql://user:secret@db.example/swinglens"
        )


def test_explicit_test_admin_url_supports_ci_without_exposing_default_credentials(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "SWINGLENS_TEST_POSTGRES_ADMIN_URL",
        "postgresql://ci-user:ci-secret@postgres-test:5432/ignored",
    )

    resolved = make_url(resolve_test_postgres_admin_url())

    assert resolved.host == "postgres-test"
    assert resolved.database == "postgres"
    assert resolved.username == "ci-user"
