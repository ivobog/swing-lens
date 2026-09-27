"""Add the scoped temporal lookup used by CERI change detection."""

from collections.abc import Sequence

from alembic import op

revision: str = "0085_ceri_change_scope"
down_revision: str | None = "0084_technical_recovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_ceri_score_snapshots_company_temporal",
        "ceri_score_snapshots",
        ["company_id", "as_of_session", "cutoff_at", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ceri_score_snapshots_company_temporal",
        table_name="ceri_score_snapshots",
    )
