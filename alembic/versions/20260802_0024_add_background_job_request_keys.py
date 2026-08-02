"""add background job request keys

Revision ID: 0024_background_job_request_keys
Revises: 0023_immutable_snapshot_evidence
Create Date: 2026-08-02 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0024_background_job_request_keys"
down_revision: str | None = "0023_immutable_snapshot_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("background_jobs", sa.Column("request_key", sa.Text(), nullable=True))

    op.execute(
        """
        UPDATE background_jobs
        SET request_key = payload_json ->> 'request_key'
        WHERE request_key IS NULL
          AND payload_json ? 'request_key'
        """
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT
                id,
                row_number() OVER (
                    PARTITION BY job_type, request_key
                    ORDER BY created_at DESC, id DESC
                ) AS duplicate_rank
            FROM background_jobs
            WHERE request_key IS NOT NULL
              AND status IN ('QUEUED', 'RUNNING')
        )
        UPDATE background_jobs AS job
        SET
            status = 'CANCELLED',
            completed_at = now(),
            locked_at = NULL,
            heartbeat_at = NULL,
            lease_expires_at = NULL,
            worker_id = NULL,
            lease_owner = NULL,
            execution_token = NULL,
            error_message = 'Cancelled duplicate active job during request-key migration.',
            operational_metadata_json =
                COALESCE(job.operational_metadata_json, '{}'::jsonb)
                || jsonb_build_object(
                    'request_key_migration',
                    jsonb_build_object(
                        'cancelled_as_duplicate', true,
                        'cancelled_at', now()
                    )
                )
        FROM ranked
        WHERE job.id = ranked.id
          AND ranked.duplicate_rank > 1
        """
    )
    op.create_index(
        "idx_background_jobs_request_key",
        "background_jobs",
        ["request_key"],
    )
    op.create_index(
        "uq_background_jobs_active_request_key",
        "background_jobs",
        ["job_type", "request_key"],
        unique=True,
        postgresql_where=sa.text("request_key IS NOT NULL AND status IN ('QUEUED', 'RUNNING')"),
    )


def downgrade() -> None:
    op.drop_index("uq_background_jobs_active_request_key", table_name="background_jobs")
    op.drop_index("idx_background_jobs_request_key", table_name="background_jobs")
    op.drop_column("background_jobs", "request_key")
