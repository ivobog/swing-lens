from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from typing import Any


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
) -> dict[str, Any]:
    base = _local_identity()
    return build_deployment_identity(
        git_sha=base[0],
        dirty=base[1],
        image_digest=base[2],
        schema_revision="0043_ceri_run102_relative_evidence",
        config_hash=config_hash,
        calculation_version=calculation_version,
        provider_signatures=provider_signatures or {},
    )


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
