"""add durable worker process-instance and supervisor reliability state

Revision ID: 0054_worker_instance_reliability
Revises: 0053_split_artifact_identity
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0054_worker_instance_reliability"
down_revision: str | None = "0053_split_artifact_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("background_jobs", sa.Column("worker_instance_id", sa.Text(), nullable=True))
    op.create_index(
        "idx_background_jobs_worker_instance",
        "background_jobs",
        ["worker_id", "worker_instance_id", "status"],
    )
    op.add_column(
        "background_workers", sa.Column("launcher_process_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "background_workers",
        sa.Column("process_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "background_workers",
        sa.Column("generation", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.create_table(
        "background_supervisors",
        sa.Column("worker_id", sa.Text(), nullable=False),
        sa.Column("instance_id", sa.Text(), nullable=False),
        sa.Column("hostname", sa.Text(), nullable=False),
        sa.Column("process_id", sa.Integer(), nullable=False),
        sa.Column("process_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generation", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "heartbeat_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("stopping_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("worker_id"),
    )
    op.create_index(
        "idx_background_supervisors_heartbeat",
        "background_supervisors",
        ["heartbeat_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_background_supervisors_heartbeat", table_name="background_supervisors"
    )
    op.drop_table("background_supervisors")
    op.drop_column("background_workers", "generation")
    op.drop_column("background_workers", "process_started_at")
    op.drop_column("background_workers", "launcher_process_id")
    op.drop_index("idx_background_jobs_worker_instance", table_name="background_jobs")
    op.drop_column("background_jobs", "worker_instance_id")
