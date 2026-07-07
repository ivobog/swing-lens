"""add earnings risk gate fields

Revision ID: 0010_add_earnings_risk_gate
Revises: 0009_history_indexes
Create Date: 2026-07-07 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_add_earnings_risk_gate"
down_revision: str | None = "0009_history_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "raw_company_rows",
        sa.Column("upcoming_earnings_date", sa.Date(), nullable=True),
    )
    op.create_index(
        "idx_raw_company_rows_upcoming_earnings_date",
        "raw_company_rows",
        ["upcoming_earnings_date"],
    )

    op.add_column(
        "combined_results",
        sa.Column("upcoming_earnings_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "combined_results",
        sa.Column("days_until_earnings", sa.Integer(), nullable=True),
    )
    op.add_column(
        "combined_results",
        sa.Column("earnings_risk_level", sa.Text(), nullable=True),
    )
    op.add_column(
        "combined_results",
        sa.Column(
            "earnings_warning_flags",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_combined_results_earnings_risk",
        "combined_results",
        ["earnings_risk_level"],
    )


def downgrade() -> None:
    op.drop_index("idx_combined_results_earnings_risk", table_name="combined_results")
    op.drop_column("combined_results", "earnings_warning_flags")
    op.drop_column("combined_results", "earnings_risk_level")
    op.drop_column("combined_results", "days_until_earnings")
    op.drop_column("combined_results", "upcoming_earnings_date")
    op.drop_index(
        "idx_raw_company_rows_upcoming_earnings_date",
        table_name="raw_company_rows",
    )
    op.drop_column("raw_company_rows", "upcoming_earnings_date")
