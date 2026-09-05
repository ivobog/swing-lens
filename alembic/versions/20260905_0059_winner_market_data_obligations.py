"""Add durable Winner market-data obligations.

Revision ID: 0059_winner_market_data_obligations
Revises: 0058_winner_temporal_integrity
Create Date: 2026-09-05
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0059_winner_market_data_obligations"
down_revision = "0058_winner_temporal_integrity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "winner_market_data_obligations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("prediction_id", sa.BigInteger(), nullable=False),
        sa.Column("forward_outcome_id", sa.BigInteger(), nullable=False),
        sa.Column("ib_contract_id", sa.BigInteger(), nullable=True),
        sa.Column("ticker_snapshot", sa.Text(), nullable=False),
        sa.Column("ib_conid_snapshot", sa.BigInteger(), nullable=True),
        sa.Column("symbol_snapshot", sa.Text(), nullable=True),
        sa.Column("local_symbol_snapshot", sa.Text(), nullable=True),
        sa.Column("exchange_snapshot", sa.Text(), nullable=True),
        sa.Column("primary_exchange_snapshot", sa.Text(), nullable=True),
        sa.Column("currency_snapshot", sa.Text(), nullable=True),
        sa.Column("sec_type_snapshot", sa.Text(), nullable=True),
        sa.Column("trading_class_snapshot", sa.Text(), nullable=True),
        sa.Column("entry_session", sa.Date(), nullable=False),
        sa.Column("required_through_session", sa.Date(), nullable=False),
        sa.Column("required_sessions_json", postgresql.JSONB(), nullable=False),
        sa.Column("timeframe", sa.Text(), server_default="1 day", nullable=False),
        sa.Column("what_to_show", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("first_missing_session", sa.Date(), nullable=True),
        sa.Column("last_missing_session", sa.Date(), nullable=True),
        sa.Column("price_series_watermark", sa.Text(), nullable=False),
        sa.Column("last_evaluated_watermark", sa.Text(), nullable=True),
        sa.Column(
            "last_checked_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('FETCH_REQUIRED', 'SATISFIED', 'IDENTITY_BLOCKED', "
            "'UNAVAILABLE', 'FAILED')",
            name="ck_winner_market_data_obligation_status",
        ),
        sa.CheckConstraint(
            "what_to_show IN ('ADJUSTED_LAST', 'TRADES')",
            name="ck_winner_market_data_obligation_basis",
        ),
        sa.ForeignKeyConstraint(
            ["prediction_id"],
            ["winner_prediction_snapshots.id"],
            name="fk_winner_market_data_obligation_prediction",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["forward_outcome_id"],
            ["winner_forward_outcomes.id"],
            name="fk_winner_market_data_obligation_outcome",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ib_contract_id"],
            ["ib_contracts.id"],
            name="fk_winner_market_data_obligation_contract",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "forward_outcome_id",
            "what_to_show",
            name="uq_winner_market_data_obligation_outcome_basis",
        ),
    )
    op.create_index(
        "idx_winner_market_data_obligation_status_range",
        "winner_market_data_obligations",
        ["status", "what_to_show", "first_missing_session", "last_missing_session"],
    )
    op.create_index(
        "idx_winner_market_data_obligation_contract_status",
        "winner_market_data_obligations",
        ["ib_contract_id", "status"],
    )
    op.create_index(
        "idx_winner_market_data_obligation_prediction",
        "winner_market_data_obligations",
        ["prediction_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_winner_market_data_obligation_prediction",
        table_name="winner_market_data_obligations",
    )
    op.drop_index(
        "idx_winner_market_data_obligation_contract_status",
        table_name="winner_market_data_obligations",
    )
    op.drop_index(
        "idx_winner_market_data_obligation_status_range",
        table_name="winner_market_data_obligations",
    )
    op.drop_table("winner_market_data_obligations")
