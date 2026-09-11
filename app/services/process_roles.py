from __future__ import annotations

from collections.abc import Mapping

from app.settings import ProcessRole, RuntimeMode, Settings

PROCESS_ROLE_ENV = "PROCESS_ROLE"


def build_process_environment(
    parent: Mapping[str, str],
    *,
    role: ProcessRole,
    settings: Settings,
) -> dict[str, str]:
    """Return an isolated, normalized environment for one runtime child.

    Runtime-critical values are serialized from the already validated Settings
    object. A sibling's overrides can therefore never leak through a shared
    mutable environment, and stale shell values cannot redefine a child's role.
    """

    environment = dict(parent)
    environment.update(
        {
            PROCESS_ROLE_ENV: role.value,
            "RUNTIME_MODE": settings.runtime_mode.value,
            "USE_DURABLE_PIPELINE": _bool(settings.use_durable_pipeline),
            "DURABLE_WORKER_PROCESS_ENABLED": _bool(settings.durable_worker_process_enabled),
            "EMBEDDED_JOB_WORKER_ENABLED": _bool(settings.embedded_job_worker_enabled),
            # Deprecated compatibility input. It is deliberately disabled in
            # every child; no runtime decision is allowed to depend on it.
            "JOB_WORKER_ENABLED": "false",
            "WINNER_PROBABILITY_AUTO_MATURATION_ENABLED": _bool(
                settings.winner_probability_auto_maturation_enabled
            ),
            "WINNER_PROBABILITY_AUTO_COHORT_REFRESH_ENABLED": _bool(
                settings.winner_probability_auto_cohort_refresh_enabled
            ),
            "MARKET_DATA_PREWARM_ENABLED": _bool(settings.market_data_prewarm_enabled),
        }
    )
    return environment


def require_process_role(settings: Settings, expected: ProcessRole) -> None:
    runtime_mode = getattr(settings, "runtime_mode", RuntimeMode.NORMAL)
    process_role = getattr(settings, "process_role", ProcessRole.CLI_OR_MAINTENANCE)
    if runtime_mode is RuntimeMode.NORMAL and process_role is ProcessRole.CLI_OR_MAINTENANCE:
        # Preserve direct developer entry points in NORMAL mode. Canonical and
        # all CERTIFICATION launches always carry an explicit role.
        return
    if process_role is not expected:
        raise RuntimeError(
            f"{expected.value} entry point requires PROCESS_ROLE={expected.value}; "
            f"received {process_role.value}"
        )


def certification_profile_environment(parent: Mapping[str, str]) -> dict[str, str]:
    """Canonical, fail-closed operator profile used before Settings is parsed."""

    environment = dict(parent)
    environment.update(
        {
            PROCESS_ROLE_ENV: ProcessRole.CLI_OR_MAINTENANCE.value,
            "RUNTIME_MODE": RuntimeMode.CERTIFICATION.value,
            "USE_DURABLE_PIPELINE": "true",
            "DURABLE_WORKER_PROCESS_ENABLED": "true",
            "EMBEDDED_JOB_WORKER_ENABLED": "false",
            "JOB_WORKER_ENABLED": "false",
            "WINNER_PROBABILITY_AUTO_MATURATION_ENABLED": "false",
            "WINNER_PROBABILITY_AUTO_COHORT_REFRESH_ENABLED": "false",
            "MARKET_DATA_PREWARM_ENABLED": "false",
        }
    )
    return environment


def _bool(value: bool) -> str:
    return "true" if value else "false"
