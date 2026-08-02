"""add price bar revisions and ceri corrections

Revision ID: 0022_price_bar_revisions_ceri_corrections
Revises: 0021_add_ceri_earnings_consensus_reason
Create Date: 2026-08-02 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0022_price_bar_revisions_ceri_corrections"
down_revision: str | None = "0021_add_ceri_earnings_consensus_reason"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "price_bar_revisions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("price_bar_id", sa.BigInteger(), nullable=False),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("bar_date", sa.Date(), nullable=False),
        sa.Column("timeframe", sa.Text(), nullable=False),
        sa.Column("what_to_show", sa.Text(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("previous_data_hash", sa.Text(), nullable=True),
        sa.Column("new_data_hash", sa.Text(), nullable=False),
        sa.Column(
            "previous_values_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "new_values_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("adjustment_type", sa.Text(), nullable=True),
        sa.Column("fetch_run_id", sa.BigInteger(), nullable=True),
        sa.Column("fetch_item_id", sa.BigInteger(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["fetch_item_id"], ["ib_fetch_items.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["fetch_run_id"], ["ib_fetch_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["price_bar_id"], ["price_bars.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "price_bar_id",
            "revision_number",
            name="uq_price_bar_revisions_price_bar_revision",
        ),
    )
    op.create_index(
        "idx_price_bar_revisions_natural_key",
        "price_bar_revisions",
        ["ticker", "bar_date", "timeframe", "what_to_show"],
    )
    op.create_index(
        "idx_price_bar_revisions_observed_at",
        "price_bar_revisions",
        ["observed_at"],
    )

    op.drop_constraint(
        "uq_ceri_source_records_provider_record",
        "ceri_source_records",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_ceri_source_records_provider_record",
        "ceri_source_records",
        ["provider", "dataset", "provider_record_id", "content_hash"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_ceri_source_records_provider_record",
        "ceri_source_records",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_ceri_source_records_provider_record",
        "ceri_source_records",
        ["provider", "dataset", "provider_record_id"],
    )

    op.drop_index("idx_price_bar_revisions_observed_at", table_name="price_bar_revisions")
    op.drop_index("idx_price_bar_revisions_natural_key", table_name="price_bar_revisions")
    op.drop_table("price_bar_revisions")
