"""Add durable lifecycle quiesce handshake fields.

Revision ID: 0067_worker_quiesce
Revises: 0066_obs_review2_liveness
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0067_worker_quiesce"
down_revision: str | None = "0066_obs_review2_liveness"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "background_workers",
        sa.Column("quiesce_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "background_workers",
        sa.Column("quiesced_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("background_workers", "quiesced_at")
    op.drop_column("background_workers", "quiesce_requested_at")
