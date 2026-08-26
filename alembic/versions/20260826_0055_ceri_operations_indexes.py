"""Add bounded-query indexes for the CERI operations dashboard.

Revision ID: 0055_ceri_operations_indexes
Revises: 0054_worker_instance_reliability
Create Date: 2026-08-26
"""

from __future__ import annotations

from alembic import op

revision = "0055_ceri_operations_indexes"
down_revision = "0054_worker_instance_reliability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_ceri_source_records_freshness
            ON ceri_source_records (
                provider,
                dataset,
                (COALESCE(observed_at, published_at, ingested_at)) DESC
            )
            """
        )
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_ceri_source_records_dataset_freshness
            ON ceri_source_records (
                dataset,
                (COALESCE(observed_at, published_at, ingested_at))
            )
            """
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_ceri_source_records_dataset_freshness")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_ceri_source_records_freshness")
