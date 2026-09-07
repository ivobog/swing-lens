"""Add immutable market calculation cutoff context.

Revision ID: 0068_market_calc_context
Revises: 0067_worker_quiesce
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0068_market_calc_context"
down_revision: str | None = "0067_worker_quiesce"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_calculation_contexts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("pipeline_run_id", sa.BigInteger(), nullable=True),
        sa.Column("upload_run_id", sa.BigInteger(), nullable=True),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exchange_timezone", sa.String(length=64), nullable=False),
        sa.Column("latest_completed_session", sa.Date(), nullable=False),
        sa.Column("daily_bar_ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("calendar_version", sa.String(length=64), nullable=False),
        sa.Column("bar_readiness_version", sa.String(length=64), nullable=False),
        sa.Column("cutoff_reason", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["upload_run_id"], ["upload_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pipeline_run_id", name="uq_market_calculation_context_pipeline"),
    )
    op.create_index(
        "idx_market_calculation_contexts_upload_session",
        "market_calculation_contexts",
        ["upload_run_id", "latest_completed_session"],
    )
    op.create_index(
        "idx_market_calculation_contexts_cutoff", "market_calculation_contexts", ["cutoff_at"]
    )
    for table_name in (
        "technical_scores",
        "market_regime_snapshots",
        "sector_rotation_snapshots",
        "setup_signal_snapshots",
    ):
        op.add_column(table_name, sa.Column("calculation_context_id", sa.BigInteger(), nullable=True))
        op.add_column(
            table_name, sa.Column("calculation_cutoff_at", sa.DateTime(timezone=True), nullable=True)
        )
        op.add_column(table_name, sa.Column("input_as_of_session", sa.Date(), nullable=True))
        op.add_column(table_name, sa.Column("calendar_version", sa.String(length=64), nullable=True))
        op.create_foreign_key(
            f"fk_{table_name}_market_calc_context",
            table_name,
            "market_calculation_contexts",
            ["calculation_context_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index(
            f"idx_{table_name}_temporal_lineage",
            table_name,
            ["input_as_of_session", "calculation_context_id"],
        )

    op.add_column(
        "ceri_price_response_features", sa.Column("feature_as_of_session", sa.Date(), nullable=True)
    )
    op.add_column(
        "ceri_price_response_features", sa.Column("reaction_start_session", sa.Date(), nullable=True)
    )
    op.add_column(
        "ceri_price_response_features", sa.Column("prior_reference_session", sa.Date(), nullable=True)
    )
    op.add_column(
        "ceri_price_response_features",
        sa.Column("window_session_map_json", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "ceri_price_response_features", sa.Column("reaction_policy_version", sa.Text(), nullable=True)
    )
    op.add_column(
        "ceri_derived_features",
        sa.Column("calculation_cutoff_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("ceri_derived_features", sa.Column("calendar_version", sa.Text(), nullable=True))
    op.add_column(
        "ceri_score_snapshots", sa.Column("calculation_context_id", sa.BigInteger(), nullable=True)
    )
    op.add_column("ceri_score_snapshots", sa.Column("calendar_version", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_ceri_score_snapshots_market_calc_context",
        "ceri_score_snapshots",
        "market_calculation_contexts",
        ["calculation_context_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "ib_intelligence_features",
        sa.Column("calculation_cutoff_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("ib_intelligence_features", sa.Column("calendar_version", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("ib_intelligence_features", "calendar_version")
    op.drop_column("ib_intelligence_features", "calculation_cutoff_at")
    op.drop_constraint(
        "fk_ceri_score_snapshots_market_calc_context",
        "ceri_score_snapshots",
        type_="foreignkey",
    )
    op.drop_column("ceri_score_snapshots", "calendar_version")
    op.drop_column("ceri_score_snapshots", "calculation_context_id")
    op.drop_column("ceri_derived_features", "calendar_version")
    op.drop_column("ceri_derived_features", "calculation_cutoff_at")
    for column in (
        "reaction_policy_version",
        "window_session_map_json",
        "prior_reference_session",
        "reaction_start_session",
        "feature_as_of_session",
    ):
        op.drop_column("ceri_price_response_features", column)
    for table_name in reversed(
        (
            "technical_scores",
            "market_regime_snapshots",
            "sector_rotation_snapshots",
            "setup_signal_snapshots",
        )
    ):
        op.drop_index(f"idx_{table_name}_temporal_lineage", table_name=table_name)
        op.drop_constraint(
            f"fk_{table_name}_market_calc_context", table_name, type_="foreignkey"
        )
        for column in (
            "calendar_version",
            "input_as_of_session",
            "calculation_cutoff_at",
            "calculation_context_id",
        ):
            op.drop_column(table_name, column)
    op.drop_index("idx_market_calculation_contexts_cutoff", table_name="market_calculation_contexts")
    op.drop_index(
        "idx_market_calculation_contexts_upload_session", table_name="market_calculation_contexts"
    )
    op.drop_table("market_calculation_contexts")
