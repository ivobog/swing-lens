"""Add explicit immediate-job causation reference.

Revision ID: 0063_observability_triggered_by_job
Revises: 0062_observability_causality
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0063_observability_triggered_by_job"
down_revision = "0062_observability_causality"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "background_jobs",
        sa.Column("triggered_by_job_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_background_jobs_triggered_by_job",
        "background_jobs",
        "background_jobs",
        ["triggered_by_job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_background_jobs_triggered_by_job_id",
        "background_jobs",
        ["triggered_by_job_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_background_jobs_triggered_by_job_id", table_name="background_jobs")
    op.drop_constraint(
        "fk_background_jobs_triggered_by_job",
        "background_jobs",
        type_="foreignkey",
    )
    op.drop_column("background_jobs", "triggered_by_job_id")
