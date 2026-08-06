from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "ops" / "PostgresUrl.psm1"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "postgresql+psycopg://qa:secret@127.0.0.1:5432/swinglens",
            "postgresql://qa:secret@127.0.0.1:5432/swinglens",
        ),
        (
            "postgresql+psycopg2://qa:secret@db.example:5432/swinglens?sslmode=require",
            "postgresql://qa:secret@db.example:5432/swinglens?sslmode=require",
        ),
        (
            "postgresql://qa:secret@127.0.0.1:5432/swinglens",
            "postgresql://qa:secret@127.0.0.1:5432/swinglens",
        ),
    ],
)
def test_postgres_client_url_conversion(source: str, expected: str) -> None:
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.fail("PowerShell is required to validate PostgreSQL operations scripts")
    command = (
        f"Import-Module '{MODULE_PATH}' -Force; "
        f"ConvertTo-PostgresClientUrl -DatabaseUrl '{source}'"
    )

    result = subprocess.run(
        [powershell, "-NoProfile", "-Command", command],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


def test_backup_and_restore_scripts_use_normalized_client_urls() -> None:
    backup = (REPO_ROOT / "scripts" / "ops" / "backup_postgres.ps1").read_text(
        encoding="utf-8"
    )
    restore = (REPO_ROOT / "scripts" / "ops" / "restore_postgres.ps1").read_text(
        encoding="utf-8"
    )

    assert "ConvertTo-PostgresClientUrl -DatabaseUrl $DatabaseUrl" in backup
    assert "$clientDatabaseUrl" in backup
    assert "ConvertTo-PostgresClientUrl -DatabaseUrl $RestoreDatabaseUrl" in restore
    assert "$clientRestoreDatabaseUrl" in restore
