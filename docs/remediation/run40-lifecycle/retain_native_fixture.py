"""Retain only the disposable native fixture, before lifecycle publication."""

import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, sleep

import psutil
from sqlalchemy import create_engine, text

from app.settings import get_settings

database = "swinglens_pytest_69284b2e2a2c"
assert database.startswith("swinglens_pytest_")
process = psutil.Process(61648)
assert "native-88-final.xml" in " ".join(process.cmdline())
url = create_engine(get_settings().database_url).url.set(database=database)
engine = create_engine(url)
evidence = Path(__file__).parent / "evidence"
destination = evidence / "native-fixture-prepublication.dump"
assert not destination.exists()
deadline = monotonic() + 7200
print("Waiting for the disposable 321-history / 88-current native input fixture.", flush=True)
try:
    while monotonic() < deadline:
        if not process.is_running():
            raise RuntimeError("Native test exited before the fixture could be retained")
        with engine.connect() as db:
            ready = db.scalar(
                text("""
                    select (select count(*) from ranking_results where run_id=702)=88
                       and exists (select 1 from core_calculation_evidence
                                   where run_id=702 and artifact_kind='SECTOR')
            """)
            )
            if ready:
                counts = dict(
                    db.execute(
                        text("""
                    select run_id, count(*) from setup_signal_snapshots
                    group by run_id order by run_id
                """)
                    ).all()
                )
                assert sum(counts.get(run_id, 0) for run_id in range(801, 808)) == 321
                assert counts.get(702, 0) == 0, "Snapshot boundary must precede setup capture"
                break
        sleep(5)
    else:
        raise RuntimeError("Native fixture retention deadline exceeded")
finally:
    engine.dispose()

environment = dict(os.environ)
environment.update(
    PGHOST=url.host,
    PGPORT=str(url.port or 5432),
    PGUSER=url.username,
    PGPASSWORD=url.password or "",
    PGDATABASE=database,
)
result = subprocess.run(
    [
        r"C:\Program Files\PostgreSQL\18\bin\pg_dump.exe",
        "-Fc",
        "--no-owner",
        "--no-acl",
        "--no-password",
        "--file",
        str(destination),
    ],
    env=environment,
    capture_output=True,
)
assert result.returncode == 0, "Disposable fixture pg_dump failed"
metadata = {
    "created_at_utc": datetime.now(UTC).isoformat(),
    "database": database,
    "dump": destination.name,
    "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
    "bytes": destination.stat().st_size,
    "snapshot_counts": counts,
    "source_manifest": "native-88-source-manifest.json",
    "purpose": (
        "Fresh-process final-code certification against authentic native seeded inputs; "
        "no production data."
    ),
}
(evidence / "native-fixture-prepublication.json").write_text(json.dumps(metadata, indent=2) + "\n")
print(json.dumps(metadata), flush=True)
