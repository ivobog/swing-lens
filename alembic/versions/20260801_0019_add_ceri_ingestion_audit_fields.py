"""add ceri ingestion audit fields

Revision ID: 0019_add_ceri_ingestion_audit_fields
Revises: 0018_add_ceri_tables
Create Date: 2026-08-01 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019_add_ceri_ingestion_audit_fields"
down_revision: str | None = "0018_add_ceri_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ceri_ingestion_runs",
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "ceri_ingestion_runs",
        sa.Column("checkpoint_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("ceri_ingestion_runs", sa.Column("duration_ms", sa.Integer(), nullable=True))
    op.alter_column("ceri_ingestion_runs", "retry_count", server_default=None)


def downgrade() -> None:
    op.drop_column("ceri_ingestion_runs", "duration_ms")
    op.drop_column("ceri_ingestion_runs", "checkpoint_json")
    op.drop_column("ceri_ingestion_runs", "retry_count")
