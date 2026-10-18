"""Read-only progress timeline for one already-enqueued Run 41 recovery job."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent / "evidence"
sys.path.insert(0, str(ROOT))


def main(scope, job_id):
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    from app.database_safety import assert_disposable_database
    from app.settings import get_settings

    source = make_url(get_settings().database_url)
    assert source.database == "swinglens"
    target = source
    if scope == "clone":
        meta = json.loads((OUT / "production-clone.json").read_text())
        target = source.set(database=meta["database"])
        assert_disposable_database(target, active_database_url=source)
    engine = create_engine(
        target,
        connect_args={"options": "-c default_transaction_read_only=on -c statement_timeout=15000"},
    )
    previous = None
    started = time.monotonic()
    try:
        while time.monotonic() - started < 7200:
            with engine.connect() as db:
                assert db.scalar(text("show transaction_read_only")) == "on"
                row = dict(
                    db.execute(
                        text(
                            "select id,status,progress_stage,progress_current_item,"
                            "progress_processed,progress_total,last_progress_at,error_message "
                            "from background_jobs where id=:job_id and related_run_id=41"
                        ),
                        {"job_id": job_id},
                    )
                    .mappings()
                    .one()
                )
            key = (row["status"], row["progress_stage"], row["progress_current_item"])
            if key != previous:
                event = {"observed_at_utc": datetime.now(UTC).isoformat(), "scope": scope, **row}
                line = json.dumps(event, default=str)
                with (OUT / f"{scope}-recovery-progress.jsonl").open("a", encoding="utf-8") as log:
                    log.write(line + "\n")
                print(line, flush=True)
                previous = key
            if row["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                return
            time.sleep(15)
        raise TimeoutError("Recovery monitor timed out; no job mutation was performed")
    finally:
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=("live", "clone"), required=True)
    parser.add_argument("--job-id", type=int, required=True)
    args = parser.parse_args()
    main(args.scope, args.job_id)
