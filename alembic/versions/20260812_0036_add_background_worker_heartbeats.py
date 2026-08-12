"""add background worker heartbeats

Revision ID: 0036_background_worker_heartbeats
Revises: 0035_ceri_workflow_identity
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0036_background_worker_heartbeats"
down_revision: str | None = "0035_ceri_workflow_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "background_workers",
        sa.Column("worker_id", sa.Text(), nullable=False),
        sa.Column(
            "queues_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("hostname", sa.Text(), nullable=True),
        sa.Column("process_id", sa.Integer(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "heartbeat_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("stopping_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("worker_id"),
    )
    op.create_index(
        "idx_background_workers_heartbeat",
        "background_workers",
        ["heartbeat_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_background_workers_heartbeat", table_name="background_workers")
    op.drop_table("background_workers")
