"""retain raw IB histogram evidence

Revision ID: 0033_retain_histogram_raw
Revises: 0032_harden_ibmi_phase1
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0033_retain_histogram_raw"
down_revision: str | None = "0032_harden_ibmi_phase1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE ib_histogram_snapshots "
        "ADD COLUMN raw_bins_json jsonb NOT NULL DEFAULT '[]'::jsonb"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE ib_histogram_snapshots DROP COLUMN raw_bins_json")
