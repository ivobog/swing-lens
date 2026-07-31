"""harden background job leases

Revision ID: 0015_harden_job_leases
Revises: 0014_sector_metadata
Create Date: 2026-07-31 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015_harden_job_leases"
down_revision: str | None = "0014_sector_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("background_jobs", sa.Column("lease_owner", sa.Text(), nullable=True))
    op.add_column("background_jobs", sa.Column("execution_token", sa.Text(), nullable=True))
    op.add_column(
        "background_jobs",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "background_jobs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "background_jobs",
        sa.Column(
            "operational_metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )

    background_jobs = sa.table(
        "background_jobs",
        sa.column("status", sa.Text),
        sa.column("worker_id", sa.Text),
        sa.column("lease_owner", sa.Text),
        sa.column("locked_at", sa.DateTime(timezone=True)),
        sa.column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.column("lease_expires_at", sa.DateTime(timezone=True)),
    )
    op.get_bind().execute(
        background_jobs.update()
        .where(background_jobs.c.status == "RUNNING")
        .where(background_jobs.c.locked_at.is_not(None))
        .values(
            lease_owner=background_jobs.c.worker_id,
            heartbeat_at=background_jobs.c.locked_at,
            lease_expires_at=background_jobs.c.locked_at + sa.text("interval '15 minutes'"),
        )
    )

    op.create_index(
        "idx_background_jobs_lease_expires_at",
        "background_jobs",
        ["lease_expires_at"],
    )
    op.create_index(
        "idx_background_jobs_execution_token",
        "background_jobs",
        ["execution_token"],
    )


def downgrade() -> None:
    op.drop_index("idx_background_jobs_execution_token", table_name="background_jobs")
    op.drop_index("idx_background_jobs_lease_expires_at", table_name="background_jobs")
    op.drop_column("background_jobs", "operational_metadata_json")
    op.drop_column("background_jobs", "lease_expires_at")
    op.drop_column("background_jobs", "heartbeat_at")
    op.drop_column("background_jobs", "execution_token")
    op.drop_column("background_jobs", "lease_owner")
