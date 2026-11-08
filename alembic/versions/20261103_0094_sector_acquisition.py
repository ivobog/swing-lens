"""Constrained sector worker and durable historical acquisition coordination.

Revision ID: 0094_sector_acquisition
Revises: 0093_market_sector_rotation
"""

import sqlalchemy as sa

from alembic import op

revision = "0094_sector_acquisition"
down_revision = "0093_market_sector_rotation"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sector_refresh_requests",
        sa.Column("request_id", sa.String(64), primary_key=True),
        sa.Column(
            "job_id",
            sa.BigInteger(),
            sa.ForeignKey("background_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.add_column(
        "background_workers",
        sa.Column("operation_profile", sa.String(32), nullable=False, server_default="general"),
    )
    op.create_table(
        "ib_historical_gate",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner", sa.String(32)),
        sa.Column("hostname", sa.String(255)),
        sa.Column("process_id", sa.Integer()),
        sa.Column("process_started_at", sa.DateTime(timezone=True)),
        sa.Column("job_id", sa.BigInteger()),
        sa.Column("execution_token", sa.String(64)),
        sa.Column("acquired_at", sa.DateTime(timezone=True)),
        sa.Column("requests_json", sa.JSON(), nullable=False),
    )
    op.execute(sa.text("INSERT INTO ib_historical_gate (id,requests_json) VALUES (1,'[]')"))


def downgrade():
    op.drop_table("sector_refresh_requests")
    op.drop_table("ib_historical_gate")
    op.drop_column("background_workers", "operation_profile")
