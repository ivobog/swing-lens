"""add background queue claim index

Revision ID: 0037_background_queue_claim_index
Revises: 0036_background_worker_heartbeats
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0037_background_queue_claim_index"
down_revision: str | None = "0036_background_worker_heartbeats"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "idx_background_jobs_queue_claim",
        "background_jobs",
        ["status", "job_type", "run_after", "created_at", "priority"],
    )


def downgrade() -> None:
    op.drop_index("idx_background_jobs_queue_claim", table_name="background_jobs")
