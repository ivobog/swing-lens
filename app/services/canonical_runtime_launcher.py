from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from app.services.lifecycle_control import GIT_SHA_ENV, RUNTIME_INSTANCE_ID_ENV
from app.services.process_roles import build_process_environment
from app.settings import ProcessRole, Settings


@dataclass(frozen=True)
class CanonicalRuntimeLaunch:
    """One production-equivalent supervisor-root command and child environment."""

    command: tuple[str, ...]
    environment: dict[str, str]
    runtime_instance_id: str


def build_canonical_runtime_launch(
    *,
    settings: Settings,
    parent_environment: Mapping[str, str],
    repo_root: Path,
    git_sha: str,
    runtime_instance_id: str | None = None,
    worker_id: str | None = None,
    queues: str = "interactive,broker,background",
) -> CanonicalRuntimeLaunch:
    """Build the exact supervisor-root boundary shared by lifecycle and certification."""

    if not settings.use_durable_pipeline or not settings.durable_worker_process_enabled:
        raise ValueError(
            "canonical supervisor-root launch requires the durable pipeline and worker"
        )
    if settings.embedded_job_worker_enabled:
        raise ValueError("canonical supervisor-root launch forbids embedded worker ownership")
    instance = runtime_instance_id or uuid4().hex
    environment = build_process_environment(
        parent_environment,
        role=ProcessRole.SUPERVISOR,
        settings=settings,
    )
    environment[RUNTIME_INSTANCE_ID_ENV] = instance
    environment[GIT_SHA_ENV] = git_sha
    command = (
        os.fspath(Path(sys.executable)),
        "-m",
        "app.worker_supervisor",
        "--worker-id",
        worker_id or settings.job_worker_id,
        "--queues",
        queues,
        "--host",
        settings.app_host,
        "--port",
        str(settings.app_port),
        "--runtime-instance-id",
        instance,
        "--repo-root",
        str(repo_root.resolve()),
    )
    return CanonicalRuntimeLaunch(command, environment, instance)
