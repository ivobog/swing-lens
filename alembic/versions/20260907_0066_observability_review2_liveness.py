"""separate process and functional control-loop liveness

Revision ID: 0066_obs_review2_liveness
Revises: 0065_observability_remediation
"""

import sqlalchemy as sa
from alembic import op

revision = "0066_obs_review2_liveness"
down_revision = "0065_observability_remediation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "background_workers",
        sa.Column("control_loop_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "background_supervisors",
        sa.Column("control_loop_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("background_supervisors", "control_loop_heartbeat_at")
    op.drop_column("background_workers", "control_loop_heartbeat_at")
