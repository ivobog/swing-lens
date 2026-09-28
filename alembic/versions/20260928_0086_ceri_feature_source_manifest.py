"""Add immutable CERI feature source manifests.

Legacy calculations remain explicitly unfrozen through nullable manifest links.
No historical fingerprints are invented or backfilled.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0086_ceri_feature_source_manifest"
down_revision: str | None = "0085_ceri_change_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ceri_feature_source_manifests",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("background_job_id", sa.BigInteger(), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("pipeline_run_id", sa.BigInteger(), nullable=False),
        sa.Column("calculation_context_id", sa.BigInteger(), nullable=False),
        sa.Column("batch_index", sa.Integer(), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("as_of_session", sa.Date(), nullable=False),
        sa.Column("calendar_version", sa.Text(), nullable=False),
        sa.Column("configuration_anchor_id", sa.String(length=64), nullable=False),
        sa.Column("configuration_fingerprint", sa.Text(), nullable=False),
        sa.Column("scope_id", sa.String(length=64), nullable=True),
        sa.Column("refresh_cycle_id", sa.String(length=64), nullable=True),
        sa.Column("acquisition_plan_id", sa.String(length=64), nullable=True),
        sa.Column("bundle_fingerprint", sa.Text(), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False),
        sa.Column(
            "manifest_version",
            sa.String(length=64),
            server_default="ceri-feature-source-manifest-v1",
            nullable=False,
        ),
        sa.Column(
            "manifest_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("source_count >= 0", name="ck_ceri_feature_source_manifest_count"),
        sa.ForeignKeyConstraint(["background_job_id"], ["background_jobs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["configuration_anchor_id"],
            ["execution_configuration_anchors.anchor_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["upload_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["calculation_context_id"],
            ["market_calculation_contexts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["scope_id"], ["work_scope_records.scope_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["refresh_cycle_id"],
            ["refresh_cycle_records.refresh_cycle_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["acquisition_plan_id"],
            ["acquisition_plan_records.plan_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("background_job_id", name="uq_ceri_feature_source_manifest_job"),
    )
    op.create_index(
        "ix_ceri_feature_source_manifest_context",
        "ceri_feature_source_manifests",
        ["calculation_context_id"],
    )
    op.create_index(
        "ix_ceri_feature_source_manifest_run",
        "ceri_feature_source_manifests",
        ["run_id", "pipeline_run_id"],
    )
    op.add_column(
        "ceri_feature_build_states",
        sa.Column("source_manifest_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_ceri_feature_build_states_source_manifest",
        "ceri_feature_build_states",
        "ceri_feature_source_manifests",
        ["source_manifest_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_ceri_feature_build_states_manifest",
        "ceri_feature_build_states",
        ["source_manifest_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ceri_feature_build_states_manifest",
        table_name="ceri_feature_build_states",
    )
    op.drop_constraint(
        "fk_ceri_feature_build_states_source_manifest",
        "ceri_feature_build_states",
        type_="foreignkey",
    )
    op.drop_column("ceri_feature_build_states", "source_manifest_id")
    op.drop_index(
        "ix_ceri_feature_source_manifest_run",
        table_name="ceri_feature_source_manifests",
    )
    op.drop_index(
        "ix_ceri_feature_source_manifest_context",
        table_name="ceri_feature_source_manifests",
    )
    op.drop_table("ceri_feature_source_manifests")
