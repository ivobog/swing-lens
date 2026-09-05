"""Add Winner maturation workflow lineage and database single-flight.

Revision ID: 0057_winner_maturation
Revises: 0056_cover_ceri_freshness
Create Date: 2026-09-05
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0057_winner_maturation"
down_revision = "0056_cover_ceri_freshness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("background_jobs", sa.Column("root_job_id", sa.BigInteger(), nullable=True))
    op.add_column("background_jobs", sa.Column("parent_job_id", sa.BigInteger(), nullable=True))
    op.add_column("background_jobs", sa.Column("continuation_depth", sa.Integer(), nullable=True))
    op.add_column("background_jobs", sa.Column("trigger_source", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_background_jobs_root_job",
        "background_jobs",
        "background_jobs",
        ["root_job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_background_jobs_parent_job",
        "background_jobs",
        "background_jobs",
        ["parent_job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("idx_background_jobs_root_job_id", "background_jobs", ["root_job_id"])
    op.create_index("idx_background_jobs_parent_job_id", "background_jobs", ["parent_job_id"])
    op.create_index(
        "uq_background_jobs_active_winner_maturation_workflow",
        "background_jobs",
        ["workflow_key"],
        unique=True,
        postgresql_where=sa.text(
            "job_type = 'WINNER_OUTCOME_MATURATION' "
            "AND workflow_key IS NOT NULL "
            "AND status IN ('QUEUED', 'RUNNING', 'RECOVERING')"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_background_jobs_active_winner_maturation_workflow",
        table_name="background_jobs",
    )
    op.drop_index("idx_background_jobs_parent_job_id", table_name="background_jobs")
    op.drop_index("idx_background_jobs_root_job_id", table_name="background_jobs")
    op.drop_constraint("fk_background_jobs_parent_job", "background_jobs", type_="foreignkey")
    op.drop_constraint("fk_background_jobs_root_job", "background_jobs", type_="foreignkey")
    op.drop_column("background_jobs", "trigger_source")
    op.drop_column("background_jobs", "continuation_depth")
    op.drop_column("background_jobs", "parent_job_id")
    op.drop_column("background_jobs", "root_job_id")
