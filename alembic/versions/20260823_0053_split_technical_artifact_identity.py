"""split technical feature and final scoring identities

Revision ID: 0053_split_artifact_identity
Revises: 0052_technical_scoring_v5
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0053_split_artifact_identity"
down_revision: str | None = "0052_technical_scoring_v5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "technical_feature_artifacts",
        sa.Column("feature_config_hash", sa.Text(), nullable=True),
    )
    # Existing rows used scoring_config_hash as the combined cache identity.  Preserve
    # those rows as readable historical artifacts; newly written rows use a dedicated
    # feature-generation hash and no longer key on final v5 composition settings.
    op.execute(
        "UPDATE technical_feature_artifacts "
        "SET feature_config_hash = scoring_config_hash "
        "WHERE feature_config_hash IS NULL"
    )
    op.alter_column("technical_feature_artifacts", "feature_config_hash", nullable=False)


def downgrade() -> None:
    op.drop_column("technical_feature_artifacts", "feature_config_hash")
