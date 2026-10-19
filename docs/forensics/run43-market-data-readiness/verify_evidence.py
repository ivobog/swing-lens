"""Check preserved DB rows, pin reviewed source, and capture only relevant settings."""

# ruff: noqa: E402
from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text

from app.settings import get_settings


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def save(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")


def main():
    engine = create_engine(
        get_settings().database_url,
        connect_args={
            "application_name": "run43-evidence-verification-readonly",
            "options": "-c default_transaction_read_only=on -c statement_timeout=30000",
        },
        isolation_level="REPEATABLE READ",
    )
    checks = []
    try:
        with engine.connect() as db:
            assert db.scalar(text("show transaction_read_only")) == "on"
            for name, sql in json.loads((OUT / "queries.json").read_text()).items():
                before = json.loads((OUT / (name + ".json")).read_text())
                after = list(db.scalars(text(sql)))
                checks.append(
                    {
                        "artifact": name,
                        "rows": len(after),
                        "before_sha256": digest(before),
                        "after_sha256": digest(after),
                        "unchanged": digest(before) == digest(after),
                    }
                )
            anchor_id = json.loads((OUT / "jobs.json").read_text())[0]["payload_json"][
                "effective_configuration_anchor"
            ]["anchor_id"]
            anchor = db.scalar(
                text(
                    "select payload_json from execution_configuration_anchors where anchor_id=:id"
                ),
                {"id": anchor_id},
            )
            record_id = anchor["configurations"]["execution.settings"]["record"]
            record = db.scalar(
                text(
                    "select to_jsonb(r) from effective_configuration_records r "
                    "where resolution_hash=:id"
                ),
                {"id": record_id},
            )
            selected = [
                entry
                for entry in record["payload_json"]["entries"]
                if entry["key"]
                in {
                    "ib_max_retries",
                    "ib_revision_window_sessions",
                    "ib_use_rth",
                    "ib_timeout_seconds",
                    "market_data_mode",
                    "market_data_policy",
                    "pipeline_technical_overlap_enabled",
                }
            ]
            save(
                "retained-relevant-settings.json",
                {
                    "anchor_id": anchor_id,
                    "resolution_hash": record_id,
                    "entries": selected,
                    "note": (
                        "Only keys represented in the durable execution.settings namespace are "
                        "included; absent keys must not be inferred as frozen "
                        "configuration evidence."
                    ),
                },
            )
            impact_queries = {
                table: f"select count(*) from {table} where run_id=43"
                for table in (
                    "fundamental_scores",
                    "technical_scores",
                    "combined_results",
                    "ranking_results",
                    "setup_signal_snapshots",
                    "winner_prediction_snapshots",
                )
            }
            save(
                "impact-counts.json",
                {
                    "counts": {name: db.scalar(text(sql)) for name, sql in impact_queries.items()},
                    "queries": impact_queries,
                    "upload_run_id": 43,
                },
            )
            save(
                "preservation-verification.json",
                {
                    "read_only_enforced": True,
                    "all_captured_rows_unchanged": all(c["unchanged"] for c in checks),
                    "checks": checks,
                    "verified_at": str(db.scalar(text("select current_timestamp"))),
                },
            )
    finally:
        engine.dispose()

    revision = "96204fb6fa15aed858fa06ba52d4b70c86d1d8b5"
    names = {
        "app/services/ib_historical_request_scope.py": [
            "reviewed_start_for_duration",
            "build_historical_request_scope",
        ],
        "app/services/ib_fetch_plan_service.py": ["_incremental_request_window"],
        "app/services/ib_fetch_executor.py": [
            "_execute_plan_item",
            "_validate_returned_scope",
            "execute_fetch_plan",
        ],
        "app/services/ib_data_fetcher.py": ["fetch_daily_bars"],
        "app/services/market_data_session_readiness.py": [
            "classify_session_readiness",
            "evaluate_and_persist_session_readiness",
        ],
        "app/services/price_bar_repository.py": ["load_price_bar_rows_for_context"],
        "app/services/pipeline_executor.py": ["_mark_pipeline_blocked"],
        "app/services/us_market_calendar.py": ["us_trading_sessions_between"],
    }
    pins = []
    for name, functions in names.items():
        current = (ROOT / name).read_bytes()
        committed = subprocess.check_output(["git", "show", revision + ":" + name], cwd=ROOT)
        source = current.decode("utf-8-sig")
        baseline = committed.decode("utf-8-sig")
        ast_current = {
            n.name: ast.dump(n, include_attributes=False)
            for n in ast.parse(source).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        ast_commit = {
            n.name: ast.dump(n, include_attributes=False)
            for n in ast.parse(baseline).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        pins.append(
            {
                "path": name,
                "current_sha256": hashlib.sha256(current).hexdigest(),
                "logged_runtime_commit": revision,
                "commit_blob_sha256": hashlib.sha256(committed).hexdigest(),
                "text_equal_after_newline_normalization": source.replace("\r\n", "\n")
                == baseline.replace("\r\n", "\n"),
                "functions_ast_equal": {
                    fn: ast_current.get(fn) == ast_commit.get(fn) for fn in functions
                },
            }
        )
    save("reviewed-source-sha256.json", pins)
    (OUT / "reviewed-working-tree.diff").write_bytes(
        subprocess.check_output(["git", "diff", "--", *names], cwd=ROOT)
    )
    print(
        json.dumps(
            {
                "all_captured_rows_unchanged": all(c["unchanged"] for c in checks),
                "source_files": len(pins),
                "retained_settings_keys": [e["key"] for e in selected],
            }
        )
    )


if __name__ == "__main__":
    main()
