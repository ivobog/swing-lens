"""add ceri earnings consensus reason

Revision ID: 0021_add_ceri_earnings_consensus_reason
Revises: 0020_add_ceri_revision_feature_lineage
Create Date: 2026-08-01 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_add_ceri_earnings_consensus_reason"
down_revision: str | None = "0020_add_ceri_revision_feature_lineage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ceri_earnings_actuals",
        sa.Column("consensus_selection_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ceri_earnings_actuals", "consensus_selection_reason")
