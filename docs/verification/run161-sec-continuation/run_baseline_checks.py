"""Reproduce unrelated certification failures in the untouched starting commit."""

import os
import subprocess
import sys
from pathlib import Path

repository = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(repository))
from sqlalchemy.engine import make_url
from app.settings import get_settings


def main():
    baseline = repository / ".qa_work" / "run161-baseline"
    assert (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=baseline, text=True).strip()
        == "d3157c8642c119a7e5d7a84c69f4a58c6d3866d8"
    )
    env = dict(os.environ)
    # Safety preflight reads the active DB identity before allowing fixture DBs.
    # The worktree has no .env; pass the same active identity without copying it.
    env["DATABASE_URL"] = get_settings().database_url
    env["SWINGLENS_TEST_POSTGRES_ADMIN_URL"] = (
        make_url(get_settings().database_url)
        .set(database="postgres", drivername="postgresql")
        .render_as_string(hide_password=False)
    )
    env["PYTHONPATH"] = str(baseline)
    env["DB_MONITOR_ENABLED"] = "false"
    arguments = sys.argv[1:]
    return subprocess.call([sys.executable, "-m", "pytest", *arguments], cwd=baseline, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
