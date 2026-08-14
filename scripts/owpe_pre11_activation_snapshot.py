"""Capture a read-only OWPE activation safety snapshot."""

from __future__ import annotations

import json

from sqlalchemy import text

from app.db import SessionLocal
from app.settings import get_settings

QUERIES = {
    "db_identity": """
        SELECT json_build_object(
            'database', current_database(),
            'user', current_user,
            'server_version', version(),
            'timezone', current_setting('TimeZone')
        )
    """,
    "winner_table_counts": """
        SELECT COALESCE(json_object_agg(tablename, cnt), '{}'::json)
        FROM (
            SELECT t.tablename,
                   (xpath(
                       '/row/cnt/text()',
                       query_to_xml(
                           format('select count(*) as cnt from %I', t.tablename),
                           false,
                           true,
                           ''
                       )
                   ))[1]::text::bigint AS cnt
            FROM pg_tables t
            WHERE schemaname = 'public'
              AND tablename LIKE 'winner_%'
            ORDER BY tablename
        ) AS counts
    """,
    "run_104": """
        SELECT json_build_object(
            'snapshots', count(DISTINCT p.id),
            'estimates', count(e.id),
            'latest_snapshot', max(p.captured_at),
            'latest_estimate', max(e.created_at)
        )
        FROM winner_prediction_snapshots p
        LEFT JOIN winner_probability_estimates e ON e.prediction_id = p.id
        WHERE p.run_id = 104
    """,
    "run_105": """
        SELECT json_build_object(
            'snapshots', count(DISTINCT p.id),
            'estimates', count(e.id),
            'latest_snapshot', max(p.captured_at),
            'latest_estimate', max(e.created_at)
        )
        FROM winner_prediction_snapshots p
        LEFT JOIN winner_probability_estimates e ON e.prediction_id = p.id
        WHERE p.run_id = 105
    """,
    "pending_h5": """
        SELECT json_build_object(
            'all_pending_h5_next_open', count(*),
            'through_2026_08_13', count(*) FILTER (
                WHERE due_session <= DATE '2026-08-13'
            ),
            'min_due', min(due_session),
            'max_due', max(due_session)
        )
        FROM winner_forward_outcomes
        WHERE is_current_revision
          AND status = 'PENDING'
          AND entry_model = 'NEXT_OPEN'
          AND horizon_sessions = 5
    """,
    "relevant_jobs": """
        SELECT COALESCE(json_agg(row_to_json(j) ORDER BY id), '[]'::json)
        FROM (
            SELECT id, job_type, status, request_key, related_run_id,
                   created_at, started_at, completed_at
            FROM background_jobs
            WHERE job_type LIKE 'WINNER_%'
            ORDER BY id DESC
            LIMIT 25
        ) AS j
    """,
    "compat_tables": """
        SELECT json_build_object(
            'decision_exists',
                to_regclass('public.winner_training_eligibility_decisions') IS NOT NULL,
            'replay_exists',
                to_regclass('public.winner_training_outcome_replays') IS NOT NULL
        )
    """,
    "compat_schema": """
        SELECT json_build_object(
            'indexes', (
                SELECT json_agg(indexname ORDER BY indexname)
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename IN (
                      'winner_training_eligibility_decisions',
                      'winner_training_outcome_replays'
                  )
            ),
            'foreign_keys', (
                SELECT json_agg(conname ORDER BY conname)
                FROM pg_constraint
                WHERE contype = 'f'
                  AND conrelid IN (
                      'winner_training_eligibility_decisions'::regclass,
                      'winner_training_outcome_replays'::regclass,
                      'winner_estimate_evidence_members'::regclass
                  )
                  AND (
                      conrelid IN (
                          'winner_training_eligibility_decisions'::regclass,
                          'winner_training_outcome_replays'::regclass
                      )
                      OR conname LIKE 'fk_winner_evidence_member_%'
                  )
            ),
            'triggers', (
                SELECT json_agg(tgname ORDER BY tgname)
                FROM pg_trigger
                WHERE NOT tgisinternal
                  AND tgrelid IN (
                      'winner_training_eligibility_decisions'::regclass,
                      'winner_training_outcome_replays'::regclass
                  )
            )
        )
    """,
    "compat_write": """
        SELECT json_build_object(
            'decisions', (SELECT count(*) FROM winner_training_eligibility_decisions),
            'approved_decisions', (
                SELECT count(*)
                FROM winner_training_eligibility_decisions
                WHERE training_allowed
            ),
            'replays', (SELECT count(*) FROM winner_training_outcome_replays),
            'request_keys', (
                SELECT json_agg(DISTINCT request_key)
                FROM winner_training_eligibility_decisions
            ),
            'manifest_hashes', (
                SELECT json_agg(DISTINCT evidence_manifest_hash)
                FROM winner_training_eligibility_decisions
            ),
            'decision_id_range', (
                SELECT json_build_array(min(id), max(id))
                FROM winner_training_eligibility_decisions
            ),
            'replay_id_range', (
                SELECT json_build_array(min(id), max(id))
                FROM winner_training_outcome_replays
            ),
            'wins', (
                SELECT count(*)
                FROM winner_training_outcome_replays
                WHERE primary_winner
            ),
            'origins', (
                SELECT json_build_object(
                    'native_1_1', 0,
                    'pre11_compat_replay', count(*)
                )
                FROM winner_training_outcome_replays
            )
        )
    """,
    "run_104_snapshot_checksum": """
        SELECT md5(COALESCE(string_agg(md5(row_to_json(t)::text), '' ORDER BY id), ''))
        FROM (SELECT * FROM winner_prediction_snapshots WHERE run_id = 104) AS t
    """,
    "run_104_estimate_checksum": """
        SELECT md5(COALESCE(string_agg(md5(row_to_json(t)::text), '' ORDER BY id), ''))
        FROM (
            SELECT e.*
            FROM winner_probability_estimates e
            JOIN winner_prediction_snapshots p ON p.id = e.prediction_id
            WHERE p.run_id = 104
        ) AS t
    """,
    "run_105_snapshot_checksum": """
        SELECT md5(COALESCE(string_agg(md5(row_to_json(t)::text), '' ORDER BY id), ''))
        FROM (SELECT * FROM winner_prediction_snapshots WHERE run_id = 105) AS t
    """,
    "run_105_estimate_checksum": """
        SELECT md5(COALESCE(string_agg(md5(row_to_json(t)::text), '' ORDER BY id), ''))
        FROM (
            SELECT e.*
            FROM winner_probability_estimates e
            JOIN winner_prediction_snapshots p ON p.id = e.prediction_id
            WHERE p.run_id = 105
        ) AS t
    """,
    "legacy_boolean_projection": """
        SELECT json_build_object(
            'true', count(*) FILTER (
                WHERE (lineage_json->>'production_training_allowed')::boolean IS TRUE
            ),
            'false', count(*) FILTER (
                WHERE (lineage_json->>'production_training_allowed')::boolean IS FALSE
            ),
            'missing', count(*) FILTER (
                WHERE lineage_json->>'production_training_allowed' IS NULL
            )
        )
        FROM winner_prediction_snapshots
    """,
    "outcome_counts": """
        SELECT json_build_object(
            'forward', (SELECT count(*) FROM winner_forward_outcomes),
            'target_stop', (SELECT count(*) FROM winner_target_stop_outcomes)
        )
    """,
}


def main() -> int:
    settings = get_settings()
    snapshot: dict[str, object] = {
        "auto_maturation_enabled": settings.winner_probability_auto_maturation_enabled,
    }
    with SessionLocal() as db:
        for name, statement in QUERIES.items():
            snapshot[name] = db.execute(text(statement)).scalar()
        db.rollback()
    print(json.dumps(snapshot, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
