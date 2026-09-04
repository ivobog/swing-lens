from __future__ import annotations

import json
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

os.environ["DB_MONITOR_ENABLED"] = "false"

from sqlalchemy import text

from app.db import SessionLocal


def clean(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def rows(db, sql: str):
    return [{key: clean(value) for key, value in row.items()} for row in db.execute(text(sql)).mappings()]


def main() -> int:
    output = {}
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        output["server"] = rows(db, """
            SELECT version() AS version,
                   current_database() AS database,
                   pg_size_pretty(pg_database_size(current_database())) AS database_size,
                   current_setting('shared_preload_libraries', true) AS shared_preload_libraries,
                   current_setting('pg_stat_statements.track', true) AS pg_stat_statements_track,
                   current_setting('auto_explain.log_min_duration', true) AS auto_explain_log_min_duration,
                   current_setting('auto_explain.log_analyze', true) AS auto_explain_log_analyze,
                   current_setting('auto_explain.log_buffers', true) AS auto_explain_log_buffers,
                   current_setting('auto_explain.log_nested_statements', true) AS auto_explain_log_nested_statements
        """)[0]
        output["pg_stat_statements_info"] = rows(db, "SELECT * FROM pg_stat_statements_info")
        output["database_stats"] = rows(db, """
            SELECT datname, numbackends, xact_commit, xact_rollback, blks_read, blks_hit,
                   tup_returned, tup_fetched, tup_inserted, tup_updated, tup_deleted,
                   conflicts, temp_files, temp_bytes, deadlocks, checksum_failures,
                   blk_read_time, blk_write_time, session_time, active_time,
                   idle_in_transaction_time, sessions, sessions_abandoned,
                   sessions_fatal, sessions_killed, stats_reset
            FROM pg_stat_database WHERE datname = current_database()
        """)
        output["statements"] = rows(db, """
            SELECT queryid, toplevel, calls, total_plan_time, mean_plan_time,
                   total_exec_time, mean_exec_time, rows,
                   shared_blks_hit, shared_blks_read, shared_blks_dirtied, shared_blks_written,
                   temp_blks_read, temp_blks_written,
                   shared_blk_read_time, shared_blk_write_time,
                   temp_blk_read_time, temp_blk_write_time,
                   wal_records, wal_fpi, wal_bytes, query
            FROM pg_stat_statements
            WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
            ORDER BY total_exec_time DESC
            LIMIT 250
        """)
        output["tables"] = rows(db, """
            SELECT relname, seq_scan, seq_tup_read, idx_scan, idx_tup_fetch,
                   n_tup_ins, n_tup_upd, n_tup_del, n_live_tup, n_dead_tup,
                   vacuum_count, autovacuum_count, analyze_count, autoanalyze_count,
                   last_vacuum, last_autovacuum, last_analyze, last_autoanalyze,
                   pg_total_relation_size(relid) AS total_bytes,
                   pg_relation_size(relid) AS heap_bytes,
                   pg_indexes_size(relid) AS index_bytes
            FROM pg_stat_user_tables
            ORDER BY pg_total_relation_size(relid) DESC
        """)
        output["indexes"] = rows(db, """
            SELECT schemaname, tablename, indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = 'public'
            ORDER BY tablename, indexname
        """)
        output["index_usage"] = rows(db, """
            SELECT relname, indexrelname, idx_scan, idx_tup_read, idx_tup_fetch,
                   pg_relation_size(indexrelid) AS index_bytes
            FROM pg_stat_user_indexes
            ORDER BY idx_scan DESC
        """)
        output["current_activity"] = rows(db, """
            SELECT pid, application_name, state, wait_event_type, wait_event,
                   backend_start, xact_start, query_start, state_change,
                   pg_blocking_pids(pid) AS blocking_pids,
                   left(query, 1000) AS query
            FROM pg_stat_activity
            WHERE datname = current_database()
            ORDER BY query_start NULLS LAST
        """)
        output["current_locks"] = rows(db, """
            SELECT locktype, mode, granted, count(*) AS count
            FROM pg_locks l
            JOIN pg_database d ON d.oid = l.database
            WHERE d.datname = current_database()
            GROUP BY locktype, mode, granted
            ORDER BY granted, locktype, mode
        """)
        db.rollback()
    Path("output/swinglens_postgres_audit_snapshot.json").write_text(
        json.dumps(output, indent=2), encoding="utf-8"
    )
    print(json.dumps({"sections": list(output), "statements": len(output["statements"]), "tables": len(output["tables"]), "indexes": len(output["indexes"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
