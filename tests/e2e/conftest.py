from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

REPO_ROOT = Path(__file__).resolve().parents[2]
POSTGRES_ADMIN_URL = "postgresql://postgres:postgres@127.0.0.1:5432/postgres"


def _available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@pytest.fixture(scope="session")
def live_server_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Run a migrated SwingLens instance against a disposable PostgreSQL database."""
    database_name = f"swinglens_pytest_browser_{uuid.uuid4().hex[:12]}"
    admin_url = os.environ.get("SWINGLENS_TEST_POSTGRES_ADMIN_URL", POSTGRES_ADMIN_URL)
    try:
        admin = psycopg.connect(admin_url, autocommit=True)
    except psycopg.Error as exc:
        pytest.fail(f"Browser tests require disposable PostgreSQL: {exc}")

    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    database_url = f"postgresql+psycopg://postgres:postgres@127.0.0.1:5432/{database_name}"
    runtime_root = tmp_path_factory.mktemp("swinglens-browser")
    env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "APP_HOST": "127.0.0.1",
        "JOB_WORKER_ENABLED": "false",
        "UPLOAD_DIR": str(runtime_root / "uploads"),
        "EXPORT_DIR": str(runtime_root / "exports"),
        "CACHE_DIR": str(runtime_root / "cache"),
        "WINNER_PROBABILITY_ENABLED": "false",
        "SETUP_LIFECYCLE_ENABLED": "false",
        "CERI_ENABLED": "false",
    }
    migration = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if migration.returncode != 0:
        admin.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                sql.Identifier(database_name)
            )
        )
        admin.close()
        pytest.fail(migration.stdout + migration.stderr)

    port = _available_port()
    base_url = f"http://127.0.0.1:{port}"
    artifact_dir = REPO_ROOT / "output" / "playwright"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    log_path = artifact_dir / "uvicorn-browser.log"
    log_handle = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=REPO_ROOT,
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                log_handle.flush()
                pytest.fail(f"SwingLens browser server stopped early; see {log_path}")
            try:
                with urllib.request.urlopen(f"{base_url}/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except (OSError, urllib.error.URLError):
                time.sleep(0.2)
        else:
            pytest.fail(f"SwingLens browser server did not become healthy; see {log_path}")

        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log_handle.close()
        admin.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                sql.Identifier(database_name)
            )
        )
        admin.close()
