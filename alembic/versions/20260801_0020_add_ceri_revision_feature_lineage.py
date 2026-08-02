"""add ceri revision feature lineage

Revision ID: 0020_add_ceri_revision_feature_lineage
Revises: 0019_add_ceri_ingestion_audit_fields
Create Date: 2026-08-01 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection

from alembic import op

revision: str = "0020_add_ceri_revision_feature_lineage"
down_revision: str | None = "0019_add_ceri_ingestion_audit_fields"
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
        "ceri_revision_features",
        sa.Column(
            "source_observation_ids_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    _add_column_if_missing(
        "ceri_revision_features",
        sa.Column("provider_selection_reason", sa.Text(), nullable=True),
    )
    _add_column_if_missing(
        "ceri_revision_features",
        sa.Column("unavailable_reason", sa.Text(), nullable=True),
    )
    _add_column_if_missing(
        "ceri_revision_features",
        sa.Column("evidence_hash", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    _drop_column_if_exists("ceri_revision_features", "evidence_hash")
    _drop_column_if_exists("ceri_revision_features", "unavailable_reason")
    _drop_column_if_exists("ceri_revision_features", "provider_selection_reason")
    _drop_column_if_exists("ceri_revision_features", "source_observation_ids_json")
