from __future__ import annotations

from pathlib import Path

import pytest

from app.services import startup_preflight
from app.settings import Settings


class _Rows:
    def __init__(self, rows=()) -> None:
        self.rows = rows

    def __iter__(self):
        return iter(self.rows)


class _Connection:
    def __init__(self, current_heads: tuple[str, ...]) -> None:
        self.current_heads = current_heads

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, statement):
        if "alembic_version" in str(statement):
            return _Rows([(head,) for head in self.current_heads])
        return _Rows([(1,)])


class _Engine:
    def __init__(self, current_heads: tuple[str, ...]) -> None:
        self.current_heads = current_heads
        self.disposed = False

    def connect(self):
        return _Connection(self.current_heads)

    def dispose(self) -> None:
        self.disposed = True


def _settings(tmp_path: Path, database_url: str | None = None) -> Settings:
    for name in ("uploads", "exports", "cache"):
        (tmp_path / name).mkdir()
    return Settings(
        _env_file=None,
        database_url=database_url
        or "postgresql+psycopg://user:secret@127.0.0.1:5432/swinglens",
        upload_dir=tmp_path / "uploads",
        export_dir=tmp_path / "exports",
        cache_dir=tmp_path / "cache",
    )


def test_database_unavailable_fails_with_redacted_message(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(startup_preflight, "_repository_alembic_heads", lambda _root: ("head",))

    def unavailable(*_args, **_kwargs):
        raise RuntimeError(
            "connect failed postgresql://user:secret@127.0.0.1:5432/swinglens"
        )

    with pytest.raises(startup_preflight.StartupPreflightError) as exc_info:
        startup_preflight.run_startup_preflight(
            _settings(tmp_path), repo_root=tmp_path, engine_factory=unavailable
        )

    message = str(exc_info.value)
    assert "secret" not in message
    assert "user:" not in message
    assert "127.0.0.1:5432/swinglens" in message


def test_migration_mismatch_fails(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(startup_preflight, "_repository_alembic_heads", lambda _root: ("head",))

    with pytest.raises(startup_preflight.StartupPreflightError, match="migration head mismatch"):
        startup_preflight.run_startup_preflight(
            _settings(tmp_path),
            repo_root=tmp_path,
            engine_factory=lambda *_args, **_kwargs: _Engine(("old-head",)),
        )


def test_successful_preflight_checks_db_schema_and_storage(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(startup_preflight, "_repository_alembic_heads", lambda _root: ("head",))
    engine = _Engine(("head",))

    report = startup_preflight.run_startup_preflight(
        _settings(tmp_path),
        repo_root=tmp_path,
        engine_factory=lambda *_args, **_kwargs: engine,
    )

    assert report.database_endpoint == "127.0.0.1:5432/swinglens"
    assert report.alembic_heads == ("head",)
    assert len(report.storage_paths) == 3
    assert engine.disposed is True
