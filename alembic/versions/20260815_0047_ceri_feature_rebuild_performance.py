"""add CERI incremental feature-build state and scoped lookup indexes

Revision ID: 0047_ceri_feature_rebuild_performance
Revises: 0046_owpe_pre11_training_compatibility
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0047_ceri_feature_rebuild_performance"
down_revision: str | None = "0046_owpe_pre11_training_compatibility"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ceri_feature_build_states",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("as_of_session", sa.Date(), nullable=False),
        sa.Column("historical_view_mode", sa.String(length=32), nullable=False),
        sa.Column("config_hash", sa.Text(), nullable=False),
        sa.Column("calculation_version", sa.Text(), nullable=False),
        sa.Column("input_evidence_hash", sa.Text(), nullable=False),
        sa.Column("output_evidence_hash", sa.Text(), nullable=False),
        sa.Column("output_feature_count", sa.Integer(), nullable=False),
        sa.Column("implementation_version", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["ceri_companies.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "as_of_session",
            "historical_view_mode",
            "config_hash",
            "calculation_version",
            name="uq_ceri_feature_build_states_identity",
        ),
    )
    op.create_index(
        "ix_ceri_feature_build_states_company_session",
        "ceri_feature_build_states",
        ["company_id", "as_of_session"],
    )
    op.create_index(
        "ix_ceri_revision_features_batch_identity",
        "ceri_revision_features",
        ["company_id", "as_of_session", "config_hash", "calculation_version"],
    )
    op.create_index(
        "ix_ceri_derived_features_batch_identity",
        "ceri_derived_features",
        ["company_id", "as_of_session", "config_hash", "calculation_version"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ceri_derived_features_batch_identity",
        table_name="ceri_derived_features",
    )
    op.drop_index(
        "ix_ceri_revision_features_batch_identity",
        table_name="ceri_revision_features",
    )
    op.drop_index(
        "ix_ceri_feature_build_states_company_session",
        table_name="ceri_feature_build_states",
    )
    op.drop_table("ceri_feature_build_states")
