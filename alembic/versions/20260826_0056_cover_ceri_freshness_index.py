"""Cover CERI source timestamps in the stale-record count index.

Revision ID: 0056_cover_ceri_freshness
Revises: 0055_ceri_operations_indexes
Create Date: 2026-08-26
"""

from __future__ import annotations

from alembic import op

revision = "0056_cover_ceri_freshness"
down_revision = "0055_ceri_operations_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS
                ix_ceri_source_records_dataset_freshness_cover
            ON ceri_source_records (
                dataset,
                (COALESCE(observed_at, published_at, ingested_at))
            )
            INCLUDE (observed_at, published_at, ingested_at)
            """
        )
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_ceri_source_records_dataset_freshness")
        op.execute(
            """
            ALTER INDEX ix_ceri_source_records_dataset_freshness_cover
            RENAME TO ix_ceri_source_records_dataset_freshness
            """
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS
                ix_ceri_source_records_dataset_freshness_plain
            ON ceri_source_records (
                dataset,
                (COALESCE(observed_at, published_at, ingested_at))
            )
            """
        )
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_ceri_source_records_dataset_freshness")
        op.execute(
            """
            ALTER INDEX ix_ceri_source_records_dataset_freshness_plain
            RENAME TO ix_ceri_source_records_dataset_freshness
            """
        )
