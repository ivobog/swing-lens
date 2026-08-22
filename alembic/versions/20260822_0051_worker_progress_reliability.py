"""add durable job progress and restart-safe market-data item identity

Revision ID: 0051_worker_progress_reliability
Revises: 0050_sec_processor_promotion
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0051_worker_progress_reliability"
down_revision: str | None = "0050_sec_processor_promotion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "background_jobs",
        sa.Column("last_progress_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "background_jobs",
        sa.Column("progress_sequence", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.add_column("background_jobs", sa.Column("progress_stage", sa.Text(), nullable=True))
    op.add_column("background_jobs", sa.Column("progress_current_item", sa.Text(), nullable=True))
    op.add_column(
        "background_jobs", sa.Column("progress_last_completed_item", sa.Text(), nullable=True)
    )
    op.add_column(
        "background_jobs",
        sa.Column("progress_processed", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.add_column("background_jobs", sa.Column("progress_total", sa.BigInteger(), nullable=True))
    op.add_column("background_jobs", sa.Column("checkpoint_version", sa.Text(), nullable=True))
    op.add_column(
        "background_jobs", sa.Column("stall_detected_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "background_jobs",
        sa.Column("recovery_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_index(
        "idx_background_jobs_progress_watchdog",
        "background_jobs",
        ["status", "last_progress_at"],
    )

    op.add_column("background_workers", sa.Column("instance_id", sa.Text(), nullable=True))
    op.add_column("background_workers", sa.Column("rss_bytes", sa.BigInteger(), nullable=True))
    op.add_column("background_workers", sa.Column("private_bytes", sa.BigInteger(), nullable=True))
    op.add_column("background_workers", sa.Column("memory_status", sa.Text(), nullable=True))

    op.add_column(
        "ib_fetch_runs", sa.Column("last_progress_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "ib_fetch_runs",
        sa.Column("progress_sequence", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.add_column("ib_fetch_items", sa.Column("execution_token", sa.Text(), nullable=True))
    op.create_unique_constraint(
        "uq_ib_fetch_items_run_ticker_feed",
        "ib_fetch_items",
        ["fetch_run_id", "ticker", "what_to_show"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_ib_fetch_items_run_ticker_feed", "ib_fetch_items", type_="unique")
    op.drop_column("ib_fetch_items", "execution_token")
    op.drop_column("ib_fetch_runs", "progress_sequence")
    op.drop_column("ib_fetch_runs", "last_progress_at")

    op.drop_column("background_workers", "memory_status")
    op.drop_column("background_workers", "private_bytes")
    op.drop_column("background_workers", "rss_bytes")
    op.drop_column("background_workers", "instance_id")

    op.drop_index("idx_background_jobs_progress_watchdog", table_name="background_jobs")
    op.drop_column("background_jobs", "recovery_count")
    op.drop_column("background_jobs", "stall_detected_at")
    op.drop_column("background_jobs", "checkpoint_version")
    op.drop_column("background_jobs", "progress_total")
    op.drop_column("background_jobs", "progress_processed")
    op.drop_column("background_jobs", "progress_last_completed_item")
    op.drop_column("background_jobs", "progress_current_item")
    op.drop_column("background_jobs", "progress_stage")
    op.drop_column("background_jobs", "progress_sequence")
    op.drop_column("background_jobs", "last_progress_at")
