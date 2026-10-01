"""Add fail-closed pipeline execution authority.

Existing pipeline rows intentionally remain NULL. This preserves their
forensic state while making their queued or expired jobs non-claimable. New
pipelines receive ACTIVE authority through the application and server default.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0089_pipeline_execution_authority"
down_revision: str | None = "0088_ceri_run_source_lineage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pipeline_runs",
        sa.Column("execution_authority_state", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "pipeline_runs",
        sa.Column("execution_authority_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "pipeline_runs",
        sa.Column("execution_authority_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_pipeline_execution_authority_state",
        "pipeline_runs",
        "execution_authority_state IS NULL OR execution_authority_state IN "
        "('ACTIVE', 'REVOKED', 'QUARANTINED', 'RETIRED')",
    )
    op.create_index(
        "idx_pipeline_runs_execution_authority",
        "pipeline_runs",
        ["execution_authority_state", "status"],
    )
    # Set defaults only after the columns exist so historical rows remain
    # explicitly ungranted rather than being rewritten as active.
    op.alter_column(
        "pipeline_runs",
        "execution_authority_state",
        server_default=sa.text("'ACTIVE'"),
    )
    op.alter_column(
        "pipeline_runs",
        "execution_authority_changed_at",
        server_default=sa.text("now()"),
    )


def downgrade() -> None:
    op.drop_index("idx_pipeline_runs_execution_authority", table_name="pipeline_runs")
    op.drop_constraint("ck_pipeline_execution_authority_state", "pipeline_runs", type_="check")
    op.drop_column("pipeline_runs", "execution_authority_changed_at")
    op.drop_column("pipeline_runs", "execution_authority_reason")
    op.drop_column("pipeline_runs", "execution_authority_state")
