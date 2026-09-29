"""Add explicit pipeline dependency and continuation authority.

The table records asynchronous handoffs without rewriting historical pipeline
or job rows. Existing runs therefore remain forensic evidence and are reported
by runtime invariant inspection instead of being backfilled speculatively.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0087_pipeline_dependencies"
down_revision: str | None = "0086_ceri_feature_source_manifest"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pipeline_dependencies",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("pipeline_run_id", sa.BigInteger(), nullable=False),
        sa.Column("dependency_type", sa.Text(), nullable=False),
        sa.Column("dependency_key", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column(
            "required_subjects_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("continuation_step", sa.Text(), nullable=False),
        sa.Column("continuation_identity", sa.Text(), nullable=False),
        sa.Column("root_job_id", sa.BigInteger(), nullable=False),
        sa.Column("child_job_id", sa.BigInteger(), nullable=True),
        sa.Column("continuation_job_id", sa.BigInteger(), nullable=True),
        sa.Column("root_worker_instance_id", sa.Text(), nullable=True),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('PENDING_ENQUEUE', 'QUEUED', 'RUNNING', "
            "'COMPLETED', 'FAILED', 'CANCELLED')",
            name="ck_pipeline_dependencies_state",
        ),
        sa.ForeignKeyConstraint(
            ["pipeline_run_id"], ["pipeline_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["root_job_id"],
            ["background_jobs.id"],
            name="fk_pipeline_dependencies_root_job",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["child_job_id"],
            ["background_jobs.id"],
            name="fk_pipeline_dependencies_child_job",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["continuation_job_id"],
            ["background_jobs.id"],
            name="fk_pipeline_dependencies_continuation_job",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dependency_key", name="uq_pipeline_dependencies_key"),
        sa.UniqueConstraint(
            "continuation_identity", name="uq_pipeline_dependencies_continuation_identity"
        ),
        sa.UniqueConstraint("child_job_id", name="uq_pipeline_dependencies_child_job"),
        sa.UniqueConstraint(
            "continuation_job_id", name="uq_pipeline_dependencies_continuation_job"
        ),
    )
    op.create_index(
        "idx_pipeline_dependencies_pipeline",
        "pipeline_dependencies",
        ["pipeline_run_id", "state"],
    )
    op.create_index(
        "idx_pipeline_dependencies_root_job",
        "pipeline_dependencies",
        ["root_job_id"],
    )
    op.create_index(
        "uq_pipeline_dependencies_active_type",
        "pipeline_dependencies",
        ["pipeline_run_id", "dependency_type"],
        unique=True,
        postgresql_where=sa.text(
            "state IN ('PENDING_ENQUEUE', 'QUEUED', 'RUNNING')"
        ),
    )
    op.add_column(
        "background_jobs",
        sa.Column("pipeline_dependency_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_background_jobs_pipeline_dependency",
        "background_jobs",
        "pipeline_dependencies",
        ["pipeline_dependency_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_background_jobs_pipeline_dependency",
        "background_jobs",
        ["pipeline_dependency_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_background_jobs_pipeline_dependency", table_name="background_jobs")
    op.drop_constraint(
        "fk_background_jobs_pipeline_dependency", "background_jobs", type_="foreignkey"
    )
    op.drop_column("background_jobs", "pipeline_dependency_id")
    op.drop_index(
        "uq_pipeline_dependencies_active_type", table_name="pipeline_dependencies"
    )
    op.drop_index("idx_pipeline_dependencies_root_job", table_name="pipeline_dependencies")
    op.drop_index("idx_pipeline_dependencies_pipeline", table_name="pipeline_dependencies")
    op.drop_table("pipeline_dependencies")
