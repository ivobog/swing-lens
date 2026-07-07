"""add pipeline tables

Revision ID: 0008_add_pipeline_tables
Revises: 0007_add_background_jobs
Create Date: 2026-07-07 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_add_pipeline_tables"
down_revision: str | None = "0007_add_background_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("upload_run_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("current_step", sa.Text(), nullable=True),
        sa.Column("requested_by", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["upload_run_id"], ["upload_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_pipeline_runs_upload_run_id", "pipeline_runs", ["upload_run_id"])
    op.create_index("idx_pipeline_runs_status", "pipeline_runs", ["status"])
    op.create_index("idx_pipeline_runs_created_at", "pipeline_runs", ["created_at"])

    op.create_table(
        "pipeline_steps",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("pipeline_run_id", sa.BigInteger(), nullable=False),
        sa.Column("step_name", sa.Text(), nullable=False),
        sa.Column("step_order", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "pipeline_run_id",
            "step_name",
            name="uq_pipeline_steps_pipeline_step",
        ),
    )
    op.create_index(
        "idx_pipeline_steps_pipeline_run_id",
        "pipeline_steps",
        ["pipeline_run_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_pipeline_steps_pipeline_run_id", table_name="pipeline_steps")
    op.drop_table("pipeline_steps")
    op.drop_index("idx_pipeline_runs_created_at", table_name="pipeline_runs")
    op.drop_index("idx_pipeline_runs_status", table_name="pipeline_runs")
    op.drop_index("idx_pipeline_runs_upload_run_id", table_name="pipeline_runs")
    op.drop_table("pipeline_runs")
