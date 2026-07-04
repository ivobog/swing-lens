"""add fundamentals v2 columns

Revision ID: 0005_add_fundamentals_v2_columns
Revises: 0004_expand_ib_fetch_persistence
Create Date: 2026-07-04 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005_add_fundamentals_v2_columns"
down_revision: str | None = "0004_expand_ib_fetch_persistence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

V2_NUMERIC_COLUMNS = [
    "growth_quality_score",
    "profitability_quality_score",
    "fcf_quality_score",
    "earnings_quality_score",
    "capital_efficiency_score",
    "balance_sheet_quality_score",
    "valuation_quality_score",
    "forward_quality_score",
    "shareholder_quality_score",
    "liquidity_risk_score",
    "data_coverage_score",
]


def upgrade() -> None:
    for column_name in V2_NUMERIC_COLUMNS:
        op.add_column("fundamental_scores", sa.Column(column_name, sa.Numeric(), nullable=True))

    op.add_column(
        "fundamental_scores",
        sa.Column("scoring_model_version", sa.Text(), nullable=True),
    )
    op.add_column(
        "fundamental_scores",
        sa.Column(
            "v2_warning_flags_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("fundamental_scores", "v2_warning_flags_json")
    op.drop_column("fundamental_scores", "scoring_model_version")
    for column_name in reversed(V2_NUMERIC_COLUMNS):
        op.drop_column("fundamental_scores", column_name)
