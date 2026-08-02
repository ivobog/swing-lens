"""add ceri earnings consensus reason

Revision ID: 0021_add_ceri_earnings_consensus_reason
Revises: 0020_add_ceri_revision_feature_lineage
Create Date: 2026-08-01 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from alembic import op

revision: str = "0021_add_ceri_earnings_consensus_reason"
down_revision: str | None = "0020_add_ceri_revision_feature_lineage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(bind: Connection, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    bind = op.get_bind()
    if not _has_column(bind, table_name, column.name):
        op.add_column(table_name, column)


def _drop_column_if_exists(table_name: str, column_name: str) -> None:
    bind = op.get_bind()
    if _has_column(bind, table_name, column_name):
        op.drop_column(table_name, column_name)


def upgrade() -> None:
    _add_column_if_missing(
        "ceri_earnings_actuals",
        sa.Column("consensus_selection_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    _drop_column_if_exists("ceri_earnings_actuals", "consensus_selection_reason")
