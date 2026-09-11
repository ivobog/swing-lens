from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import psycopg
from alembic.config import Config
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from alembic import command
from app.database_safety import configure_guarded_alembic
from app.services.ceri.sec.processor_lifecycle import (
    certify_processor,
    promote_processor,
    register_deployed_processor,
)
from app.services.ceri.sec.processor_signature import (
    SEC_PROCESSOR_SIGNATURE_ALGORITHM_VERSION,
    sec_guidance_processor_identity_inputs,
    sec_guidance_processor_signature,
)
from app.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
PREFIX = "swinglens_qa_sec_contract_"
CLONE_NAMES = tuple(f"{PREFIX}clone_{suffix}" for suffix in ("a", "b", "c"))
BOOTSTRAP_NAMES = tuple(f"{PREFIX}bootstrap_{suffix}" for suffix in ("a", "b", "c"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only production parity and disposable SEC bootstrap certification."
    )
    parser.add_argument("--keep-databases", action="store_true")
    args = parser.parse_args()

    settings = Settings()
    production_url = make_url(settings.database_url)
    if production_url.database != "swinglens":
        raise RuntimeError("The configured production database is not the expected swinglens DB.")
    admin_url = production_url.set(database="postgres")
    pg_bin = settings.swinglens_postgres_executable.parent
    pg_dump = pg_bin / "pg_dump.exe"
    pg_restore = pg_bin / "pg_restore.exe"
    if not pg_dump.is_file() or not pg_restore.is_file():
        raise RuntimeError("PostgreSQL pg_dump/pg_restore executables were not found.")

    targets = (*CLONE_NAMES, *BOOTSTRAP_NAMES)
    _drop_targets(admin_url, targets)
    before = _snapshot(production_url, read_only=True)
    try:
        with tempfile.TemporaryDirectory(prefix="swinglens-sec-contract-") as temp_dir:
            dump_path = Path(temp_dir) / "production.dump"
            _dump_production(production_url, pg_dump, dump_path)
            _create_database(admin_url, CLONE_NAMES[0])
            _restore_clone(production_url, pg_restore, dump_path, CLONE_NAMES[0])
            for name in CLONE_NAMES[1:]:
                _create_database(admin_url, name, template=CLONE_NAMES[0])

        clone_snapshots = [
            _snapshot(production_url.set(database=name), read_only=True) for name in CLONE_NAMES
        ]

        bootstrap_snapshots: list[dict[str, Any]] = []
        for name in BOOTSTRAP_NAMES:
            candidate = production_url.set(database=name)
            _create_database(admin_url, name)
            _migrate(candidate)
            migration_only = _snapshot(candidate, read_only=True)
            activated = _activate(candidate)
            bootstrap_snapshots.append(
                {"database": name, "migration_only": migration_only, "activated": activated}
            )

        after = _snapshot(production_url, read_only=True)
        result = {
            "signature_algorithm_version": SEC_PROCESSOR_SIGNATURE_ALGORITHM_VERSION,
            "code_signature": sec_guidance_processor_signature(),
            "identity_inputs": list(sec_guidance_processor_identity_inputs()),
            "production_before": before,
            "production_after": after,
            "production_unchanged": before["metadata_sha256"] == after["metadata_sha256"],
            "clones": clone_snapshots,
            "clones_match_production": all(
                item["metadata_sha256"] == before["metadata_sha256"] for item in clone_snapshots
            ),
            "bootstraps": bootstrap_snapshots,
            "bootstrap_active_signatures": [
                item["activated"]["active_signature"] for item in bootstrap_snapshots
            ],
        }
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
    finally:
        if not args.keep_databases:
            _drop_targets(admin_url, targets)
    return 0


def _snapshot(database_url: URL, *, read_only: bool) -> dict[str, Any]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            if read_only:
                connection.execute(text("set transaction read only"))
            revision = str(
                connection.execute(text("select version_num from alembic_version")).scalar_one()
            )
            rows = connection.execute(
                text(
                    "select processor_signature, status, deployed_git_sha, "
                    "certification_evidence_json, created_at, certified_at, certified_by, "
                    "activated_at, activated_by, retired_at "
                    "from ceri_sec_processor_releases order by processor_signature"
                )
            ).mappings()
            releases = [_jsonable(dict(row)) for row in rows]
            connection.rollback()
    finally:
        engine.dispose()
    canonical = json.dumps(releases, sort_keys=True, separators=(",", ":"), default=str)
    active = [row for row in releases if row["status"] == "ACTIVE"]
    summarized_releases = []
    for row in releases:
        summary = {key: value for key, value in row.items() if key != "certification_evidence_json"}
        evidence = json.dumps(
            row["certification_evidence_json"],
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        summary["certification_evidence_sha256"] = hashlib.sha256(
            evidence.encode("utf-8")
        ).hexdigest()
        summarized_releases.append(summary)
    return {
        "alembic_revision": revision,
        "release_count": len(releases),
        "active_signature": active[0]["processor_signature"] if len(active) == 1 else None,
        "active_count": len(active),
        "releases": summarized_releases,
        "metadata_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def _activate(database_url: URL) -> dict[str, Any]:
    engine = create_engine(database_url)
    signature = sec_guidance_processor_signature()
    try:
        with Session(engine) as session:
            register_deployed_processor(session, git_sha=_git_head())
            certify_processor(
                session,
                processor_signature=signature,
                evidence={"scope": "disposable-sec-contract-certification"},
                actor="codex-sec-cert-20260911",
            )
            promote_processor(
                session,
                processor_signature=signature,
                actor="codex-sec-cert-20260911",
            )
            session.commit()
    finally:
        engine.dispose()
    return _snapshot(database_url, read_only=True)


def _migrate(database_url: URL) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    configure_guarded_alembic(config, database_url)
    command.upgrade(config, "head")


def _dump_production(production_url: URL, executable: Path, dump_path: Path) -> None:
    environment = _postgres_environment(production_url)
    command_line = [
        str(executable),
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--file",
        str(dump_path),
        "--host",
        str(production_url.host or "127.0.0.1"),
        "--port",
        str(production_url.port or 5432),
        "--username",
        str(production_url.username or "postgres"),
        str(production_url.database),
    ]
    subprocess.run(command_line, env=environment, check=True, cwd=ROOT)


def _restore_clone(
    production_url: URL,
    executable: Path,
    dump_path: Path,
    database_name: str,
) -> None:
    environment = _postgres_environment(production_url)
    command_line = [
        str(executable),
        "--exit-on-error",
        "--no-owner",
        "--no-privileges",
        "--jobs=4",
        "--host",
        str(production_url.host or "127.0.0.1"),
        "--port",
        str(production_url.port or 5432),
        "--username",
        str(production_url.username or "postgres"),
        "--dbname",
        database_name,
        str(dump_path),
    ]
    subprocess.run(command_line, env=environment, check=True, cwd=ROOT)


def _create_database(admin_url: URL, name: str, *, template: str | None = None) -> None:
    _assert_target(name)
    with psycopg.connect(_psycopg_url(admin_url), autocommit=True) as connection:
        if template is None:
            statement = sql.SQL("create database {}").format(sql.Identifier(name))
        else:
            _assert_target(template)
            statement = sql.SQL("create database {} template {}").format(
                sql.Identifier(name), sql.Identifier(template)
            )
        connection.execute(statement)


def _drop_targets(admin_url: URL, names: tuple[str, ...]) -> None:
    for name in names:
        _assert_target(name)
    with psycopg.connect(_psycopg_url(admin_url), autocommit=True) as connection:
        for name in names:
            connection.execute(
                sql.SQL("drop database if exists {} with (force)").format(sql.Identifier(name))
            )


def _assert_target(name: str) -> None:
    if not name.startswith(PREFIX) or name not in {*CLONE_NAMES, *BOOTSTRAP_NAMES}:
        raise RuntimeError(f"Refusing unsafe disposable database target: {name}")


def _postgres_environment(database_url: URL) -> dict[str, str]:
    environment = os.environ.copy()
    if database_url.password:
        environment["PGPASSWORD"] = database_url.password
    return environment


def _psycopg_url(database_url: URL) -> str:
    return database_url.set(drivername="postgresql").render_as_string(hide_password=False)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
