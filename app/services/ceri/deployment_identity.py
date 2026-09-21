from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.alembic_heads import repository_alembic_heads

CERI_EVIDENCE_SCHEMA_REVISION = "ceri-decision-evidence-v1"


class DeploymentSchemaMismatch(RuntimeError):
    pass


def session_database_schema_revision(db: Any) -> str | None:
    """Read the deployed head for real sessions; test adapters use compiled identity."""

    if not isinstance(db, Session):
        return None
    value = db.scalar(text("select version_num from alembic_version"))
    if value in (None, ""):
        raise DeploymentSchemaMismatch("CERI_DEPLOYMENT_SCHEMA_UNAVAILABLE")
    return str(value)


def build_deployment_identity(
    *,
    git_sha: str | None,
    dirty: bool | None,
    image_digest: str | None,
    schema_revision: str,
    config_hash: str | None,
    calculation_version: str | None,
    provider_signatures: dict[str, str],
) -> dict[str, Any]:
    return {
        "git_sha": git_sha,
        "git_dirty": dirty,
        "image_digest": image_digest,
        "schema_revision": schema_revision,
        "config_hash": config_hash,
        "calculation_version": calculation_version,
        "provider_signatures": dict(sorted(provider_signatures.items())),
    }


def current_deployment_identity(
    *,
    config_hash: str | None,
    calculation_version: str | None,
    provider_signatures: dict[str, str] | None = None,
    database_schema_revision: str | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    base = _local_identity()
    authoritative_repo_root = repo_root or Path(__file__).resolve().parents[3]
    heads = repository_alembic_heads(authoritative_repo_root)
    if len(heads) != 1:
        raise DeploymentSchemaMismatch(
            f"CERI_DEPLOYMENT_SCHEMA_HEAD_AMBIGUOUS: expected one head, got {heads}"
        )
    expected_revision = heads[0]
    actual_revision = database_schema_revision or expected_revision
    if actual_revision != expected_revision:
        raise DeploymentSchemaMismatch(
            "CERI_DEPLOYMENT_SCHEMA_MISMATCH: "
            f"database={actual_revision}; repository={expected_revision}"
        )
    identity = build_deployment_identity(
        git_sha=base[0],
        dirty=base[1],
        image_digest=base[2],
        schema_revision=actual_revision,
        config_hash=config_hash,
        calculation_version=calculation_version,
        provider_signatures=provider_signatures or {},
    )
    identity.update(
        {
            "database_schema_revision": actual_revision,
            "repository_schema_revision": expected_revision,
            "schema_revision_match": True,
            "ceri_evidence_schema_revision": CERI_EVIDENCE_SCHEMA_REVISION,
        }
    )
    return identity


@lru_cache(maxsize=1)
def _local_identity() -> tuple[str | None, bool | None, str | None]:
    sha = os.getenv("GIT_SHA")
    dirty: bool | None = None
    try:
        if not sha:
            sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                timeout=3,
            ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                check=True,
                capture_output=True,
                text=True,
                timeout=3,
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError):
        dirty = None
    return sha, dirty, os.getenv("IMAGE_DIGEST")
