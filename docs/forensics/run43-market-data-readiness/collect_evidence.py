"""Run 43 forensic capture. All database transactions are enforced read-only."""

# ruff: noqa: E402
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text

from app.settings import get_settings


def save(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")


def capture():
    engine = create_engine(
        get_settings().database_url,
        connect_args={
            "application_name": "run43-forensics-readonly",
            "options": "-c default_transaction_read_only=on -c statement_timeout=30000",
        },
        isolation_level="REPEATABLE READ",
    )
    queries = {
        "pipeline": "select to_jsonb(p) from pipeline_runs p where id=43",
        "steps": "select to_jsonb(s) from pipeline_steps s where pipeline_run_id=43 order by id",
        "jobs": (
            "select to_jsonb(j)-'execution_token' from background_jobs j "
            "where related_run_id=43 or payload_json->>'pipeline_run_id'='43' order by id"
        ),
        "dispositions": (
            "select to_jsonb(d) from market_data_session_dispositions d "
            "where pipeline_run_id=43 order by id"
        ),
        "dds-contract": "select to_jsonb(c) from ib_contracts c where ticker='DDS'",
        "dds-lifecycle": (
            "select to_jsonb(l) from instrument_lifecycle_records l "
            "where ticker='DDS' order by observed_at"
        ),
        "dds-recent-bars": (
            "select to_jsonb(b) from price_bars b where ticker='DDS' "
            "and bar_date>='2026-10-01' order by bar_date,what_to_show"
        ),
        "context": "select to_jsonb(c) from market_calculation_contexts c where pipeline_run_id=43",
        "upload": "select to_jsonb(u) from upload_runs u where id=43",
        "fetch-runs": (
            "select to_jsonb(f)-'execution_token' from ib_fetch_runs f where run_id=43 order by id"
        ),
        "fetch-items": (
            "select to_jsonb(i)-'execution_token' from ib_fetch_items i "
            "where fetch_run_id in (select id from ib_fetch_runs where run_id=43) "
            "order by id"
        ),
        "dds-fetch-history": (
            "select to_jsonb(i)-'execution_token' from ib_fetch_items i "
            "where ticker='DDS' order by id"
        ),
        "scopes": (
            "select to_jsonb(s) from work_scope_records s where "
            "parent_scope_id=(select scope_id from pipeline_runs where id=43) "
            "or scope_id=(select scope_id from pipeline_runs where id=43)"
        ),
        "acquisition-plans": (
            "select to_jsonb(a) from acquisition_plan_records a "
            "where plan_id in (select acquisition_plan_id from ib_fetch_runs "
            "where run_id=43) or plan_id=(select acquisition_plan_id "
            "from pipeline_runs where id=43)"
        ),
        "schema": (
            "select to_jsonb(c) from information_schema.columns c "
            "where table_schema='public' and (table_name like '%market%context%' "
            "or table_name like '%acquisition%' or table_name like '%fetch%' "
            "or table_name like '%scope%' or table_name like '%price_bar%') "
            "order by table_name,ordinal_position"
        ),
    }
    try:
        with engine.connect() as db:
            assert db.scalar(text("show transaction_read_only")) == "on"
            identity = dict(
                db.execute(
                    text(
                        "select current_database() database, current_timestamp captured_at, "
                        "version() version"
                    )
                )
                .mappings()
                .one()
            )
            save("database-identity.json", {**identity, "read_only_enforced": True})
            for name, sql in queries.items():
                rows = list(db.scalars(text(sql)))
                save(name + ".json", rows)
                print(json.dumps({"artifact": name, "rows": len(rows)}), flush=True)
    finally:
        engine.dispose()
    save("queries.json", queries)
    save(
        "git-state.json",
        {
            "head": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "status": subprocess.check_output(["git", "status", "--short"], cwd=ROOT, text=True),
        },
    )


if __name__ == "__main__":
    capture()
