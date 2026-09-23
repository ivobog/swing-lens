"""Resolve a dump client compatible with the connected PostgreSQL server."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool


def server_major(database_url: str) -> int:
    engine = create_engine(database_url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            return int(connection.scalar(text("show server_version_num"))) // 10000
    finally:
        engine.dispose()


def candidate_paths(tool: str, major: int, explicit_bin: str | None = None) -> list[Path]:
    candidates: list[Path] = []
    executable = f"{tool}.exe" if os.name == "nt" else tool
    if explicit_bin:
        candidates.append(Path(explicit_bin) / executable)
    on_path = shutil.which(tool)
    if on_path:
        candidates.append(Path(on_path))
    for environment in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(environment)
        if base:
            candidates.append(Path(base) / "PostgreSQL" / str(major) / "bin" / executable)
    candidates.extend(
        [
            Path(f"/usr/lib/postgresql/{major}/bin/{tool}"),
            Path(f"/usr/local/pgsql/bin/{tool}"),
        ]
    )
    return list(dict.fromkeys(candidates))


def resolve_tool(tool: str, major: int, explicit_bin: str | None = None) -> tuple[Path, int]:
    incompatible = []
    for path in candidate_paths(tool, major, explicit_bin):
        if not path.is_file():
            continue
        result = subprocess.run(
            [str(path), "--version"], capture_output=True, text=True, check=False
        )
        match = re.search(r"\b(\d+)(?:\.\d+)?\b", result.stdout)
        if result.returncode != 0 or match is None:
            incompatible.append(f"{path}: unparseable version")
            continue
        client_major = int(match.group(1))
        if client_major == major:
            return path.resolve(), client_major
        incompatible.append(f"{path}: major {client_major}")
    raise RuntimeError(
        f"No PostgreSQL {major} {tool} client found; checked explicit bin, PATH, "
        f"and installed PostgreSQL locations. Incompatible: {incompatible}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--postgres-bin")
    parser.add_argument("--tool", choices=("pg_dump", "pg_restore", "psql"), default="pg_dump")
    args = parser.parse_args()
    major = server_major(args.database_url)
    path, client_major = resolve_tool(args.tool, major, args.postgres_bin)
    print(json.dumps({"path": str(path), "server_major": major, "client_major": client_major}))


if __name__ == "__main__":
    main()
