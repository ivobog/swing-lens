"""add background jobs

Revision ID: 0007_add_background_jobs
Revises: 0006_add_technical_v4_columns
Create Date: 2026-07-07 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_add_background_jobs"
down_revision: str | None = "0006_add_technical_v4_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "background_jobs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("job_type", sa.Text(), nullable=False),
        sa.Column("related_run_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), server_default=sa.text("100"), nullable=False),
        sa.Column(
            "payload_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_retries", sa.Integer(), server_default=sa.text("3"), nullable=False),
        sa.Column(
            "requested_cancel",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("worker_id", sa.Text(), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_after", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_background_jobs_status_priority",
        "background_jobs",
        ["status", "priority", "run_after", "created_at"],
    )
    op.create_index(
        "idx_background_jobs_related_run_id",
        "background_jobs",
        ["related_run_id"],
    )
    op.create_index(
        "idx_background_jobs_locked_at",
        "background_jobs",
        ["locked_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_background_jobs_locked_at", table_name="background_jobs")
    op.drop_index("idx_background_jobs_related_run_id", table_name="background_jobs")
    op.drop_index("idx_background_jobs_status_priority", table_name="background_jobs")
    op.drop_table("background_jobs")
