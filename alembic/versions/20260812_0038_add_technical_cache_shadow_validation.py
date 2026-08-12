"""add technical cache shadow validation state

Revision ID: 0038_technical_cache_shadow_validation
Revises: 0037_background_queue_claim_index
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0038_technical_cache_shadow_validation"
down_revision: str | None = "0037_background_queue_claim_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "technical_feature_artifacts",
        sa.Column(
            "shadow_validation_status",
            sa.Text(),
            server_default="UNVALIDATED",
            nullable=False,
        ),
    )
    op.add_column(
        "technical_feature_artifacts",
        sa.Column(
            "shadow_validation_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "technical_feature_artifacts",
        sa.Column(
            "shadow_mismatch_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "technical_feature_artifacts",
        sa.Column("last_shadow_validated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "technical_feature_artifacts",
        sa.Column(
            "last_shadow_mismatch_json",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_technical_feature_artifacts_shadow_status",
        "technical_feature_artifacts",
        "shadow_validation_status IN ('UNVALIDATED', 'MATCH', 'MISMATCH')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_technical_feature_artifacts_shadow_status",
        "technical_feature_artifacts",
        type_="check",
    )
    op.drop_column("technical_feature_artifacts", "last_shadow_mismatch_json")
    op.drop_column("technical_feature_artifacts", "last_shadow_validated_at")
    op.drop_column("technical_feature_artifacts", "shadow_mismatch_count")
    op.drop_column("technical_feature_artifacts", "shadow_validation_count")
    op.drop_column("technical_feature_artifacts", "shadow_validation_status")
