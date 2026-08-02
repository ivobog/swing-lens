"""add ceri ingestion audit fields

Revision ID: 0019_add_ceri_ingestion_audit_fields
Revises: 0018_add_ceri_tables
Create Date: 2026-08-01 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection

from alembic import op

revision: str = "0019_add_ceri_ingestion_audit_fields"
down_revision: str | None = "0018_add_ceri_tables"
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
        "ceri_ingestion_runs",
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
    )
    _add_column_if_missing(
        "ceri_ingestion_runs",
        sa.Column("checkpoint_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    _add_column_if_missing(
        "ceri_ingestion_runs",
        sa.Column("duration_ms", sa.Integer(), nullable=True),
    )
    if _has_column(op.get_bind(), "ceri_ingestion_runs", "retry_count"):
        op.alter_column("ceri_ingestion_runs", "retry_count", server_default=None)


def downgrade() -> None:
    _drop_column_if_exists("ceri_ingestion_runs", "duration_ms")
    _drop_column_if_exists("ceri_ingestion_runs", "checkpoint_json")
    _drop_column_if_exists("ceri_ingestion_runs", "retry_count")
