from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from app.services.redaction import redact_text
from app.settings import Settings


def _settings() -> Settings:
    # Constructing Settings does not create directories, so status stays read-only.
    return Settings()


def _database_report() -> dict[str, object]:
    settings = _settings()
    url = make_url(settings.database_url)
    report: dict[str, object] = {
        "scheme": url.drivername,
        "host": url.host or "",
        "port": url.port or 5432,
        "database": url.database or "",
        "reachable": False,
        "dataDirectory": None,
        "serverVersion": None,
        "currentHeads": [],
        "expectedHeads": _alembic_heads(),
        "schemaAtHead": False,
        "useDurablePipeline": settings.use_durable_pipeline,
    }
    engine = None
    try:
        engine = create_engine(
            settings.database_url,
            poolclass=NullPool,
            connect_args={"connect_timeout": settings.database_connect_timeout_seconds},
        )
        with engine.connect() as connection:
            connection.execute(text("select 1"))
            row = connection.execute(
                text(
                    "select current_setting('data_directory'), "
                    "current_setting('server_version')"
                )
            ).one()
            report["reachable"] = True
            report["dataDirectory"] = str(row[0])
            report["serverVersion"] = str(row[1])
            if "alembic_version" in inspect(connection).get_table_names():
                report["currentHeads"] = sorted(
                    str(item[0])
                    for item in connection.execute(text("select version_num from alembic_version"))
                )
            report["schemaAtHead"] = report["currentHeads"] == report["expectedHeads"]
    except Exception as exc:  # status must report dependency failures, not crash
        report["error"] = redact_text(str(exc))
    finally:
        if engine is not None:
            engine.dispose()
    return report


def _active_jobs_report() -> dict[str, object]:
    settings = _settings()
    engine = create_engine(
        settings.database_url,
        poolclass=NullPool,
        connect_args={"connect_timeout": settings.database_connect_timeout_seconds},
    )
    try:
        with engine.connect() as connection:
            if "background_jobs" not in inspect(connection).get_table_names():
                return {"reachable": True, "activeCount": 0}
            rows = connection.execute(
                text(
                    "select id, job_type, status from background_jobs "
                    "where status in ('RUNNING', 'RECOVERING') order by id"
                )
            ).mappings()
            active = [dict(row) for row in rows]
            return {"reachable": True, "activeCount": len(active), "active": active}
    except Exception as exc:
        return {"reachable": False, "activeCount": None, "error": redact_text(str(exc))}
    finally:
        engine.dispose()


def _alembic_heads() -> list[str]:
    config = Config(str(Path.cwd() / "alembic.ini"))
    config.set_main_option("script_location", str(Path.cwd() / "alembic"))
    return sorted(ScriptDirectory.from_config(config).get_heads())


def _launch_web(stdout_path: Path, stderr_path: Path) -> dict[str, object]:
    environment = dict(os.environ)
    environment["JOB_WORKER_ENABLED"] = "true"
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    with (
        stdout_path.open("ab", buffering=0) as stdout,
        stderr_path.open("ab", buffering=0) as stderr,
    ):
        process = subprocess.Popen(
            [sys.executable, "-m", "app.serve", "--host", "127.0.0.1", "--port", "8000"],
            cwd=Path.cwd(),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            creationflags=creationflags,
            start_new_session=os.name != "nt",
        )
    return {"launcherPid": process.pid}


def _signal_break(process_id: int) -> dict[str, object]:
    try:
        if os.name == "nt":
            os.kill(process_id, signal.CTRL_BREAK_EVENT)
        else:
            os.kill(process_id, signal.SIGTERM)
        return {"signaled": True}
    except Exception as exc:
        return {"signaled": False, "error": redact_text(str(exc))}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("database")
    subparsers.add_parser("active-jobs")
    launch = subparsers.add_parser("launch-web")
    launch.add_argument("--stdout", type=Path, required=True)
    launch.add_argument("--stderr", type=Path, required=True)
    stop = subparsers.add_parser("signal-break")
    stop.add_argument("--pid", type=int, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    if args.command == "database":
        report = _database_report()
    elif args.command == "active-jobs":
        report = _active_jobs_report()
    elif args.command == "launch-web":
        report = _launch_web(args.stdout, args.stderr)
    else:
        report = _signal_break(args.pid)
    print(json.dumps(report, default=str, separators=(",", ":")))


if __name__ == "__main__":
    main()
