"""add ceri revision feature lineage

Revision ID: 0020_add_ceri_revision_feature_lineage
Revises: 0019_add_ceri_ingestion_audit_fields
Create Date: 2026-08-01 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020_add_ceri_revision_feature_lineage"
down_revision: str | None = "0019_add_ceri_ingestion_audit_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ceri_revision_features",
        sa.Column(
            "source_observation_ids_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "ceri_revision_features",
        sa.Column("provider_selection_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "ceri_revision_features",
        sa.Column("unavailable_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "ceri_revision_features",
        sa.Column("evidence_hash", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ceri_revision_features", "evidence_hash")
    op.drop_column("ceri_revision_features", "unavailable_reason")
    op.drop_column("ceri_revision_features", "provider_selection_reason")
    op.drop_column("ceri_revision_features", "source_observation_ids_json")
