"""add IB fetch decision telemetry

Revision ID: 0039_ib_fetch_decision_telemetry
Revises: 0038_technical_cache_shadow_validation
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0039_ib_fetch_decision_telemetry"
down_revision: str | None = "0038_technical_cache_shadow_validation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ib_fetch_runs",
        sa.Column(
            "decision_counts_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "ib_fetch_items",
        sa.Column(
            "decision_metadata_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_index(
        "idx_ib_fetch_items_full_backfill_evidence",
        "ib_fetch_items",
        ["ticker", "what_to_show", "duration", "bar_size"],
        postgresql_where=sa.text(
            "status = 'SUCCESS' AND action IN ('FULL_BACKFILL', 'FORCE_REFRESH')"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_ib_fetch_items_full_backfill_evidence",
        table_name="ib_fetch_items",
    )
    op.drop_column("ib_fetch_items", "decision_metadata_json")
    op.drop_column("ib_fetch_runs", "decision_counts_json")
