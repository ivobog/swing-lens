"""add immutable parallel CERI controlled replay provenance

Revision ID: 0044_ceri_controlled_replays
Revises: 0043_ceri_run102_relative_evidence
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0044_ceri_controlled_replays"
down_revision: str | None = "0043_ceri_run102_relative_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ceri_controlled_replays",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("replay_identifier", sa.Text(), nullable=False),
        sa.Column("source_run_id", sa.BigInteger(), nullable=False),
        sa.Column("original_cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("git_sha", sa.String(length=64), nullable=False),
        sa.Column("processor_signature", sa.Text(), nullable=False),
        sa.Column("config_version", sa.Text(), nullable=False),
        sa.Column("config_hash", sa.Text(), nullable=False),
        sa.Column("calculation_version", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("universe_count", sa.Integer(), nullable=False),
        sa.Column("feature_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("snapshot_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("changed_feature_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("original_state_hash", sa.Text(), nullable=False),
        sa.Column("replay_state_hash", sa.Text(), nullable=True),
        sa.Column("certification_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("impact_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("feature_changes_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["source_run_id"], ["upload_runs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("replay_identifier"),
    )
    op.create_index(
        "ix_ceri_controlled_replays_source_run",
        "ceri_controlled_replays",
        ["source_run_id"],
    )
    op.create_index(
        "ix_ceri_controlled_replays_status",
        "ceri_controlled_replays",
        ["status"],
    )
    op.add_column(
        "ceri_revision_features",
        sa.Column("controlled_replay_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_ceri_revision_features_controlled_replay",
        "ceri_revision_features",
        "ceri_controlled_replays",
        ["controlled_replay_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_ceri_revision_features_controlled_replay",
        "ceri_revision_features",
        ["controlled_replay_id"],
    )
    op.add_column(
        "ceri_score_snapshots",
        sa.Column("controlled_replay_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_ceri_score_snapshots_controlled_replay",
        "ceri_score_snapshots",
        "ceri_controlled_replays",
        ["controlled_replay_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_ceri_score_snapshots_controlled_replay",
        "ceri_score_snapshots",
        ["controlled_replay_id"],
    )
    op.create_unique_constraint(
        "uq_ceri_score_snapshots_controlled_replay_company",
        "ceri_score_snapshots",
        ["controlled_replay_id", "company_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_ceri_score_snapshots_controlled_replay_company",
        "ceri_score_snapshots",
        type_="unique",
    )
    op.drop_index(
        "ix_ceri_score_snapshots_controlled_replay",
        table_name="ceri_score_snapshots",
    )
    op.drop_constraint(
        "fk_ceri_score_snapshots_controlled_replay",
        "ceri_score_snapshots",
        type_="foreignkey",
    )
    op.drop_column("ceri_score_snapshots", "controlled_replay_id")
    op.drop_index(
        "ix_ceri_revision_features_controlled_replay",
        table_name="ceri_revision_features",
    )
    op.drop_constraint(
        "fk_ceri_revision_features_controlled_replay",
        "ceri_revision_features",
        type_="foreignkey",
    )
    op.drop_column("ceri_revision_features", "controlled_replay_id")
    op.drop_index("ix_ceri_controlled_replays_status", table_name="ceri_controlled_replays")
    op.drop_index(
        "ix_ceri_controlled_replays_source_run",
        table_name="ceri_controlled_replays",
    )
    op.drop_table("ceri_controlled_replays")
