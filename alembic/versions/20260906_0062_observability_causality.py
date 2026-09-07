"""Add observability causality and enqueue-attempt audit.

Revision ID: 0062_observability_causality
Revises: 0061_winner_estimate_policy
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0062_observability_causality"
down_revision = "0061_winner_estimate_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("background_workers", sa.Column("cpu_percent", sa.Float(), nullable=True))
    for name in (
        "root_correlation_id",
        "causation_id",
        "trigger_kind",
        "trigger_name",
        "triggered_by_request_id",
        "fanout_group_id",
    ):
        op.add_column("background_jobs", sa.Column(name, sa.Text(), nullable=True))
    op.add_column(
        "background_jobs",
        sa.Column("coalesced_into_job_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_background_jobs_coalesced_into",
        "background_jobs",
        "background_jobs",
        ["coalesced_into_job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_background_jobs_root_correlation_id",
        "background_jobs",
        ["root_correlation_id", "created_at", "id"],
    )
    op.create_index("idx_background_jobs_fanout_group_id", "background_jobs", ["fanout_group_id"])
    op.create_index(
        "idx_background_jobs_coalesced_into_job_id",
        "background_jobs",
        ["coalesced_into_job_id"],
    )
    op.create_table(
        "background_job_enqueue_attempts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("job_type", sa.Text(), nullable=False),
        sa.Column("root_correlation_id", sa.Text(), nullable=False),
        sa.Column("causation_id", sa.Text(), nullable=False),
        sa.Column("parent_job_id", sa.BigInteger(), nullable=True),
        sa.Column("triggered_by_request_id", sa.Text(), nullable=True),
        sa.Column("request_key", sa.Text(), nullable=True),
        sa.Column("workflow_key", sa.Text(), nullable=True),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("authoritative_job_id", sa.BigInteger(), nullable=True),
        sa.Column("trigger_kind", sa.Text(), nullable=False),
        sa.Column("trigger_name", sa.Text(), nullable=False),
        sa.Column("fanout_group_id", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["parent_job_id"],
            ["background_jobs.id"],
            name="fk_enqueue_attempt_parent_job",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["authoritative_job_id"],
            ["background_jobs.id"],
            name="fk_enqueue_attempt_authoritative_job",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_enqueue_attempts_root_time",
        "background_job_enqueue_attempts",
        ["root_correlation_id", "occurred_at", "id"],
    )
    op.create_index(
        "idx_enqueue_attempts_parent_time",
        "background_job_enqueue_attempts",
        ["parent_job_id", "occurred_at", "id"],
    )
    op.create_index(
        "idx_enqueue_attempts_authoritative_job",
        "background_job_enqueue_attempts",
        ["authoritative_job_id"],
    )


def downgrade() -> None:
    op.drop_table("background_job_enqueue_attempts")
    op.drop_index("idx_background_jobs_coalesced_into_job_id", table_name="background_jobs")
    op.drop_index("idx_background_jobs_fanout_group_id", table_name="background_jobs")
    op.drop_index("idx_background_jobs_root_correlation_id", table_name="background_jobs")
    op.drop_constraint("fk_background_jobs_coalesced_into", "background_jobs", type_="foreignkey")
    for name in reversed(
        (
            "root_correlation_id",
            "causation_id",
            "trigger_kind",
            "trigger_name",
            "triggered_by_request_id",
            "fanout_group_id",
            "coalesced_into_job_id",
        )
    ):
        op.drop_column("background_jobs", name)
    op.drop_column("background_workers", "cpu_percent")
