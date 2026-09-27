"""Fail-closed, read-only admission check for a small provider canary.

The checker validates repository, architecture, runtime-policy, PostgreSQL, and
durable-queue state. It never creates a certification root or changes database
state. Callers must supply the exact Git commit and certification session they
intend to use so a stale shell cannot silently certify a different generation.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import ipaddress
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import SessionLocal  # noqa: E402
from app.services.background_job_service import TERMINAL_JOB_STATUSES  # noqa: E402
from app.services.certification_runtime import (  # noqa: E402
    certification_claimable_job_ids,
    queue_isolation_status,
)
from app.settings import RuntimeMode, Settings  # noqa: E402
from scripts.check_architecture_registry import main as architecture_registry_main  # noqa: E402

ACTIVE_JOB_STATUSES = ("QUEUED", "RUNNING", "STALLED", "RECOVERING", "RETRYING")
REQUIRED_CHECKS = (
    "expected_head",
    "tracked_worktree_clean",
    "architecture_registry",
    "runtime_mode_policy",
    "certification_session",
    "database_identity",
    "postgresql_health",
    "alembic_head",
    "queue_isolation",
    "historical_rows_terminal",
    "active_stale_leases",
    "certification_claim_set_empty",
    "unauthorized_autonomous_activity",
)


@dataclass(frozen=True)
class ReadinessCheck:
    passed: bool
    detail: Any


def finalize_report(checks: dict[str, ReadinessCheck]) -> dict[str, Any]:
    """Return a stable verdict and fail closed when a required check is absent."""
    complete = dict(checks)
    for name in REQUIRED_CHECKS:
        complete.setdefault(name, ReadinessCheck(False, "check was not executed"))
    ready = all(complete[name].passed for name in REQUIRED_CHECKS)
    return {
        "ready": ready,
        "verdict": "PASS" if ready else "FAIL",
        "checks": {name: asdict(check) for name, check in sorted(complete.items())},
    }


def collect_report(
    *,
    expected_head: str,
    certification_session_id: str,
    settings: Settings,
) -> dict[str, Any]:
    checks: dict[str, ReadinessCheck] = {}
    actual_head = _git("rev-parse", "HEAD")
    checks["expected_head"] = ReadinessCheck(
        actual_head == expected_head,
        {"expected": expected_head, "actual": actual_head},
    )
    tracked_clean = _git_status("diff", "--quiet", "HEAD", "--") == 0
    checks["tracked_worktree_clean"] = ReadinessCheck(
        tracked_clean,
        "no tracked source drift" if tracked_clean else "tracked source differs from HEAD",
    )
    checks["architecture_registry"] = _architecture_check()
    checks["runtime_mode_policy"] = _runtime_policy_check(settings)
    configured_session = str(settings.runtime_instance_id or "").strip()
    checks["certification_session"] = ReadinessCheck(
        bool(certification_session_id)
        and configured_session == certification_session_id,
        {
            "supplied": certification_session_id,
            "configured": configured_session or None,
        },
    )

    source_heads = _source_alembic_heads()
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        observed_at = datetime.now(UTC)
        identity = db.execute(
            text(
                """
                SELECT current_database() AS database,
                       current_user AS username,
                       inet_server_addr()::text AS server_address,
                       inet_server_port() AS server_port,
                       current_setting('server_version') AS server_version,
                       pg_postmaster_start_time() AS postmaster_start_time,
                       pg_is_in_recovery() AS in_recovery
                """
            )
        ).mappings().one()
        checks["database_identity"] = _database_identity_check(settings, dict(identity))
        checks["postgresql_health"] = ReadinessCheck(
            identity["in_recovery"] is False,
            {
                "server_version": identity["server_version"],
                "postmaster_start_time": identity["postmaster_start_time"].isoformat(),
                "in_recovery": identity["in_recovery"],
            },
        )
        database_heads = tuple(
            db.execute(text("SELECT version_num FROM alembic_version ORDER BY version_num"))
            .scalars()
            .all()
        )
        checks["alembic_head"] = ReadinessCheck(
            database_heads == source_heads,
            {"source": source_heads, "database": database_heads},
        )

        isolation = queue_isolation_status(db, now=observed_at)
        checks["queue_isolation"] = ReadinessCheck(isolation.isolated, isolation.to_dict())
        nonterminal = {
            str(row.status): int(row.count)
            for row in db.execute(
                text(
                    """
                    SELECT status, count(*)::int AS count
                    FROM background_jobs
                    WHERE status <> ALL(:terminal_statuses)
                    GROUP BY status ORDER BY status
                    """
                ),
                {"terminal_statuses": sorted(TERMINAL_JOB_STATUSES)},
            ).mappings()
        }
        checks["historical_rows_terminal"] = ReadinessCheck(
            not nonterminal,
            {"nonterminal_status_counts": nonterminal},
        )
        stale_leases = int(
            db.execute(
                text(
                    """
                    SELECT count(*)
                    FROM background_jobs
                    WHERE status IN ('RUNNING', 'RECOVERING')
                      AND (lease_expires_at IS NULL OR lease_expires_at <= :observed_at)
                    """
                ),
                {"observed_at": observed_at},
            ).scalar_one()
        )
        checks["active_stale_leases"] = ReadinessCheck(
            stale_leases == 0,
            {"count": stale_leases},
        )
        claimable = certification_claimable_job_ids(
            db,
            certification_session_id=certification_session_id,
            now=observed_at,
        )
        checks["certification_claim_set_empty"] = ReadinessCheck(
            not claimable,
            {"job_ids": claimable},
        )
        unauthorized = int(
            db.execute(
                text(
                    """
                    SELECT count(*)
                    FROM background_jobs
                    WHERE status = ANY(:active_statuses)
                      AND NOT (
                        coalesce(payload_json ->> 'certification_authorized', 'false') = 'true'
                        AND payload_json ->> 'certification_session_id' = :session_id
                      )
                    """
                ),
                {
                    "active_statuses": list(ACTIVE_JOB_STATUSES),
                    "session_id": certification_session_id,
                },
            ).scalar_one()
        )
        live_worker_cutoff = observed_at - timedelta(
            seconds=settings.job_worker_heartbeat_timeout_seconds
        )
        live_workers = int(
            db.execute(
                text(
                    """
                    SELECT count(*) FROM background_workers
                    WHERE stopping_at IS NULL AND heartbeat_at >= :cutoff
                    """
                ),
                {"cutoff": live_worker_cutoff},
            ).scalar_one()
        )
        checks["unauthorized_autonomous_activity"] = ReadinessCheck(
            unauthorized == 0 and live_workers <= 1,
            {
                "unauthorized_active_jobs": unauthorized,
                "live_durable_workers": live_workers,
                "maximum_live_durable_workers": 1,
            },
        )
        db.rollback()

    report = finalize_report(checks)
    report.update(
        {
            "checked_at": datetime.now(UTC).isoformat(),
            "expected_head": expected_head,
            "certification_session_id": certification_session_id,
            "read_only": True,
        }
    )
    return report


def _architecture_check() -> ReadinessCheck:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        result = architecture_registry_main(["--check"])
    detail = (stdout.getvalue() or stderr.getvalue()).strip()
    return ReadinessCheck(result == 0, detail)


def _runtime_policy_check(settings: Settings) -> ReadinessCheck:
    values = {
        "runtime_mode": settings.runtime_mode.value,
        "use_durable_pipeline": settings.use_durable_pipeline,
        "durable_worker_process_enabled": settings.durable_worker_process_enabled,
        "embedded_job_worker_enabled": settings.embedded_job_worker_enabled,
        "legacy_job_worker_enabled": settings.job_worker_enabled,
        "winner_auto_maturation": settings.winner_probability_auto_maturation_enabled,
        "winner_auto_cohort_refresh": settings.winner_probability_auto_cohort_refresh_enabled,
        "market_data_prewarm": settings.market_data_prewarm_enabled,
    }
    passed = (
        settings.runtime_mode is RuntimeMode.CERTIFICATION
        and settings.use_durable_pipeline
        and settings.durable_worker_process_enabled
        and not settings.embedded_job_worker_enabled
        and not settings.job_worker_enabled
        and not settings.winner_probability_auto_maturation_enabled
        and not settings.winner_probability_auto_cohort_refresh_enabled
        and not settings.market_data_prewarm_enabled
    )
    return ReadinessCheck(passed, values)


def _database_identity_check(settings: Settings, identity: dict[str, Any]) -> ReadinessCheck:
    configured = make_url(settings.database_url)
    server_address = str(identity["server_address"]).split("/", 1)[0]
    try:
        loopback = ipaddress.ip_address(server_address).is_loopback
    except ValueError:
        loopback = False
    expected_port = configured.port or 5432
    passed = (
        configured.database == identity["database"]
        and configured.host in {"127.0.0.1", "localhost", "::1"}
        and loopback
        and expected_port == identity["server_port"]
    )
    return ReadinessCheck(
        passed,
        {
            "configured_host": configured.host,
            "configured_port": expected_port,
            "configured_database": configured.database,
            "server_address": identity["server_address"],
            "server_port": identity["server_port"],
            "current_database": identity["database"],
            "username": identity["username"],
        },
    )


def _source_alembic_heads() -> tuple[str, ...]:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    return tuple(sorted(ScriptDirectory.from_config(config).get_heads()))


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _git_status(*args: str) -> int:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--certification-session-id", required=True)
    args = parser.parse_args(argv)
    try:
        settings = Settings()
        report = collect_report(
            expected_head=args.expected_head.strip(),
            certification_session_id=args.certification_session_id.strip(),
            settings=settings,
        )
    except Exception as exc:  # Fail closed while keeping machine-readable output.
        report = {
            "ready": False,
            "verdict": "FAIL",
            "read_only": True,
            "error": f"{type(exc).__name__}: {exc}",
        }
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0 if report.get("ready") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
