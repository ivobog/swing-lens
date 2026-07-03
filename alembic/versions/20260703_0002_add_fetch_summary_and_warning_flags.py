"""add fetch summary and warning flags

Revision ID: 0002_fetch_warning_flags
Revises: 0001_create_database_foundation
Create Date: 2026-07-03 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_fetch_warning_flags"
down_revision: str | None = "0001_create_database_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ib_fetch_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=True),
        sa.Column("requested_tickers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("include_benchmarks", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("fetched_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("inserted_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("failure_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["upload_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "ib_fetch_items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("fetch_run_id", sa.BigInteger(), nullable=False),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("what_to_show", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("fetched", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("inserted", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["fetch_run_id"], ["ib_fetch_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_ib_fetch_items_fetch_run_id", "ib_fetch_items", ["fetch_run_id"])
    op.create_index("idx_ib_fetch_items_ticker", "ib_fetch_items", ["ticker"])

    op.add_column(
        "combined_results",
        sa.Column("warning_flags_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "combined_results",
        sa.Column("is_complete", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "combined_results",
        sa.Column("has_fundamental", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "combined_results",
        sa.Column("has_technical", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "combined_results",
        sa.Column("has_warning", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("combined_results", sa.Column("sort_bucket", sa.Integer(), nullable=True))

    op.execute(
        """
        UPDATE combined_results
        SET
            warning_flags_json = '[]'::jsonb,
            has_fundamental = fundamental_score IS NOT NULL,
            has_technical = dual_score IS NOT NULL,
            is_complete = fundamental_score IS NOT NULL AND dual_score IS NOT NULL,
            has_warning = COALESCE(notes, '') <> 'aligned'
                OR combined_decision = 'Incomplete data',
            sort_bucket = CASE
                WHEN NOT (fundamental_score IS NOT NULL AND dual_score IS NOT NULL) THEN 50
                WHEN combined_decision = 'Strong candidate' THEN 10
                WHEN combined_decision = 'Candidate' THEN 20
                WHEN combined_decision = 'Watchlist' THEN 30
                WHEN combined_decision = 'Avoid' THEN 40
                ELSE 60
            END
        """
    )


def downgrade() -> None:
    op.drop_column("combined_results", "sort_bucket")
    op.drop_column("combined_results", "has_warning")
    op.drop_column("combined_results", "has_technical")
    op.drop_column("combined_results", "has_fundamental")
    op.drop_column("combined_results", "is_complete")
    op.drop_column("combined_results", "warning_flags_json")

    op.drop_index("idx_ib_fetch_items_ticker", table_name="ib_fetch_items")
    op.drop_index("idx_ib_fetch_items_fetch_run_id", table_name="ib_fetch_items")
    op.drop_table("ib_fetch_items")
    op.drop_table("ib_fetch_runs")
