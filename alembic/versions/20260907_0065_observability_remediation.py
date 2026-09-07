"""Fence recovering jobs and add bounded Operations access paths.

Revision ID: 0065_observability_remediation
Revises: 0064_observability_downstream_correlation

The unique replacement is built concurrently before the old index is removed.
If pre-existing RECOVERING conflicts exist PostgreSQL aborts index creation and
leaves the original fence in place; operators must reconcile those rows rather
than this migration fabricating a winner.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0065_observability_remediation"
down_revision = "0064_observability_downstream_correlation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table_name in ("background_workers", "background_supervisors"):
        op.add_column(table_name, sa.Column("telemetry_status", sa.Text(), nullable=True))
        op.add_column(table_name, sa.Column("resource_collector_status", sa.Text(), nullable=True))
        op.add_column(
            table_name,
            sa.Column("resource_collector_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        )
    op.create_table(
        "background_job_fanout_roots",
        sa.Column("root_correlation_id", sa.Text(), nullable=False),
        sa.Column("workflow_family", sa.Text(), nullable=False),
        sa.Column("first_occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempted_enqueues", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("created_jobs", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("coalesced_attempts", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("rejected_attempts", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("total_descendant_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("maximum_depth", sa.Integer(), server_default="0", nullable=False),
        sa.Column("warning_emitted", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("critical_emitted", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "job_family_distribution_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("root_correlation_id"),
    )
    op.create_index(
        "idx_fanout_roots_recent",
        "background_job_fanout_roots",
        ["last_occurred_at", "root_correlation_id"],
    )
    op.create_index(
        "idx_fanout_roots_family_recent",
        "background_job_fanout_roots",
        ["workflow_family", "last_occurred_at"],
    )
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY "
            "uq_background_jobs_active_request_key_recovering "
            "ON background_jobs (job_type, request_key) "
            "WHERE request_key IS NOT NULL "
            "AND status IN ('QUEUED', 'RUNNING', 'RECOVERING')"
        )
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS uq_background_jobs_active_request_key")
        op.execute(
            "ALTER INDEX uq_background_jobs_active_request_key_recovering "
            "RENAME TO uq_background_jobs_active_request_key"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_enqueue_attempts_time_root "
            "ON background_job_enqueue_attempts (occurred_at DESC, id DESC, root_correlation_id)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_ceri_provider_telemetry_observed_provider "
            "ON ceri_provider_request_telemetry "
            "(observed_at DESC, provider) INCLUDE (latency_ms, retry_count, error_code)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY "
            "uq_background_jobs_active_request_key_legacy "
            "ON background_jobs (job_type, request_key) "
            "WHERE request_key IS NOT NULL AND status IN ('QUEUED', 'RUNNING')"
        )
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS uq_background_jobs_active_request_key")
        op.execute(
            "ALTER INDEX uq_background_jobs_active_request_key_legacy "
            "RENAME TO uq_background_jobs_active_request_key"
        )
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_enqueue_attempts_time_root")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_ceri_provider_telemetry_observed_provider")
    op.drop_table("background_job_fanout_roots")
    for table_name in reversed(("background_workers", "background_supervisors")):
        op.drop_column(table_name, "resource_collector_heartbeat_at")
        op.drop_column(table_name, "resource_collector_status")
        op.drop_column(table_name, "telemetry_status")
