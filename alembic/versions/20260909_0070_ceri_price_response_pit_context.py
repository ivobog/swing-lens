"""Persist CERI price-response point-in-time context.

Revision ID: 0070_ceri_price_response_pit_context
Revises: 0069_lifecycle_current_selection
Create Date: 2026-09-09
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0070_ceri_price_response_pit_context"
down_revision: str | None = "0069_lifecycle_current_selection"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Nullable by design: legacy rows retain an explicit unknown boundary.  No
    # historical knowledge timestamp or context identity is fabricated.
    op.add_column(
        "ceri_price_response_features",
        sa.Column("calculation_cutoff_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ceri_price_response_features",
        sa.Column("calculation_context_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "ceri_price_response_features",
        sa.Column("calendar_version", sa.Text(), nullable=True),
    )
    op.create_foreign_key(
        "fk_ceri_price_response_features_calculation_context",
        "ceri_price_response_features",
        "market_calculation_contexts",
        ["calculation_context_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_ceri_price_response_features_cutoff",
        "ceri_price_response_features",
        ["calculation_cutoff_at"],
    )
    op.create_index(
        "ix_ceri_price_response_features_context",
        "ceri_price_response_features",
        ["calculation_context_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ceri_price_response_features_context",
        table_name="ceri_price_response_features",
    )
    op.drop_index(
        "ix_ceri_price_response_features_cutoff",
        table_name="ceri_price_response_features",
    )
    op.drop_constraint(
        "fk_ceri_price_response_features_calculation_context",
        "ceri_price_response_features",
        type_="foreignkey",
    )
    op.drop_column("ceri_price_response_features", "calendar_version")
    op.drop_column("ceri_price_response_features", "calculation_context_id")
    op.drop_column("ceri_price_response_features", "calculation_cutoff_at")
