from __future__ import annotations

import csv
import os
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql

from app.main import create_app
from app.settings import Settings

POSTGRES_ADMIN_URL = "postgresql://postgres:postgres@127.0.0.1:5432/postgres"
DISPOSABLE_DATABASE_PREFIX = "swinglens_pytest_"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Give every test an explicit QA lane without rewriting legacy modules."""
    for item in items:
        path = Path(str(item.path)).as_posix().lower()
        name = item.name.lower()
        assigned_level = False

        if "/e2e/" in path or "webapp_fix_flows" in path:
            item.add_marker(pytest.mark.e2e)
            item.add_marker(pytest.mark.slow)
            assigned_level = True
        elif "/integration/" in path or "migration_remediation" in path:
            item.add_marker(pytest.mark.integration)
            assigned_level = True

        if not assigned_level:
            item.add_marker(pytest.mark.unit)

        if "performance" in path or "benchmark" in name:
            item.add_marker(pytest.mark.performance)
            item.add_marker(pytest.mark.slow)
        if "migration" in path or "purge" in path or "restore" in path:
            item.add_marker(pytest.mark.destructive)
        if any(token in path for token in ("security", "no_order", "redaction")):
            item.add_marker(pytest.mark.security)


@dataclass(frozen=True)
class QaPaths:
    root: Path
    uploads: Path
    exports: Path
    cache: Path


@dataclass(frozen=True)
class FrozenClock:
    instant: datetime

    def now(self) -> datetime:
        return self.instant

    def today(self) -> date:
        return self.instant.date()


class ScriptedIBGateway:
    """Read-only deterministic IB double with scripted ticker outcomes."""

    def __init__(self, outcomes: dict[str, list[Any] | Exception] | None = None) -> None:
        self.outcomes = outcomes or {}
        self.connected = False
        self.connect_calls: list[dict[str, Any]] = []
        self.historical_requests: list[str] = []
        self.order_api_calls: list[str] = []

    def connect(self, *_args: Any, **kwargs: Any) -> None:
        if kwargs.get("readonly") is not True:
            raise AssertionError("QA IB sessions must be read-only")
        self.connect_calls.append(dict(kwargs))
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def isConnected(self) -> bool:  # noqa: N802 - mirrors ib_insync
        return self.connected

    def reqHistoricalData(self, contract: Any, *_args: Any, **_kwargs: Any) -> list[Any]:  # noqa: N802
        ticker = str(getattr(contract, "symbol", contract)).upper()
        self.historical_requests.append(ticker)
        outcome = self.outcomes.get(ticker, [])
        if isinstance(outcome, Exception):
            raise outcome
        return list(outcome)

    def __getattr__(self, name: str) -> Any:
        if "order" in name.lower():
            self.order_api_calls.append(name)
            raise AssertionError(f"broker order API must not be called: {name}")
        raise AttributeError(name)


@pytest.fixture
def qa_paths(tmp_path: Path) -> QaPaths:
    paths = QaPaths(
        root=tmp_path,
        uploads=tmp_path / "uploads",
        exports=tmp_path / "exports",
        cache=tmp_path / "cache",
    )
    for directory in (paths.uploads, paths.exports, paths.cache):
        directory.mkdir()
    return paths


@pytest.fixture
def fixed_clock() -> FrozenClock:
    return FrozenClock(datetime(2026, 8, 5, 20, 0, tzinfo=UTC))


@pytest.fixture
def settings_factory(qa_paths: QaPaths) -> Callable[..., Settings]:
    def build(**overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "_env_file": None,
            "upload_dir": qa_paths.uploads,
            "export_dir": qa_paths.exports,
            "cache_dir": qa_paths.cache,
            "job_worker_enabled": False,
        }
        values.update(overrides)
        return Settings(**values)

    return build


@pytest.fixture
def app_client_factory(
    settings_factory: Callable[..., Settings],
) -> Callable[..., TestClient]:
    def build(**overrides: Any) -> TestClient:
        return TestClient(create_app(settings_factory(**overrides)))

    return build


@pytest.fixture
def csv_factory(tmp_path: Path) -> Callable[..., Path]:
    def build(
        rows: list[dict[str, Any]],
        *,
        headers: list[str] | None = None,
        filename: str = "fixture.csv",
        delimiter: str = ",",
        encoding: str = "utf-8",
    ) -> Path:
        if headers is None:
            headers = list(rows[0]) if rows else ["Symbol"]
        path = tmp_path / filename
        with path.open("w", encoding=encoding, newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers, delimiter=delimiter)
            writer.writeheader()
            writer.writerows(rows)
        return path

    return build


@pytest.fixture
def ohlcv_factory() -> Callable[..., list[dict[str, Any]]]:
    def build(
        *,
        count: int = 300,
        start: date = date(2025, 1, 2),
        start_close: float = 100.0,
        daily_step: float = 0.25,
        volume: int = 1_000_000,
    ) -> list[dict[str, Any]]:
        bars: list[dict[str, Any]] = []
        current = start
        close = start_close
        while len(bars) < count:
            if current.weekday() < 5:
                bars.append(
                    {
                        "date": current,
                        "open": round(close - 0.2, 4),
                        "high": round(close + 1.0, 4),
                        "low": round(close - 1.0, 4),
                        "close": round(close, 4),
                        "volume": volume + len(bars) * 100,
                    }
                )
                close += daily_step
            current += timedelta(days=1)
        return bars

    return build


@pytest.fixture
def fake_ib_gateway_factory() -> Callable[..., ScriptedIBGateway]:
    return lambda outcomes=None: ScriptedIBGateway(outcomes)


@pytest.fixture
def disposable_postgres_database() -> Iterator[str]:
    """Create a safely named empty PostgreSQL database and drop only that database."""
    admin_url = os.environ.get("SWINGLENS_TEST_POSTGRES_ADMIN_URL", POSTGRES_ADMIN_URL)
    database_name = f"{DISPOSABLE_DATABASE_PREFIX}{uuid.uuid4().hex[:12]}"
    try:
        admin = psycopg.connect(admin_url, autocommit=True)
    except psycopg.Error as exc:
        pytest.skip(f"PostgreSQL admin database unavailable: {exc}")

    try:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
        yield f"postgresql+psycopg://postgres:postgres@127.0.0.1:5432/{database_name}"
    finally:
        admin.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                sql.Identifier(database_name)
            )
        )
        admin.close()
