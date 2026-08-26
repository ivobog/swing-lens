from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import text

from app.db import SessionLocal, db_monitor
from app.observability.db_monitor import emit_monitor_record, shutdown_database_monitor
from app.settings import get_settings


def main() -> int:
    settings = get_settings()
    started_at = datetime.now(UTC).isoformat()
    with SessionLocal() as db:
        database = dict(
            db.execute(
                text(
                    """
                    SELECT version() AS postgres_version,
                           current_setting('shared_preload_libraries', true)
                               AS shared_preload_libraries,
                           current_setting('pg_stat_statements.track', true)
                               AS pg_stat_statements_track,
                           current_setting('auto_explain.log_min_duration', true)
                               AS auto_explain_log_min_duration,
                           current_setting('auto_explain.log_analyze', true)
                               AS auto_explain_log_analyze,
                           current_setting('auto_explain.log_nested_statements', true)
                               AS auto_explain_log_nested_statements,
                           EXISTS (
                               SELECT 1 FROM pg_extension
                               WHERE extname = 'pg_stat_statements'
                           ) AS pg_stat_statements_extension
                    """
                )
            )
            .mappings()
            .one()
        )
        db.rollback()

    marker = {
        "record_type": "FULL_WEEK_SQL_AUDIT_START",
        "authoritative": True,
        "supersedes_prior_start_markers": True,
        "timestamp": started_at,
        "audit_start_timestamp": started_at,
        "deployment_id": settings.deployment_id,
        "application_version": settings.application_version,
        "postgresql": database,
        "monitor_configuration": {
            "enabled": settings.db_monitor_enabled,
            "slow_query_ms": settings.db_monitor_slow_query_ms,
            "full_trace_ms": settings.db_monitor_full_trace_ms,
            "retention_days": settings.db_monitor_retention_days,
            "log_dir": str(settings.db_monitor_log_dir),
            "test_log_dir": str(settings.db_monitor_test_log_dir),
            "max_file_mb": settings.db_monitor_max_file_mb,
            "max_files_per_role": settings.db_monitor_max_files,
            "max_total_mb_per_role": settings.db_monitor_max_total_mb,
            "activity_sampler_enabled": settings.db_monitor_activity_sampler_enabled,
            "activity_threshold_ms": settings.db_monitor_activity_threshold_ms,
            "activity_sample_interval_seconds": (
                settings.db_monitor_activity_sample_interval_seconds
            ),
            "long_transaction_ms": settings.db_monitor_long_transaction_ms,
            "parameter_digest_enabled": settings.db_monitor_parameter_digest_enabled,
        },
        "monitor_status": db_monitor.status(),
    }
    if not emit_monitor_record(marker):
        raise RuntimeError("SQL monitor did not accept the full-week audit marker")
    shutdown_database_monitor()
    print(
        json.dumps(
            {
                "audit_start_timestamp": started_at,
                "deployment_id": settings.deployment_id,
                "pg_stat_statements": database["pg_stat_statements_extension"],
                "auto_explain_log_min_duration": database["auto_explain_log_min_duration"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
