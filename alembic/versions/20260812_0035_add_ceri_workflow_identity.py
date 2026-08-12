"""add CERI workflow identity

Revision ID: 0035_ceri_workflow_identity
Revises: 0034_slse_dashboard_indexes
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0035_ceri_workflow_identity"
down_revision: str | None = "0034_slse_dashboard_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("background_jobs", sa.Column("workflow_key", sa.Text(), nullable=True))
    op.create_index(
        "idx_background_jobs_workflow_type_status",
        "background_jobs",
        ["workflow_key", "job_type", "status"],
    )
    op.create_index(
        "uq_background_jobs_workflow_stage",
        "background_jobs",
        ["workflow_key", "job_type", "request_key"],
        unique=True,
        postgresql_where=sa.text("workflow_key IS NOT NULL AND request_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_background_jobs_workflow_stage", table_name="background_jobs")
    op.drop_index(
        "idx_background_jobs_workflow_type_status",
        table_name="background_jobs",
    )
    op.drop_column("background_jobs", "workflow_key")
