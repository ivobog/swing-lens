"""expand ib fetch persistence

Revision ID: 0004_expand_ib_fetch_persistence
Revises: 0003_price_bar_revision_metadata
Create Date: 2026-07-03 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_expand_ib_fetch_persistence"
down_revision: str | None = "0003_price_bar_revision_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ib_fetch_runs",
        sa.Column(
            "symbols_including_benchmarks",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "ib_fetch_runs",
        sa.Column("force_refresh", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "ib_fetch_runs",
        sa.Column(
            "force_full_backfill",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    for column_name in [
        "planned_request_count",
        "executed_request_count",
        "skipped_count",
        "success_count",
        "updated_count",
        "revised_count",
        "unchanged_count",
    ]:
        op.add_column(
            "ib_fetch_runs",
            sa.Column(column_name, sa.Integer(), server_default=sa.text("0"), nullable=False),
        )

    op.add_column("ib_fetch_items", sa.Column("action", sa.Text(), nullable=True))
    op.add_column("ib_fetch_items", sa.Column("duration", sa.Text(), nullable=True))
    op.add_column(
        "ib_fetch_items",
        sa.Column("bar_size", sa.Text(), server_default=sa.text("'1 day'"), nullable=False),
    )
    op.add_column("ib_fetch_items", sa.Column("reason", sa.Text(), nullable=True))
    for column_name in [
        "current_bar_count",
        "updated",
        "revised",
        "unchanged",
        "attempt_count",
    ]:
        op.add_column(
            "ib_fetch_items",
            sa.Column(column_name, sa.Integer(), server_default=sa.text("0"), nullable=False),
        )
    op.add_column("ib_fetch_items", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "ib_fetch_items",
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_ib_fetch_items_status", "ib_fetch_items", ["status"])


def downgrade() -> None:
    op.drop_index("idx_ib_fetch_items_status", table_name="ib_fetch_items")
    op.drop_column("ib_fetch_items", "completed_at")
    op.drop_column("ib_fetch_items", "started_at")
    for column_name in [
        "attempt_count",
        "unchanged",
        "revised",
        "updated",
        "current_bar_count",
    ]:
        op.drop_column("ib_fetch_items", column_name)
    op.drop_column("ib_fetch_items", "reason")
    op.drop_column("ib_fetch_items", "bar_size")
    op.drop_column("ib_fetch_items", "duration")
    op.drop_column("ib_fetch_items", "action")

    for column_name in [
        "unchanged_count",
        "revised_count",
        "updated_count",
        "success_count",
        "skipped_count",
        "executed_request_count",
        "planned_request_count",
    ]:
        op.drop_column("ib_fetch_runs", column_name)
    op.drop_column("ib_fetch_runs", "force_full_backfill")
    op.drop_column("ib_fetch_runs", "force_refresh")
    op.drop_column("ib_fetch_runs", "symbols_including_benchmarks")
